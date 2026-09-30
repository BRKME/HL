"""Telegram renderer for whale signals.

Two modes, each producing a single HTML message:
- instant: warn/critical signals — sent immediately after each whale-monitor run
- digest:  info signals accumulated over ~24h — sent once a day

Both modes keep the message under Telegram's 4096-char limit, escape HTML,
and use markers consistent with daily_monitor (🐋 for whale-related lines).
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.whale_correlation import (
    Signal,
    SIG_CLUSTER, SIG_OVERLAP, SIG_NEW_OPEN, SIG_FLIP,
    SEV_INFO, SEV_WARN, SEV_CRITICAL,
    fmt_wr,
)


_MOSCOW = timezone(timedelta(hours=3))
_TG_LIMIT = 4096
_MAX_LINES_PER_SECTION = 15

_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _short_whale(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr or "?"
    return addr[:8] + "…"


def _ru_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month - 1]}"


def _fmt_money(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


# --------------------------------------------------------------- routing

# Ночное окно тишины, МСК. Мгновенный алерт ночью оператор всё равно не
# исполнит — он либо срочный, либо не нужен вовсе, а разбудить может.
# Копим и отдаём утренним дайджестом (14.09, письмо в 01:04 про одного
# кита). Границы по московскому времени, потому что живёт он по нему.
QUIET_START_MSK = 23
QUIET_END_MSK = 7


def in_quiet_hours(now) -> bool:
    """Ночь ли сейчас по Москве. Окно переходит через полночь."""
    from datetime import timedelta

    msk = (now + timedelta(hours=3)).hour
    return msk >= QUIET_START_MSK or msk < QUIET_END_MSK


def split_by_mode(signals: list[Signal], now=None
                  ) -> tuple[list[Signal], list[Signal]]:
    """Return (instant, digest). warn+ are instant; info are digest.

    Ночью мгновенных алертов нет вовсе: всё уходит в дайджест, который
    отправится утром.
    """
    if now is not None and in_quiet_hours(now):
        return [], list(signals)
    instant: list[Signal] = []
    digest: list[Signal] = []
    for s in signals:
        if s.severity >= SEV_WARN:
            instant.append(s)
        else:
            digest.append(s)
    return instant, digest


# --------------------------------------------------------------- instant

def _format_cluster(s: Signal) -> str:
    d = s.details
    coin = _e(s.coin)
    side = (d.get("direction") or "").upper()
    n = d.get("whale_count", 0)
    marker = "🎯⚡" if d.get("focus") else "⚡"
    return f"{marker} <b>CLUSTER {coin}</b> {side} — {n} китов"


def _format_flip(s: Signal) -> str:
    d = s.details
    coin = _e(s.coin)
    frm = (d.get("from_side") or "").upper()
    to = (d.get("to_side") or "").upper()
    whale = _e(_short_whale(d.get("whale", "")))
    notional = d.get("notional_usd")
    notional_str = f" • {_fmt_money(float(notional))}" if notional else ""
    marker = "🎯🔄" if d.get("focus") else "🔄"
    return f"{marker} <b>FLIP {coin}</b> {frm} → {to} • <code>{whale}</code>{notional_str}"


def _format_focus_new_open(s: Signal) -> str:
    """Phase 3.2 new path: focus NEW_OPEN is warn-severity, gets its own line."""
    d = s.details
    coin = _e(s.coin)
    direction = (d.get("direction") or "").upper()
    whale = _e(_short_whale(d.get("whale", "")))
    notional = d.get("notional_usd", 0)
    wr = d.get("winrate_used", 0)
    return (f"🎯 <b>NEW_OPEN {coin}</b> {direction} • "
            f"<code>{whale}</code> • {_fmt_money(notional)} "
            f"({fmt_wr(wr, d.get('closures_used'))})")


def render_instant_alerts(signals: list[Signal], now: datetime) -> Optional[str]:
    """Build the immediate alert message for warn/critical signals.

    Returns None when there's nothing to send.
    """
    if not signals:
        return None

    msk = now.astimezone(_MOSCOW)
    lines = [f"🐋 <b>Whale watch</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK"]

    # sort: highest severity first, stable inside tier
    ordered = sorted(signals, key=lambda s: -s.severity)
    for s in ordered:
        if s.rule == SIG_CLUSTER:
            lines.append(_format_cluster(s))
        elif s.rule == SIG_FLIP:
            lines.append(_format_flip(s))
        elif s.rule == SIG_NEW_OPEN:
            lines.append(_format_focus_new_open(s))
        else:
            # any other warn-level rule — fallback to message field
            lines.append(f"• {_e(s.message)}")

    msg = "\n".join(lines)
    if len(msg) > _TG_LIMIT:
        # extreme defensive cap; in practice instant lists are tiny
        msg = msg[: _TG_LIMIT - 20] + "\n… (truncated)"
    return msg


# --------------------------------------------------------------- digest

def _observation_key(s: Signal) -> tuple:
    """Одно наблюдение — кит + монета + сторона (§5 политики, 29.09).

    Монитор пишет сигнал на каждый крупный филл, и «BTC ×173» было одной
    позицией одного кита. Совпадение с позицией и вход того же кита по той
    же монете и стороне — тоже одно решение. День в ключ не входит: окно
    дайджеста — сутки, и полночь внутри окна разрезала бы одну позицию на
    два наблюдения. Кластер — одно наблюдение на монету и сторону. Сигнал
    без кита не склеивается ни с чем: неизвестное происхождение не
    объединяется.
    """
    d = s.details or {}
    side = (d.get("whale_side") or d.get("direction")
            or d.get("to_side") or "").lower()
    if s.rule == SIG_CLUSTER:
        return ("cluster", s.coin, side)
    whale = d.get("whale")
    if not whale:
        return ("single", id(s))
    return ("whale", whale, s.coin, side)


def _dedupe_observations(signals: list[Signal]) -> list[Signal]:
    """Первая копия каждого наблюдения, порядок сохраняется."""
    seen: set[tuple] = set()
    out: list[Signal] = []
    for s in signals:
        k = _observation_key(s)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _digest_overlap_section(signals: list[Signal]) -> Optional[str]:
    if not signals:
        return None
    # dedup by (coin, whale): one line per pair, with count
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for s in signals:
        whale = s.details.get("whale", "")
        grouped[(s.coin, whale)].append(s)

    # sort sections by count descending, then by max winrate
    # Копий одного кита больше не считаем (29.09): «×173» было числом
    # филлов одной позиции, а не силой сигнала.
    ranked = sorted(
        grouped.items(),
        key=lambda kv: -max((x.details.get("winrate_used", 0) for x in kv[1]), default=0),
    )
    lines = ["", "<b>👥 Совпадения с твоими позициями</b>"]
    for (coin, whale), group in ranked[:_MAX_LINES_PER_SECTION]:
        # WR и число закрытий — из одного сигнала, иначе процент окажется
        # подписан чужой выборкой.
        best = max(group, key=lambda x: x.details.get("winrate_used", 0))
        wr = best.details.get("winrate_used", 0)
        closures = best.details.get("closures_used")
        whale_short = _e(_short_whale(whale)) if whale else "?"
        lines.append(
            f"• <code>{_e(coin)}</code> от <code>{whale_short}</code> "
            f"({fmt_wr(wr, closures)})"
        )
    if len(ranked) > _MAX_LINES_PER_SECTION:
        lines.append(f"  …и ещё {len(ranked) - _MAX_LINES_PER_SECTION}")
    return "\n".join(lines)


def _digest_new_open_section(signals: list[Signal]) -> Optional[str]:
    if not signals:
        return None
    # Монета + сторона (29.09). До этого группировали по монете и
    # подписывали строку направлением большинства: ZEC-лонг на $3.6M
    # печатался внутри «ZEC SHORT». Лонги и шорты — разные строки.
    by_coin: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for s in signals:
        side = str(s.details.get("direction") or "?").upper()
        by_coin[(s.coin, side)].append(s)

    # От большего объёма к меньшему (решение оператора 29.09): по числу
    # сигналов $107M шли вторыми после $12M.
    def _total(group: list[Signal]) -> float:
        return sum(g.details.get("notional_usd", 0) for g in group)

    ranked = sorted(by_coin.items(), key=lambda kv: -_total(kv[1]))

    lines = ["", "<b>🆕 Новые входы китов</b>"]
    for (coin, side), group in ranked[:_MAX_LINES_PER_SECTION]:
        # ×N — разные киты. Объём суммируется по всем сигналам: у каждого
        # свои филлы, повторный вход того же кита — новые деньги.
        n = len({g.details.get("whale") or id(g) for g in group})
        suffix = f" ×{n}" if n > 1 else ""
        if n == 1 and "winrate_used" in group[0].details:
            # Один кит — строка целиком его, и его послужной список должен
            # быть рядом, как в мгновенном алерте (30.09: «$37.7M» стоял на
            # ките с одним закрытием за всю историю).
            best = max(group, key=lambda x: x.details.get("winrate_used", 0))
            wr = best.details.get("winrate_used", 0)
            suffix = f" ({fmt_wr(wr, best.details.get('closures_used'))})"
        lines.append(
            f"• <code>{_e(coin)}</code> {_e(side)} • "
            f"{_fmt_money(_total(group))}{suffix}"
        )
    if len(ranked) > _MAX_LINES_PER_SECTION:
        lines.append(f"  …и ещё {len(ranked) - _MAX_LINES_PER_SECTION}")
    return "\n".join(lines)


def _digest_rank_section(signals: list[Signal]) -> Optional[str]:
    """Render NEW_ENTRANT / DROP_OFF block in digest."""
    new_entrants = [s for s in signals if s.rule == "WHALE_NEW_ENTRANT"]
    drop_offs = [s for s in signals if s.rule == "WHALE_DROP_OFF"]
    if not new_entrants and not drop_offs:
        return None

    lines = ["", "<b>📊 Изменения в топе</b>"]
    for s in new_entrants[:_MAX_LINES_PER_SECTION]:
        whale = _e(_short_whale(s.details.get("whale", "")))
        rank = s.details.get("last_rank", "?")
        runs = s.details.get("consecutive_in_top", 0)
        lines.append(f"🆕 <code>{whale}</code> вошёл в топ (rank {rank}, {runs} запусков подряд)")
    for s in drop_offs[:_MAX_LINES_PER_SECTION]:
        whale = _e(_short_whale(s.details.get("whale", "")))
        runs = s.details.get("runs_in_top", 0)
        last_rank = s.details.get("last_rank", "?")
        lines.append(f"📉 <code>{whale}</code> ушёл из топа (был {runs} запусков, последний rank {last_rank})")
    return "\n".join(lines)


# Правила, сознательно не показываемые в дайджесте: ротация лидерборда не
# несёт торгового решения (UX-фидбек 12.06). До 08.08 они всё равно копились
# в буфере и попадали в счётчик заголовка — сообщение обещало 146 событий и
# показывало одно. То, что решено не показывать, в буфер показа не кладётся.
DIGEST_HIDDEN_RULES = ("WHALE_NEW_ENTRANT", "WHALE_DROP_OFF")


def digest_visible(signals: list[Signal]) -> list[Signal]:
    """Только те сигналы, которые дайджест действительно покажет."""
    return [s for s in signals if s.rule not in DIGEST_HIDDEN_RULES]


def render_digest(signals: list[Signal], now: datetime) -> Optional[str]:
    """Build the daily digest for info-level signals."""
    signals = digest_visible(signals)
    if not signals:
        return None

    overlap = [s for s in signals if s.rule == SIG_OVERLAP]
    new_open = [s for s in signals if s.rule == SIG_NEW_OPEN]
    msk = now.astimezone(_MOSCOW)
    parts = [
        f"🐋 <b>Whale digest за 24ч</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK",
        f"Всего сигналов: {len(_dedupe_observations(signals))}",
    ]

    block = _digest_overlap_section(overlap)
    if block:
        parts.append(block)
    block = _digest_new_open_section(new_open)
    if block:
        parts.append(block)
    # other info-level rules — generic fallback
    # rank-churn (NEW_ENTRANT/DROP_OFF) намеренно исключён из канала —
    # ротация лидерборда не несёт торгового решения (UX-фидбек 12.06).
    other = _dedupe_observations(
        [s for s in signals if s.rule not in (SIG_OVERLAP, SIG_NEW_OPEN)])
    if other:
        parts.append("\n<b>Прочее</b>")
        for s in other[:_MAX_LINES_PER_SECTION]:
            parts.append(f"• {_e(s.message)}")

    if len(parts) <= 2:          # только заголовок + "Всего сигналов" -> нечего слать
        return None
    msg = "\n".join(parts)
    if len(msg) > _TG_LIMIT:
        while len(msg) > _TG_LIMIT and len(parts) > 2:
            parts.pop()
            parts.append("…")
            msg = "\n".join(parts)
        if len(msg) > _TG_LIMIT:
            msg = msg[: _TG_LIMIT - 20] + "\n… (truncated)"
    return msg
