"""Сжатие дайджеста: перечислять отклонения, а не норму.

Сообщение от 03.08 23:26 — 1353 символа, из них 626 (46%) занимали восемь
строк «НЕ ВХОДИТЬ» с почти одинаковыми пояснениями. Оператор читает такое
сообщение каждый день; если половина его объёма сообщает, что ничего не
произошло, читать перестанут целиком — вместе с двумя строками, которые
требовали реакции.

Здесь только подача: вердикты не меняются, ни один не исчезает из журнала.
Меняется то, сколько места занимает отсутствие событий.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

# Записи дайджеста приходят кортежем
# (coin, mark, verdict, rationale, raw_verdict, raw_rationale).
_COIN, _VERDICT = 0, 2

_ENTRY_VERDICTS = ("LONG", "SHORT")


def collapse_wait_verdicts(
    verdicts: Sequence[tuple],
) -> tuple[list[tuple], Optional[str]]:
    """Схлопнуть сплошные WAIT в одну строку.

    Возвращает (что печатать построчно, сводка или None).

    Если есть хотя бы один вход — не схлопываем ничего: когда появляется
    сигнал, важно сравнение с остальными монетами, и полный список снова
    несёт смысл.

    NODATA никогда не прячется: это отказ источника, а не отсутствие
    сигнала, и он должен быть виден — иначе получится ровно тот тихий
    отказ, от которого мы защищаемся в другом месте.
    """
    if not verdicts:
        return [], None

    has_entry = any(v[_VERDICT] in _ENTRY_VERDICTS for v in verdicts)
    if has_entry:
        return list(verdicts), None

    nodata = [v for v in verdicts if v[_VERDICT] == "NODATA"]
    waits = [v for v in verdicts if v[_VERDICT] not in ("NODATA",)]
    if not waits:
        return nodata, None

    # Моноширинные тикеры, как в остальном сообщении: сводка заменяет
    # восемь строк, но не должна выглядеть чужеродной вставкой.
    coins = " ".join(f"<code>{v[_COIN]}</code>" for v in waits)
    summary = f"⚪ Входов нет ({len(waits)}/{len(waits)}): {coins}"
    return nodata, summary


def compact_stance_line(line: str) -> str:
    """Убрать из китовой строки монеты без данных.

    «BTC — • ETH 100%↓ • ZEC 100%↓ • NEAR — • HYPE — • TAO —» несёт ровно
    два факта и восемь названий. Пустые позиции удаляются; если не осталось
    ничего, строка исчезает целиком.
    """
    if not line or ":" not in line:
        return line

    prefix, _, body = line.partition(":")
    parts = [p.strip() for p in body.split("•")]
    kept = [p for p in parts if p and not re.fullmatch(r"\S+\s*—", p)]
    if not kept:
        return ""
    return f"{prefix}: " + " • ".join(kept)


def beta_warning(verdicts) -> str:
    """Предупреждение, когда за проход выпало несколько входов в одну сторону.

    Тактический слой такое предупреждение даёт, дайджест — нет, хотя
    показывает ту же картину сразу по девяти монетам и читается как
    несколько независимых идей. 24.08 их было четыре, на счёте $189 и при
    корреляции альтов около единицы это треть депозита в одну сторону.

    Разные стороны одновременно не предупреждаем: это хедж, а не ставка.
    """
    dirs = [v[_VERDICT] for v in verdicts if v[_VERDICT] in _ENTRY_VERDICTS]
    if len(dirs) < 2 or len(set(dirs)) != 1:
        return ""
    side = dirs[0]
    return (f"⚠️ {len(dirs)} входа в одну сторону ({side}) — это одна "
            f"бета-ставка на рынок: дели тактический размер между ними, "
            f"не удваивай риск.")


# --------------------------------- дайджест как руководство к действию (30.08)

_OVERHEAT = ("overbought", "oversold", "перегрев")


def collapse_waits_when_entries(verdicts):
    """Когда входы есть, «НЕ ВХОДИТЬ» сворачивается в одну строку.

    30.08 письмо состояло из пяти строк про то, чего делать НЕ надо, и
    четырёх про то, что делать. Оператор читает его ради вторых. Причина
    ожидания сохраняется — но одним словом, а не повтором «RSI 70 — ждать
    pullback» пять раз (политика §7.7).

    NODATA не прячется никогда: это отказ источника, а не отсутствие
    сигнала.
    """
    if not verdicts:
        return list(verdicts), ""
    if not any(v[_VERDICT] in _ENTRY_VERDICTS for v in verdicts):
        return list(verdicts), ""

    kept, hot, weak = [], [], []
    for v in verdicts:
        verdict = v[_VERDICT]
        if verdict in _ENTRY_VERDICTS or verdict == "NODATA":
            kept.append(v)
            continue
        rationale = str(v[3] or "").lower()
        (hot if any(w in rationale for w in _OVERHEAT) else weak).append(str(v[_COIN]))

    parts = []
    if hot:
        parts.append("перегрев: " + " ".join(f"<code>{c}</code>" for c in hot))
    if weak:
        parts.append("слабый сигнал: " + " ".join(f"<code>{c}</code>" for c in weak))
    return kept, ("⚪ Ждут — " + " • ".join(parts)) if parts else ""


def rank_entries(verdicts):
    """Упорядочить входы по относительной силе, сильные выше.

    ВАЖНО: валидированного способа ранжировать входы у системы нет.
    Относительная сила — кандидат из гипотезы H4 (фильтр перегрева
    отсеивает лидеров), которая зарегистрирована, но НЕ проверена. Порядок
    показывается вместе с числом и пометкой, чтобы оператор видел, на чём
    он основан.

    Отбор сигналов это не меняет: измеримость H3/H4 не страдает.
    Монеты без данных о силе уходят вниз, порядок между ними сохраняется.
    """
    entries = [v for v in verdicts if v[_VERDICT] in _ENTRY_VERDICTS]
    if len(entries) < 2:
        return list(verdicts)
    rest = [v for v in verdicts if v[_VERDICT] not in _ENTRY_VERDICTS]
    ranked = sorted(
        entries,
        key=lambda v: (v[6] is None, -(v[6] or 0.0)),
    )
    return ranked + rest
