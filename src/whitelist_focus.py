"""Whitelist daily verdicts — one Telegram message with a per-coin
LONG/SHORT/WAIT verdict for the focus coins
(BTC, ETH, ZEC, NEAR, HYPE, ASTER, MORPHO, TAO, ONDO).

Uses compute_verdict_pair from eth_focus so the journal can record both
raw and final verdicts for backtest comparison.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.eth_focus import compute_verdict_pair
from src.ta import compute_indicators


logger = logging.getLogger("whitelist_focus")

# The 6 coins to evaluate. Order = display order in the message.
FOCUS_COINS = ["BTC", "ETH", "ZEC", "NEAR", "HYPE", "ASTER", "MORPHO", "TAO",
               "ONDO"]  # ONDO добавлен 08.08.2026 решением оператора

_MOSCOW = timezone(timedelta(hours=3))


def _e(s: str) -> str:
    return html.escape(str(s))


def _ru_date(dt: datetime) -> str:
    months = ["янв", "фев", "мар", "апр", "мая", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _fmt_price(p: float) -> str:
    """Значащая точность, а не фиксированная.

    До 30.08 всё в диапазоне 1–1000 округлялось до целого: BTC от этого не
    страдал, а NEAR за $1.88 показывался как «$2». При стопе в 1.6% ошибка
    округления больше всего риска сделки — оператор не мог проверить, по
    той ли цене входит. Четыре монеты из девяти были в этом диапазоне.
    """
    if p is None or p == 0:
        return "—"
    p = float(p)
    if p >= 1000:
        return f"{round(p):,}".replace(",", " ")
    if p >= 100:
        out = f"{p:.1f}"
    elif p >= 1:
        out = f"{p:.4g}"
    elif p >= 0.01:
        out = f"{p:.4f}"
    else:
        out = f"{p:.8f}"
    return out.rstrip("0").rstrip(".") if "." in out else out


def _read_recent_whale_fills(state_dir: Path, coin: str, days: int,
                              now: datetime) -> list[dict]:
    """Read whale fills from state for ONE coin over last N days."""
    import json
    path = state_dir / "whale_fills.jsonl"
    if not path.exists():
        return []
    cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    out = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("coin") != coin:
                    continue
                if row.get("time_ms", 0) < cutoff_ms:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _read_recent_whale_signals(state_dir: Path, coin: str, days: int,
                                now: datetime) -> list[dict]:
    """Read whale signals from state for ONE coin over last N days."""
    import json
    path = state_dir / "whale_signals.jsonl"
    if not path.exists():
        return []
    cutoff = now - timedelta(days=days)
    out = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
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


def _whale_state_for_coin(state_dir: Path, coin: str, now: datetime
                           ) -> tuple[int, Optional[bool]]:
    """Return (cluster_count, whale_net_long) for the verdict input."""
    signals = _read_recent_whale_signals(state_dir, coin, days=7, now=now)
    fills = _read_recent_whale_fills(state_dir, coin, days=7, now=now)
    cluster_count = sum(1 for s in signals if s.get("rule") == "WHALE_CLUSTER")
    net_long: Optional[bool] = None
    if fills:
        long_notional = sum(f.get("notional_usd", 0) for f in fills
                            if f.get("direction") == "Open Long")
        short_notional = sum(f.get("notional_usd", 0) for f in fills
                              if f.get("direction") == "Open Short")
        if long_notional > short_notional * 1.2:
            net_long = True
        elif short_notional > long_notional * 1.2:
            net_long = False
    return cluster_count, net_long


def evaluate_coin(
    coin: str,
    mark: float,
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
    now: datetime,
) -> tuple[str, str]:
    """Compute (verdict_final, rationale_final) for one coin.
    Backward-compatible wrapper around evaluate_coin_pair."""
    _, final = evaluate_coin_pair(
        coin=coin, mark=mark, candles_closes=candles_closes,
        funding_apr_pct=funding_apr_pct, regime_snapshot=regime_snapshot,
        state_dir=state_dir, now=now,
    )
    return final


def evaluate_coin_pair(
    coin: str,
    mark: float,
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
    now: datetime,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return ((verdict_raw, rationale_raw), (verdict_final, rationale_final))
    for a coin. Used by the journal to record both versions for backtest
    comparison."""
    if not mark or mark <= 0:
        return (("WAIT", "Нет цены"), ("WAIT", "Нет цены"))

    ta_dict = None
    if candles_closes and len(candles_closes) >= 200:
        candle_dicts = [{"o": c, "h": c, "l": c, "c": c} for c in candles_closes]
        ta_dict = compute_indicators(candle_dicts, swing_lookback=30)

    cluster_count, whale_net_long = _whale_state_for_coin(state_dir, coin, now)

    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)

    from src.eth_focus import compute_verdict_pair
    return compute_verdict_pair(
        ta=ta_dict,
        funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long,
        whale_cluster_count=cluster_count,
        regime=regime,
        phase=phase,
    )


