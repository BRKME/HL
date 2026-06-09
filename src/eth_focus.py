"""ETH Saturday Focus — separate weekly report on ETH market state.

Runs Saturday 10:30 MSK (30 min after the regular weekly scan, to avoid
overlap). Pulls ETH-specific TA, funding/OI, whale activity from past 7 days,
and OracAI broad-market regime, then produces a single descriptive Telegram
message.

Design choices:
- Does NOT trade or write decisions.jsonl — informational only
- No prescriptive entry levels (per Phase 3.4 decision: descriptive only)
- Each section is optional — if data is missing, section is skipped
- Total message <4096 chars (Telegram limit)
- OracAI regime is shown WITH a caveat that it's broad market, not ETH-specific
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.ta import compute_indicators


logger = logging.getLogger("eth_focus")

_MOSCOW = timezone(timedelta(hours=3))
_TG_LIMIT = 4096
_WHALE_LOOKBACK_DAYS = 7

_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


# ----------------------------------------------------- formatting helpers

def _e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _ru_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month - 1]} {dt.year}"


def _fmt_price(p: Optional[float]) -> str:
    if p is None or p == 0:
        return "—"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 10:
        return f"{p:.2f}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0").rstrip(".")


def _fmt_pct(p: float, sign: bool = True) -> str:
    return f"{p:+.1f}%" if sign else f"{p:.1f}%"


def _fmt_money_compact(v: float) -> str:
    """Compact USD: 1.2B / 350M / 15k / $80."""
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1_000_000_000:
        return f"{sign}${abs_v / 1_000_000_000:.1f}B"
    if abs_v >= 1_000_000:
        return f"{sign}${abs_v / 1_000_000:.0f}M"
    if abs_v >= 1_000:
        return f"{sign}${abs_v / 1_000:.0f}k"
    return f"{sign}${abs_v:.0f}"


# ---------------------------------------------------- section: header

def _section_header(
    mark: float,
    prev_day: float,
    candles: list[float],
    now: datetime,
) -> Optional[str]:
    """Mark, 24h change, week change, month change."""
    if not mark or mark <= 0:
        return None

    parts = [f"$<b>{_fmt_price(mark)}</b>"]

    if prev_day and prev_day > 0:
        d24 = (mark - prev_day) / prev_day * 100
        parts.append(f"24h {_fmt_pct(d24)}")

    if candles and len(candles) >= 8:
        week_ago = candles[-8] if len(candles) >= 8 else None
        if week_ago:
            week_pct = (mark - week_ago) / week_ago * 100
            parts.append(f"7d {_fmt_pct(week_pct)}")

    if candles and len(candles) >= 31:
        month_ago = candles[-31]
        if month_ago:
            month_pct = (mark - month_ago) / month_ago * 100
            parts.append(f"30d {_fmt_pct(month_pct)}")

    return "  •  ".join(parts)


# ---------------------------------------------------- section: TA

def _section_ta(closes: list[float], now: datetime) -> Optional[str]:
    """Technical indicators block. Needs >= 200 candles for EMA200."""
    if not closes or len(closes) < 200:
        return None

    # compute_indicators expects candle dicts; we have only closes,
    # so build minimal candles (h=l=c=o) for it
    candles = [{"o": c, "h": c, "l": c, "c": c} for c in closes]
    ind = compute_indicators(candles, swing_lookback=30)

    if ind.get("ema200") is None:
        return None

    lines = ["", "<b>📈 Технический анализ (D1)</b>"]

    rsi = ind.get("rsi_d1")
    e50 = ind.get("ema50")
    e200 = ind.get("ema200")
    vs50 = ind.get("vs_ema50_pct")
    vs200 = ind.get("vs_ema200_pct")
    atr = ind.get("atr14")
    last = ind.get("last")

    bits = []
    if rsi is not None:
        bits.append(f"RSI {rsi:.0f}")
    if e50:
        bits.append(f"EMA50 ${_fmt_price(e50)} ({_fmt_pct(vs50)})")
    if e200:
        bits.append(f"EMA200 ${_fmt_price(e200)} ({_fmt_pct(vs200)})")
    if bits:
        lines.append("  " + " • ".join(bits))

    # Trend description
    above_e50 = ind.get("above_ema50")
    above_e200 = ind.get("above_ema200")
    if above_e50 and above_e200:
        trend = "восходящий тренд — выше обеих EMA"
    elif above_e200 and not above_e50:
        trend = "коррекция в восходящем тренде (выше EMA200, ниже EMA50)"
    elif not above_e200 and above_e50:
        trend = "отскок в нисходящем тренде (выше EMA50, ниже EMA200)"
    else:
        trend = "нисходящий тренд — ниже обеих EMA"
    lines.append(f"  Тренд: {trend}")

    if atr and last:
        atr_pct = atr / last * 100
        lines.append(f"  ATR(14) ${_fmt_price(atr)} ({_fmt_pct(atr_pct, sign=False)})")

    swing = ind.get("swing_low")
    if swing and last:
        swing_dist = (swing - last) / last * 100
        lines.append(f"  Swing low 30d ${_fmt_price(swing)} ({_fmt_pct(swing_dist)})")

    return "\n".join(lines)


# ---------------------------------------------------- section: funding & OI

def _section_funding_oi(
    funding_apr_pct: Optional[float],
    open_interest_usd: Optional[float],
) -> Optional[str]:
    if funding_apr_pct is None and open_interest_usd is None:
        return None

    lines = ["", "<b>💰 Funding &amp; OI</b>"]
    if funding_apr_pct is not None:
        if funding_apr_pct > 0:
            who = "long платят short — bias к коррекции/short setup усилен"
        elif funding_apr_pct < 0:
            who = "short платят long — bias к отскоку/long setup усилен"
        else:
            who = "нейтрально"
        lines.append(f"  Funding {funding_apr_pct:+.1f}% APR — {who}")
    if open_interest_usd is not None and open_interest_usd > 0:
        lines.append(f"  OI {_fmt_money_compact(open_interest_usd)}")
    return "\n".join(lines)


# ---------------------------------------------------- section: whale activity

def _read_recent_whale_signals(
    state_dir: Path,
    coin: str,
    days: int,
    now: datetime,
) -> list[dict]:
    """Read signals from state/whale_signals.jsonl, filter by coin + age."""
    path = Path(state_dir) / "whale_signals.jsonl"
    if not path.exists():
        return []
    cutoff = now - timedelta(days=days)
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("coin") != coin:
                    continue
                ts_str = row.get("run_ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _read_recent_whale_fills(
    state_dir: Path,
    coin: str,
    days: int,
    now: datetime,
) -> list[dict]:
    """Read fills from state/whale_fills.jsonl, filter by coin + age."""
    path = Path(state_dir) / "whale_fills.jsonl"
    if not path.exists():
        return []
    cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("coin") != coin:
                    continue
                t = row.get("time_ms", 0)
                if t < cutoff_ms:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _section_whales(
    state_dir: Path,
    focus_coin: str,
    now: datetime,
) -> Optional[str]:
    """Summarise whale signals + fills on the focus coin over past 7d."""
    signals = _read_recent_whale_signals(state_dir, coin=focus_coin,
                                          days=_WHALE_LOOKBACK_DAYS, now=now)
    fills = _read_recent_whale_fills(state_dir, coin=focus_coin,
                                      days=_WHALE_LOOKBACK_DAYS, now=now)

    if not signals and not fills:
        return None

    lines = ["", f"<b>🐋 Whale activity ({_WHALE_LOOKBACK_DAYS}d)</b>"]

    # Cluster event counts: long vs short
    cluster_long = sum(
        1 for s in signals
        if s.get("rule") == "WHALE_CLUSTER"
        and s.get("details", {}).get("direction") == "long"
    )
    cluster_short = sum(
        1 for s in signals
        if s.get("rule") == "WHALE_CLUSTER"
        and s.get("details", {}).get("direction") == "short"
    )
    total_clusters = cluster_long + cluster_short
    if total_clusters > 0:
        lines.append(
            f"  {total_clusters} cluster event{'s' if total_clusters != 1 else ''}: "
            f"{cluster_long} long, {cluster_short} short"
        )

    # FLIP events
    flips = [s for s in signals if s.get("rule") == "WHALE_FLIP"]
    if flips:
        lines.append(f"  🔄 {len(flips)} flip event{'s' if len(flips) != 1 else ''}")

    # Net flow from fills (Open events only)
    long_notional = sum(
        f.get("notional_usd", 0)
        for f in fills
        if f.get("direction") == "Open Long"
    )
    short_notional = sum(
        f.get("notional_usd", 0)
        for f in fills
        if f.get("direction") == "Open Short"
    )
    if long_notional or short_notional:
        net = long_notional - short_notional
        net_label = "long" if net > 0 else "short" if net < 0 else "neutral"
        lines.append(
            f"  Net flow: {_fmt_money_compact(net)} {net_label} "
            f"(L {_fmt_money_compact(long_notional)} / "
            f"S {_fmt_money_compact(short_notional)})"
        )

    # If we only have signals (no fills), still emit something
    if len(lines) == 2:
        # only the header — promote at least one signal line
        new_opens = [s for s in signals if s.get("rule") == "WHALE_NEW_OPEN"]
        if new_opens:
            lines.append(f"  {len(new_opens)} новый whale entry")
        else:
            return None

    return "\n".join(lines)


# ---------------------------------------------------- section: regime

def _section_regime(snapshot: Optional[dict]) -> Optional[str]:
    if not snapshot:
        return None
    regime = snapshot.get("regime")
    phase = (snapshot.get("cycle") or {}).get("phase")
    if not regime and not phase:
        return None
    lines = ["", "<b>📊 Market regime</b>"]
    bits = []
    if regime:
        bits.append(f"regime <b>{_e(regime)}</b>")
    if phase:
        bits.append(f"phase <b>{_e(phase)}</b>")
    lines.append("  " + " · ".join(bits))
    lines.append("  <i>(broad market — не специфика ETH)</i>")
    return "\n".join(lines)


# ---------------------------------------------------- section: setup summary

def _section_setup(
    ta: Optional[dict],
    funding_apr_pct: Optional[float],
    whale_cluster_count: int,
    whale_net_long: Optional[bool],
    regime: Optional[str],
    phase: Optional[str] = None,
) -> Optional[str]:
    """Descriptive summary — what's the current setup on ETH.

    Strict rule: NO entry/SL prices, NO 'buy at $X', NO prescriptive sizing.
    Only describes observable state in plain language.
    """
    bullets: list[str] = []

    if ta is not None:
        above_e50 = ta.get("above_ema50")
        above_e200 = ta.get("above_ema200")
        rsi = ta.get("rsi_d1")
        if above_e50 and above_e200:
            bullets.append("Цена выше EMA50 и EMA200 — структура восходящая")
        elif above_e200 and above_e50 is False:
            bullets.append("Цена выше EMA200 но ниже EMA50 — коррекция в тренде")
        elif above_e200 is False and above_e50:
            bullets.append("Цена выше EMA50 но ниже EMA200 — отскок в нисходящем тренде")
        elif above_e200 is False and above_e50 is False:
            bullets.append("Цена ниже EMA50 и EMA200 — структура нисходящая")

        if rsi is not None:
            if rsi >= 70:
                bullets.append(f"RSI {rsi:.0f} — зона перекупленности")
            elif rsi <= 30:
                bullets.append(f"RSI {rsi:.0f} — зона перепроданности")
            elif rsi > 60:
                bullets.append(f"RSI {rsi:.0f} — повышенная сила покупателя")
            elif rsi < 40:
                bullets.append(f"RSI {rsi:.0f} — повышенное давление продавца")

        # Swing-low proximity: support nearby is a structurally important fact
        swing_low = ta.get("swing_low")
        last = ta.get("last")
        if swing_low and last and last > 0:
            swing_dist_pct = (last - swing_low) / last * 100
            if 0 <= swing_dist_pct <= 5.0:
                bullets.append(
                    f"Цена в {swing_dist_pct:.1f}% от swing low 30d "
                    f"(${_fmt_price(swing_low)}) — поддержка рядом"
                )

    if funding_apr_pct is not None:
        # Lowered thresholds to ±5% so moderate funding bias is surfaced
        if funding_apr_pct >= 15:
            bullets.append(
                f"Funding {funding_apr_pct:+.0f}% APR — лонги дорогие, "
                f"short setup финансово выгоден"
            )
        elif funding_apr_pct >= 5:
            bullets.append(
                f"Funding {funding_apr_pct:+.1f}% APR — long платят short, "
                f"умеренный bias к коррекции"
            )
        elif funding_apr_pct <= -10:
            bullets.append(
                f"Funding {funding_apr_pct:+.0f}% APR — шорты дорогие, "
                f"long setup финансово выгоден"
            )
        elif funding_apr_pct <= -5:
            bullets.append(
                f"Funding {funding_apr_pct:+.1f}% APR — short платят long, "
                f"умеренный bias к отскоку"
            )

    if whale_cluster_count >= 2:
        direction_hint = ""
        if whale_net_long is True:
            direction_hint = " (нетто long)"
        elif whale_net_long is False:
            direction_hint = " (нетто short)"
        bullets.append(
            f"{whale_cluster_count} cluster event{'s' if whale_cluster_count != 1 else ''} "
            f"за неделю{direction_hint} — повышенный интерес китов"
        )

    if regime and regime in ("BULL", "BEAR"):
        regime_bit = f"Broad market regime: {regime}"
        if phase:
            regime_bit += f" · phase {phase}"
        bullets.append(regime_bit)

    if not bullets:
        return None

    lines = ["", "<b>💭 Setup</b>"]
    for b in bullets:
        lines.append(f"  • {b}")
    return "\n".join(lines)


# ---------- Verdict computation (Variant B short-form report) ----------

def _compute_verdict(
    ta: Optional[dict],
    funding_apr_pct: Optional[float],
    whale_net_long: Optional[bool],
    whale_cluster_count: int,
    regime: Optional[str],
    phase: Optional[str] = None,
) -> tuple[str, str]:
    """Aggregate signals into a verdict + rationale.

    Returns (verdict_final, rationale).

    Methodology (after analyst review June 9):
    - trend_score: pure direction signal (EMA50/EMA200 alignment).
      -2 (full bearish), -1 (partial), 0, +1 (partial), +2 (full bullish).
    - exhaustion: a separate flag indicating market is overheated/oversold.
      Computed from RSI extremes, extreme funding, swing-edge proximity.
      Exhaustion does NOT vote on direction — it modifies certainty.
      Counter-trend exhaustion (overheated in uptrend) DOWNGRADES verdict.
    - Reversal phases (CAPITULATION, EUPHORIA) flip the read: oversold +
      capitulation = enter long; overheated + euphoria = enter short.
    - Whale signals: contribute 0 weight until journal validates them
      (analyst feedback: whale data may not represent net bias).
    - Regime: still blocks counter-trend entries (BEAR blocks LONG etc.)
      but bottom/top phases override blocker.

    For internal use, also computable: verdict without regime ('raw').
    See compute_verdict_pair() for that.
    """
    return _compute_verdict_full(
        ta=ta, funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long, whale_cluster_count=whale_cluster_count,
        regime=regime, phase=phase,
    )[1]  # return (verdict, rationale) — final, regime-applied


def compute_verdict_pair(
    ta: Optional[dict],
    funding_apr_pct: Optional[float],
    whale_net_long: Optional[bool],
    whale_cluster_count: int,
    regime: Optional[str],
    phase: Optional[str] = None,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return BOTH verdicts: (raw, final).

    raw = trend + exhaustion ONLY (no regime / phase consideration)
    final = raw modified by regime/phase blockers and reversal bonuses

    This pair feeds the journal so we can later answer:
    'does adding OracAI regime improve win rate, or hurt it?'
    """
    raw_verdict, final_verdict = _compute_verdict_full(
        ta=ta, funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long, whale_cluster_count=whale_cluster_count,
        regime=regime, phase=phase,
    )
    return raw_verdict, final_verdict


