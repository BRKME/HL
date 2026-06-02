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
    """Aggregate all signals into a single verdict + one-line rationale.

    Returns (verdict, rationale).
    verdict: 'LONG' | 'SHORT' | 'WAIT'
    rationale: short sentence in Russian explaining the call.

    Scoring logic:
    - Each TA/funding/whale factor contributes 1 to long_score or short_score
    - Regime BEAR/EARLY_BEAR blocks LONG verdict
    - Regime BULL/EARLY_BULL blocks SHORT verdict
    - |long-short| < 2 → WAIT (signals balanced)
    - Verdict only fires when signals are decisive AND regime allows
    """
    long_score = 0
    short_score = 0
    long_reasons: list[str] = []
    short_reasons: list[str] = []
    blocker = ""

    if ta is not None:
        above_e50 = ta.get("above_ema50")
        above_e200 = ta.get("above_ema200")
        rsi = ta.get("rsi_d1")

        # Trend structure: 2 points for full alignment, 1 for partial
        if above_e50 and above_e200:
            long_score += 2
            long_reasons.append("тренд вверх")
        elif above_e200 is False and above_e50 is False:
            short_score += 2
            short_reasons.append("тренд вниз")

        # RSI extremes (contrarian)
        if rsi is not None:
            if rsi <= 30:
                long_score += 1
                long_reasons.append("RSI перепродан")
            elif rsi >= 70:
                short_score += 1
                short_reasons.append("RSI перекуплен")

        # Swing low / high proximity
        swing_low = ta.get("swing_low")
        swing_high = ta.get("swing_high")
        last = ta.get("last")
        if swing_low and last and last > 0:
            if (last - swing_low) / last * 100 <= 3.0:
                long_score += 1
                long_reasons.append("у поддержки")
        if swing_high and last and last > 0:
            if (swing_high - last) / last * 100 <= 3.0:
                short_score += 1
                short_reasons.append("у сопротивления")

    # Funding bias (contrarian: high positive funding → short setup)
    if funding_apr_pct is not None:
        if funding_apr_pct >= 15:
            short_score += 2
            short_reasons.append("дорогой long funding")
        elif funding_apr_pct >= 5:
            short_score += 1
            short_reasons.append("положительный funding")
        elif funding_apr_pct <= -10:
            long_score += 2
            long_reasons.append("дорогой short funding")
        elif funding_apr_pct <= -5:
            long_score += 1
            long_reasons.append("отрицательный funding")

    # Whales: only counts when cluster activity confirms direction
    if whale_cluster_count >= 2:
        if whale_net_long is True:
            long_score += 1
            long_reasons.append("киты long")
        elif whale_net_long is False:
            short_score += 1
            short_reasons.append("киты short")

    # Regime/phase categorisation:
    #
    # bottom_phases: rare 'market is at the lows' signals. Don't block
    # long — actively contribute +2 to long_score (Wyckoff accumulation,
    # capitulation = panic-selling exhaustion, late-bear = downtrend
    # losing steam).
    #
    # top_phases: 'market is at the highs' — same idea inverted.
    #
    # bear_phases / bull_phases: ongoing trend, blocks counter-trend entries.
    bottom_phases = ("CAPITULATION", "ACCUMULATION", "LATE_BEAR")
    top_phases = ("DISTRIBUTION", "EUPHORIA", "LATE_BULL")
    bear_phases = ("EARLY_BEAR", "MID_BEAR")
    bull_phases = ("EARLY_BULL", "MID_BULL", "MARKUP")

    bottom_signal = phase in bottom_phases if phase else False
    top_signal = phase in top_phases if phase else False

    # Bottom signal adds long bias — buying into capitulation/accumulation
    # historically pays. Top signal adds short bias.
    if bottom_signal:
        long_score += 2
        long_reasons.append(f"{phase.lower()} — потенциальное дно")
    if top_signal:
        short_score += 2
        short_reasons.append(f"{phase.lower()} — потенциальная вершина")

    # Blockers — but only when regime is the matching direction AND
    # phase isn't already a bottom/top signal. CAPITULATION technically
    # falls under regime=BEAR but you want to BUY it, not block buys.
    if not bottom_signal and (
        regime == "BEAR" or (phase and phase in bear_phases)
    ):
        blocker = "BEAR"
    elif not top_signal and (
        regime == "BULL" or (phase and phase in bull_phases)
    ):
        blocker = "BULL"

    # Decision tree
    margin = abs(long_score - short_score)

    if margin < 2:
        # Signals balanced — no decisive bias
        if long_score == 0 and short_score == 0:
            return ("WAIT", "Сигналов нет, рынок без направления.")
        return (
            "WAIT",
            f"Сигналы смешанные ({long_score} за long, {short_score} за short). "
            f"Чёткой картины нет."
        )

    if long_score > short_score:
        if blocker == "BEAR":
            return (
                "WAIT",
                f"За long: {', '.join(long_reasons[:3])}. "
                f"Но broad regime BEAR — против тренда не входить."
            )
        return (
            "LONG",
            f"За long: {', '.join(long_reasons[:3])}."
        )
    else:
        if blocker == "BULL":
            return (
                "WAIT",
                f"За short: {', '.join(short_reasons[:3])}. "
                f"Но broad regime BULL — против тренда не входить."
            )
        return (
            "SHORT",
            f"За short: {', '.join(short_reasons[:3])}."
        )


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