def compute_all_verdicts(
    now: datetime,
    coin_data: dict[str, dict],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> list[tuple[str, float, str, str, str, str]]:
    """Compute raw + final verdicts for all focus coins.

    Returns [(coin, mark, verdict_final, rationale_final,
              verdict_raw, rationale_raw)] in FOCUS_COINS order.
    Coins with no mark get verdict='NODATA' (both raw and final).

    Single source of truth — render_whitelist_verdicts shows final,
    journal stores both for raw-vs-final WR comparison.
    """
    out: list[tuple[str, float, str, str, str, str]] = []
    for coin in FOCUS_COINS:
        data = coin_data.get(coin, {})
        mark = data.get("mark", 0)
        if not mark or mark <= 0:
            out.append((coin, 0.0, "NODATA", "", "NODATA", ""))
            continue
        (raw_v, raw_r), (final_v, final_r) = evaluate_coin_pair(
            coin=coin, mark=mark,
            candles_closes=data.get("candles_closes"),
            funding_apr_pct=data.get("funding_apr_pct"),
            regime_snapshot=regime_snapshot,
            state_dir=state_dir, now=now,
        )
        out.append((coin, mark, final_v, final_r, raw_v, raw_r))
    return out


def _rs_for_digest(coin_data: dict) -> dict:
    """Относительная сила против BTC по каждой монете, 30 дней."""
    try:
        from src.relative_strength import compute_rs
    except Exception:  # noqa: BLE001
        return {}
    btc = (coin_data.get("BTC") or {}).get("candles_closes")
    if not btc:
        return {}
    out = {}
    for coin, data in coin_data.items():
        closes = (data or {}).get("candles_closes")
        if not closes:
            continue
        try:
            out[coin] = compute_rs(closes, btc, 30)
        except Exception:  # noqa: BLE001
            continue
    return out


def _plan_line(verdict: str, entry: float, sl: float, n_entries: int) -> str:
    """Строка «стоп · размер» — или пусто, если план неисполним.

    Две защиты, обе добавлены 30.08 после превью:

    * стоп обязан быть НА СВОЕЙ СТОРОНЕ от входа. Стоп выше входа для лонга
      — не стоп, а мгновенный убыток; печатать оператору неисполнимый план
      опаснее, чем не печатать ничего;
    * размер делится между одновременными входами. Иначе предупреждение
      «дели тактический размер» противоречит числу, стоящему рядом с ним:
      четыре входа по 50% депозита — это 200% в одну сторону.
    """
    if verdict not in ("LONG", "SHORT"):
        return ""
    try:
        entry, sl = float(entry), float(sl)
    except (TypeError, ValueError):
        return ""
    if entry <= 0 or sl <= 0:
        return ""
    if verdict == "LONG" and sl >= entry:
        return ""
    if verdict == "SHORT" and sl <= entry:
        return ""

    from src.leverage import suggest as leverage_suggest

    risk_pct = abs(entry - sl) / entry * 100
    sizing = leverage_suggest("", verdict, None, entry, sl) or {}
    size = sizing.get("size_pct_equity")
    size_txt = ""
    if size:
        size_txt = f" · размер ~{size / max(n_entries, 1):.1f}%"
    return f"↳ стоп {_fmt_price(sl)} ({risk_pct:.1f}%){size_txt}"


def _entry_plan(coin: str, verdict: str, mark: float, data: dict,
                n_entries: int = 1) -> str:
    """Стоп и размер для входа тем же расчётом, что и тактический сигнал."""
    if verdict not in ("LONG", "SHORT") or not mark:
        return ""
    try:
        from src.tactical_signals import sl_for
        from src import ta

        closes = (data or {}).get("candles_closes") or []
        if len(closes) < 30:
            return ""
        cd = [{"o": c, "h": c, "l": c, "c": c} for c in closes]
        sl = sl_for(verdict, mark, ta.atr(cd, 14),
                    min(closes[-30:]), max(closes[-30:]))
        return _plan_line(verdict, mark, sl, n_entries) if sl else ""
    except Exception:  # noqa: BLE001
        return ""


def render_whitelist_verdicts(
    now: datetime,
    coin_data: dict[str, dict],
    regime_snapshot: Optional[dict],
    state_dir: Path,
    show_whale_stance: bool = True,
    include_regime_line: bool = True,
) -> str:
    """One-message report.

    coin_data: {coin: {mark, candles_closes, funding_apr_pct}}.
    Each coin gets one line: emoji COIN price - verdict (reasons).

    show_whale_stance: if True, prepend a '🐋 Киты 7d: ...' line built
    from whale_fills.jsonl. Skipped silently if no fills accumulated yet.

    include_regime_line: подпись «regime X · phase Y». Выключается, когда
    дайджест встраивается в дневной отчёт — тот печатает режим в подвале, и
    в сообщении от 03.08 строка встречалась трижды.
    """
    msk = now.astimezone(_MOSCOW)
    header = (f"🎯 <b>Whitelist daily</b> — {_ru_date(msk)}, "
              f"{msk.strftime('%H:%M')} MSK")
    emoji_map = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
    label_map = {"LONG": "ВХОДИТЬ LONG", "SHORT": "ВХОДИТЬ SHORT", "WAIT": "НЕ ВХОДИТЬ"}

    # Add regime advice headline if available (same as daily-monitor)
    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)
    regime_line = ""
    if regime and phase and include_regime_line:
        regime_line = f"\n<i>regime {_e(regime)} · phase {_e(phase)}</i>"

    lines = [header + regime_line]

    # Whale stance line — derived from whale_fills.jsonl
    if show_whale_stance:
        try:
            from src.whale_stance import compute_stance, format_stance_line
            stances = compute_stance(state_dir, coins=FOCUS_COINS, now=now)
            stance_line = format_stance_line(stances, FOCUS_COINS)
            # Монеты без данных из строки убираются (03.08): «BTC — • NEAR —
            # • HYPE —» несёт нули информации при полной строке текста.
            from src.digest_compact import compact_stance_line
            stance_line = compact_stance_line(stance_line)
            if stance_line:
                lines.append(stance_line)
        except Exception:
            # whale stance is auxiliary — never block the main message
            pass

    lines.append("")  # blank separator before verdicts

    verdicts = compute_all_verdicts(now, coin_data, regime_snapshot, state_dir)

    # Сплошной WAIT схлопывается в одну строку (03.08): восемь одинаковых
    # «НЕ ВХОДИТЬ» занимали 46% сообщения. Появится вход — список
    # развернётся обратно сам.
    from src.digest_compact import (
        collapse_wait_verdicts, collapse_waits_when_entries, rank_entries,
    )
    verdicts, wait_summary = collapse_wait_verdicts(verdicts)

    # Относительная сила по каждой монете — для порядка входов и для того,
    # чтобы оператор видел, на чём этот порядок основан (30.08).
    rs_by_coin = _rs_for_digest(coin_data)
    verdicts = [tuple(v) + (rs_by_coin.get(v[0]),) for v in verdicts]

    # Когда входы есть, «НЕ ВХОДИТЬ» сворачивается в строку-итог, а входы
    # выстраиваются по силе. Отбор сигналов при этом не меняется.
    verdicts, waits_line = collapse_waits_when_entries(verdicts)
    verdicts = rank_entries(verdicts)

    # Новизна: «🆕» или «N-й день». Без неё оператору приходилось держать
    # вчерашнее письмо в голове, чтобы понять, новый это сигнал или тот же.
    try:
        from src.digest_history import load_prev, mark_novelty, save_prev
        _marks, _new_state = mark_novelty(
            verdicts, load_prev(state_dir), now.date())
        save_prev(state_dir, _new_state)
    except Exception:  # noqa: BLE001
        _marks = {}

    _n_entries = sum(1 for v in verdicts if v[2] in ("LONG", "SHORT"))

    for coin, mark, verdict, rationale, _raw_v, _raw_r, _rs in verdicts:
        if verdict == "NODATA":
            lines.append(f"⚫ <code>{_e(coin)}</code> — нет данных")
            continue

        emoji = emoji_map.get(verdict, "⚪")
        label = label_map.get(verdict, "НЕ ВХОДИТЬ")

        # Compact: parenthesised rationale, trimmed to keep one line short
        short_rat = rationale
        if len(short_rat) > 90:
            short_rat = short_rat[:87].rstrip() + "…"

        # По входу сразу даём стоп и размер: без них письмо неисполнимо,
        # оператору приходилось ждать отдельного тактического сигнала.
        plan = _entry_plan(coin, verdict, mark, coin_data.get(coin) or {},
                           n_entries=_n_entries)
        rs_note = f" · RS {_rs:+.0f}" if (_rs is not None and
                                          verdict in ("LONG", "SHORT")) else ""
        novelty = _marks.get(coin)
        novelty_note = f" · {novelty}" if novelty else ""

        lines.append(
            f"{emoji} <code>{_e(coin)}</code> ${_fmt_price(mark)}{rs_note} — "
            f"<b>{label}</b>{novelty_note}  <i>({short_rat})</i>"
        )
        if plan:
            lines.append(f"    {plan}")

    if waits_line:
        lines.append("")
        lines.append(waits_line)
    if wait_summary:
        lines.append(wait_summary)

    from src.digest_compact import beta_warning
    warn = beta_warning(verdicts)
    if warn:
        lines.append("")
        lines.append(warn)

    return "\n".join(lines)
