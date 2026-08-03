"""Отношение позиции к вердикту, с учётом подавленного сырого сигнала.

Инвариант иерархии («тактика не торгует против стратегии») гасит шорт при
regime=BULL и отдаёт наружу ⚪ WAIT. Правило само по себе защитное и здесь
не оспаривается — но в отчёте оно выглядело как отсутствие мнения, хотя на
деле мнение было и его заглушили. За июнь-июль так погашено 60 сырых
шортов, все при BULL.

Оператор просил обратного: пусть система говорит, что видит, а слушать её
или нет — решение оператора. Поэтому здесь только чтение и подача: сырой
вердикт показывается рядом с финальным, а позиция, идущая против любого из
них, помечается явно. Логика вердикта не меняется — выборка для разбора
конца августа остаётся сравнимой.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

_DIRECTIONAL = ("LONG", "SHORT")
_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
_OPPOSITE = {"LONG": "SHORT", "SHORT": "LONG"}


class Stance(Enum):
    ALIGNED = "aligned"            # вердикт за позицию
    NEUTRAL = "neutral"            # вердикт молчит и сырой не против
    AGAINST = "against"            # финальный вердикт против позиции
    AGAINST_RAW = "against_raw"    # финал молчит, но сырой сигнал против


def _norm(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = str(v).upper()
    return v if v in ("LONG", "SHORT", "WAIT") else None


def position_stance(side: Optional[str], final: Optional[str],
                    raw: Optional[str] = None) -> Stance:
    """Как вердикт относится к открытой позиции.

    Финальный вердикт главнее: если он прямо против — это AGAINST. Если он
    молчит, но сырой сигнал направлен против позиции, это AGAINST_RAW —
    самый важный случай, потому что раньше он выглядел как нейтральное
    молчание.
    """
    side_n = _norm(side)
    final_n = _norm(final)
    raw_n = _norm(raw)

    if side_n not in _DIRECTIONAL:
        return Stance.NEUTRAL

    if final_n in _DIRECTIONAL:
        return Stance.AGAINST if final_n == _OPPOSITE[side_n] else Stance.ALIGNED

    if raw_n in _DIRECTIONAL and raw_n == _OPPOSITE[side_n]:
        return Stance.AGAINST_RAW

    return Stance.NEUTRAL


def format_verdict_pair(final: Optional[str], raw: Optional[str] = None) -> str:
    """«⚪ WAIT ← 🔴 SHORT» когда слои расходятся, иначе просто вердикт."""
    final_n = _norm(final)
    raw_n = _norm(raw)
    if final_n is None:
        return ""
    head = f"{_EMOJI[final_n]} {final_n}"
    if raw_n is None or raw_n == final_n:
        return head
    return f"{head} ← {_EMOJI[raw_n]} {raw_n} сыр."