def _compute_verdict_full(
    ta: Optional[dict],
    funding_apr_pct: Optional[float],
    whale_net_long: Optional[bool],
    whale_cluster_count: int,
    regime: Optional[str],
    phase: Optional[str] = None,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Internal: compute both raw and final verdicts in one pass.

    Returns ((verdict_raw, rationale_raw), (verdict_final, rationale_final)).
    """
    # --- Step 1: Trend (pure direction) ---
    trend_score = 0
    trend_reason = ""
    if ta is not None:
        above_e50 = ta.get("above_ema50")
        above_e200 = ta.get("above_ema200")
        if above_e50 and above_e200:
            trend_score = 2
            trend_reason = "тренд вверх"
        elif above_e200 is False and above_e50 is False:
            trend_score = -2
            trend_reason = "тренд вниз"
        elif above_e200 and above_e50 is False:
            trend_score = -1
            trend_reason = "коррекция в восходящем тренде"
        elif above_e200 is False and above_e50:
            trend_score = 1
            trend_reason = "отскок в нисходящем тренде"

    # --- Step 2: Exhaustion (overheated/oversold flags) ---
    # Two separate flags — they're contrarian, not direction signals
    overheated = False
    oversold = False
    exh_reasons: list[str] = []

    if ta is not None:
        rsi = ta.get("rsi_d1")
        if rsi is not None:
            if rsi >= 70:
                overheated = True
                exh_reasons.append(f"RSI {rsi:.0f}")
            elif rsi <= 30:
                oversold = True
                exh_reasons.append(f"RSI {rsi:.0f}")

        # Swing-edge proximity
        swing_low = ta.get("swing_low")
        swing_high = ta.get("swing_high")
        last = ta.get("last")
        if swing_low and last and last > 0:
            if (last - swing_low) / last * 100 <= 3.0:
                oversold = True
                exh_reasons.append("у swing low")
        if swing_high and last and last > 0:
            if (swing_high - last) / last * 100 <= 3.0:
                overheated = True
                exh_reasons.append("у swing high")

    if funding_apr_pct is not None:
        if funding_apr_pct >= 15:
            overheated = True
            exh_reasons.append(f"funding {funding_apr_pct:+.0f}%")
        elif funding_apr_pct <= -10:
            oversold = True
            exh_reasons.append(f"funding {funding_apr_pct:+.0f}%")

    # --- Step 3: RAW verdict (trend + exhaustion only, no regime) ---
    raw_verdict, raw_rationale = _raw_decision(
        trend_score, trend_reason, overheated, oversold, exh_reasons,
    )

    # --- Step 4: Apply regime/phase to get FINAL verdict ---
    final_verdict, final_rationale = _apply_regime(
        raw_verdict, raw_rationale, trend_score, oversold, overheated,
        exh_reasons, regime, phase,
    )

    return (raw_verdict, raw_rationale), (final_verdict, final_rationale)


def _raw_decision(
    trend_score: int, trend_reason: str,
    overheated: bool, oversold: bool, exh_reasons: list[str],
) -> tuple[str, str]:
    """Direction from trend, downgraded if counter-trend exhaustion is present.

    - Strong trend (|trend_score| == 2):
      * counter-trend exhaustion (overheated in uptrend, oversold in downtrend)
        → WAIT, "тренд X но Y exhaustion — ждать"
      * otherwise → follow trend
    - Weak trend (|trend_score| == 1):
      * acts as WAIT unless exhaustion supports the trend direction
    - No trend (0):
      * WAIT
    """
    exh_text = ", ".join(exh_reasons) if exh_reasons else ""

    if trend_score >= 2:
        # Bullish trend
        if overheated:
            return ("WAIT",
                    f"{trend_reason}, но overbought ({exh_text}) — ждать pullback.")
        return ("LONG", f"{trend_reason}.")
    if trend_score <= -2:
        if oversold:
            return ("WAIT",
                    f"{trend_reason}, но oversold ({exh_text}) — ждать отскок.")
        return ("SHORT", f"{trend_reason}.")
    if trend_score == 1:
        # Partial bull (bounce in downtrend) — weak, generally WAIT unless
        # supported by oversold (extreme bounce setup)
        if oversold and not overheated:
            return ("LONG", f"{trend_reason} + oversold ({exh_text}).")
        return ("WAIT", f"{trend_reason} — слабый сигнал.")
    if trend_score == -1:
        if overheated and not oversold:
            return ("SHORT", f"{trend_reason} + overbought ({exh_text}).")
        return ("WAIT", f"{trend_reason} — слабый сигнал.")
    # No trend
    return ("WAIT", "Тренд не определён.")


def _apply_regime(
    raw_verdict: str, raw_rationale: str,
    trend_score: int, oversold: bool, overheated: bool,
    exh_reasons: list[str],
    regime: Optional[str], phase: Optional[str],
) -> tuple[str, str]:
    """Layer regime/phase on top of the raw verdict.

    Reversal phases (CAPITULATION/ACCUMULATION at bottom, EUPHORIA/
    DISTRIBUTION at top) ENABLE contrarian entries even when raw said
    WAIT or trend says opposite.

    Ongoing trend phases (EARLY_BEAR, MID_BEAR for bear; EARLY_BULL,
    MID_BULL, MARKUP for bull) BLOCK counter-trend entries.
    """
    bottom_phases = ("CAPITULATION", "ACCUMULATION", "LATE_BEAR")
    top_phases = ("DISTRIBUTION", "EUPHORIA", "LATE_BULL")
    bear_phases = ("EARLY_BEAR", "MID_BEAR")
    bull_phases = ("EARLY_BULL", "MID_BULL", "MARKUP")

    is_bottom = phase in bottom_phases if phase else False
    is_top = phase in top_phases if phase else False
    is_bear = (regime == "BEAR" or (phase and phase in bear_phases))
    is_bull = (regime == "BULL" or (phase and phase in bull_phases))

    exh_text = ", ".join(exh_reasons) if exh_reasons else ""

    # Bottom phases: oversold + capitulation = LONG opportunity even if
    # raw said WAIT or SHORT
    if is_bottom and oversold:
        phase_name = phase.lower() if phase else "bottom"
        return ("LONG",
                f"{phase_name} + oversold ({exh_text}) — потенциальное дно.")

    # Top phases: overheated + euphoria = SHORT opportunity
    if is_top and overheated:
        phase_name = phase.lower() if phase else "top"
        return ("SHORT",
                f"{phase_name} + overbought ({exh_text}) — потенциальная вершина.")

    # Trend phases as blockers
    if raw_verdict == "LONG" and is_bear and not is_bottom:
        return ("WAIT",
                f"{raw_rationale.rstrip('.')} Но broad regime BEAR — против тренда не входить.")
    if raw_verdict == "SHORT" and is_bull and not is_top:
        return ("WAIT",
                f"{raw_rationale.rstrip('.')} Но broad regime BULL — против тренда не входить.")

    # Otherwise raw stands
    return (raw_verdict, raw_rationale)



def _render_verdict_report(
    now: datetime,
    mark: float,
    verdict: str,
    rationale: str,
) -> str:
    """Variant B: short-form report. Verdict + 1-2 line rationale only."""
    msk = now.astimezone(_MOSCOW)
    label_map = {
        "LONG":  "ВХОДИТЬ LONG",
        "SHORT": "ВХОДИТЬ SHORT",
        "WAIT":  "НЕ ВХОДИТЬ",
    }
    emoji_map = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
    label = label_map.get(verdict, "НЕ ВХОДИТЬ")
    emoji = emoji_map.get(verdict, "⚪")
    return (
        f"🎯 <b>ETH</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK • "
        f"${_fmt_price(mark)}\n"
        f"\n"
        f"{emoji} <b>{label}</b>\n"
        f"{rationale}"
    )


# ---------------------------------------------------- coordinator

def build_eth_focus_report(
    now: datetime,
    mark: float,
    prev_day_mark: Optional[float],
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    open_interest_usd: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> Optional[str]:
    """Variant B (short verdict): aggregate all signals into a single
    LONG/SHORT/WAIT call with a one-line rationale.

    The verbose multi-section report (TA, funding, whales, regime, setup)
    was confusing — gave data but not a decision. This version answers
    'войти или нет' directly. Logic in _compute_verdict above.

    open_interest_usd and prev_day_mark kept in signature for backward
    compat but no longer rendered — verdict considers them via funding
    bias instead.
    """
    if not mark or mark <= 0:
        return None

    # Compute indicators if we have candles
    ta_dict = None
    if candles_closes and len(candles_closes) >= 200:
        candle_dicts = [{"o": c, "h": c, "l": c, "c": c} for c in candles_closes]
        ta_dict = compute_indicators(candle_dicts, swing_lookback=30)

    # Whale signals/fills for the past lookback window
    signals = _read_recent_whale_signals(state_dir, "ETH",
                                          _WHALE_LOOKBACK_DAYS, now)
    fills = _read_recent_whale_fills(state_dir, "ETH",
                                      _WHALE_LOOKBACK_DAYS, now)
    cluster_count = sum(1 for s in signals if s.get("rule") == "WHALE_CLUSTER")
    whale_net_long: Optional[bool] = None
    if fills:
        long_notional = sum(f.get("notional_usd", 0) for f in fills
                            if f.get("direction") == "Open Long")
        short_notional = sum(f.get("notional_usd", 0) for f in fills
                              if f.get("direction") == "Open Short")
        if long_notional > short_notional * 1.2:
            whale_net_long = True
        elif short_notional > long_notional * 1.2:
            whale_net_long = False

    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)

    verdict, rationale = _compute_verdict(
        ta=ta_dict,
        funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long,
        whale_cluster_count=cluster_count,
        regime=regime,
        phase=phase,
    )

    return _render_verdict_report(now, mark, verdict, rationale)


def compute_eth_verdict(
    now: datetime,
    mark: float,
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> tuple[str, str]:
    """Return (verdict, rationale) for ETH without rendering — used by
    the verdict journal so the recorded verdict matches what the report
    shows. Side-effect-free (just reads state)."""
    if not mark or mark <= 0:
        return ("NODATA", "")

    ta_dict = None
    if candles_closes and len(candles_closes) >= 200:
        candle_dicts = [{"o": c, "h": c, "l": c, "c": c} for c in candles_closes]
        ta_dict = compute_indicators(candle_dicts, swing_lookback=30)

    signals = _read_recent_whale_signals(state_dir, "ETH",
                                          _WHALE_LOOKBACK_DAYS, now)
    fills = _read_recent_whale_fills(state_dir, "ETH",
                                      _WHALE_LOOKBACK_DAYS, now)
    cluster_count = sum(1 for s in signals if s.get("rule") == "WHALE_CLUSTER")
    whale_net_long: Optional[bool] = None
    if fills:
        long_notional = sum(f.get("notional_usd", 0) for f in fills
                            if f.get("direction") == "Open Long")
        short_notional = sum(f.get("notional_usd", 0) for f in fills
                              if f.get("direction") == "Open Short")
        if long_notional > short_notional * 1.2:
            whale_net_long = True
        elif short_notional > long_notional * 1.2:
            whale_net_long = False

    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)

    return _compute_verdict(
        ta=ta_dict, funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long, whale_cluster_count=cluster_count,
        regime=regime, phase=phase,
    )


def render_eth_focus(
    now: datetime,
    mark: float,
    prev_day_mark: Optional[float],
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    open_interest_usd: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> Optional[str]:
    """Public entry — returns one Telegram-ready HTML message or None."""
    return build_eth_focus_report(
        now=now, mark=mark, prev_day_mark=prev_day_mark,
        candles_closes=candles_closes,
        funding_apr_pct=funding_apr_pct,
        open_interest_usd=open_interest_usd,
        regime_snapshot=regime_snapshot,
        state_dir=state_dir,
    )
