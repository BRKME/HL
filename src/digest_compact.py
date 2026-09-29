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
_COIN, _VERDICT, _RAW = 0, 2, 4

_ENTRY_VERDICTS = ("LONG", "SHORT")


def _no_entries_reason(waits: Sequence[tuple], regime: Optional[str]) -> str:
    """Почему входов нет — из того, что система уже посчитала (29.09).

    «Входов нет (9/9)» не отвечало на вопрос оператора «а почему не
    шорты?». Ответ лежит в самих записях: график по монете (raw) и режим,
    который мог его погасить. Вердикты здесь не меняются.
    """
    def _raw(v):
        return str(v[_RAW]).upper() if len(v) > _RAW else "WAIT"

    def _list(vs):
        return " ".join(f"<code>{v[_COIN]}</code>" for v in vs)

    up = [v for v in waits if _raw(v) == "LONG"]
    down = [v for v in waits if _raw(v) == "SHORT"]
    flat = [v for v in waits if _raw(v) not in ("LONG", "SHORT")]
    reg = str(regime or "").upper()

    out = []
    if up:
        why = (f"лонги гасит режим {reg}" if reg == "BEAR"
               else "вход отложен фильтрами графика")
        out.append(f"   ↳ график вверх у {len(up)}: {_list(up)} — {why}")
    if down:
        why = (f"шорты гасит режим {reg}" if reg == "BULL"
               else "вход отложен фильтрами графика")
        out.append(f"   ↳ график вниз у {len(down)}: {_list(down)} — {why}")
    if flat:
        out.append(f"   ↳ без тренда: {_list(flat)}")
    # Отсутствие второй стороны называется, только когда первая есть:
    # «шортов нет» — ответ на вопрос, заданный при сплошном «вверх».
    if up and not down:
        out.append("   ↳ шортов нет: график ни на одной монете не вниз")
    elif down and not up:
        out.append("   ↳ лонгов нет: график ни на одной монете не вверх")
    return "\n".join(out)


def collapse_wait_verdicts(
    verdicts: Sequence[tuple],
    regime: Optional[str] = None,
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

    # Тикеры перечисляются в строках причин — каждая монета ровно один
    # раз (§7.9): список в заголовке повторял бы их все второй раз.
    summary = (f"⚪ <b>Сегодня ничего не делаем.</b> "
               f"Входов нет ({len(waits)}/{len(waits)}):\n"
               + _no_entries_reason(waits, regime))
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


def rank_entries(verdicts, scores: dict = None):
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
    if scores:
        # Составная оценка: расстановка сил, сила против BTC, новизна,
        # исполнимость. Веса — из фактических замеров, см. entry_score.
        ranked = sorted(entries, key=lambda v: -scores.get(v[_COIN], 0.0))
    else:
        ranked = sorted(entries, key=lambda v: (v[6] is None, -(v[6] or 0.0)))
    return ranked + rest


# ------------------------------------------------- приоритет входов (11.09)

# Веса взяты из ФАКТИЧЕСКИХ замеров, а не назначены:
#
#   расстановка сил  0.45 — «крупные в шорте при толпе в лонге» дало
#                           −0.45% на семи монетах из девяти (07–08.09).
#                           Самый сильный измеренный эффект из имеющихся.
#   сила против BTC  0.13 — разница avg R между RS>0 и RS<0 составила
#                           +0.128 (H4, 24.08).
#   новизна          0.10 — НЕ измерялась. Вес мал намеренно: ценность
#                           практическая, а не предсказательная — сигнал
#                           двенадцатого дня оператор уже видел и не взял.
#
# ВАЖНО: ни один признак не перешёл порог значимости. RS дал +0.128 при
# пороге 0.20, расстановка −0.45% при пороге 0.5%. Порядок — подсказка, а
# не сигнал; он меняет, что читать первым, и не меняет, что эмитируется.
W_POSITIONING = 0.45
W_RELATIVE_STRENGTH = 0.13
W_NOVELTY = 0.10

RS_SCALE = 30.0        # п.п., выше которых прибавка не растёт


def entry_score(rs_pp, accounts_ratio, top_pos_ratio, is_new,
                executable=True) -> float:
    """Оценка привлекательности входа. Больше — выше в письме.

    Неисполнимое опускается вниз безусловно: сделка, которую нельзя
    открыть, не может быть первой в списке, каким бы хорошим ни был
    сигнал.
    """
    if not executable:
        return -100.0

    score = 0.0

    if rs_pp is not None:
        # Обрезаем: +105 п.п. у ZEC не втрое лучше, чем +35, — это уже
        # область, где рост говорит скорее о перегреве, чем о качестве.
        capped = max(-RS_SCALE, min(RS_SCALE, float(rs_pp)))
        score += W_RELATIVE_STRENGTH * (capped / RS_SCALE)

    # Расстановка: крупные против толпы — вниз, крупные заодно с толпой
    # в противоход толпе — вверх.
    if accounts_ratio is not None and top_pos_ratio is not None:
        from src.positioning import long_share
        la, lt = long_share(accounts_ratio), long_share(top_pos_ratio)
        if la is not None and lt is not None:
            divergence = lt - la          # >0: крупные длиннее толпы
            score += W_POSITIONING * max(-1.0, min(1.0, divergence * 4))

    if is_new:
        score += W_NOVELTY

    return score



# --------------------------------- одно предложение в день (18.09.2026)

# Сколько входов показывать подробно. Один был крайностью в другую
# сторону: порядок строится по НЕподтверждённой оценке, и единственная
# строка делает цену ошибки ранжирования максимальной. Три — компромисс:
# оператор видит выбор, но не семь идей в день при недельном горизонте
# (18.09, уточнение оператора).
TOP_ENTRIES = 3


def keep_top_entry(verdicts, scores: dict = None, top: int = TOP_ENTRIES):
    """Оставить несколько лучших входов, остальные свернуть в строку.

    Указание оператора: «мы смотрим рынок в среднесрочной перспективе» —
    семь идей в день при удержании неделями это две сотни идей на горизонте
    одной сделки. Письмо при этом противоречило себе трижды разом: «вот
    семь входов», «это одна бета-ставка на рынок», «счёт тянет одну
    сделку».

    Остальные входы НЕ прячутся: они уходят в одну строку с монетами.
    Скрыть их значило бы лишить оператора выбора; показать наравне —
    вернуть ту же кашу.
    """
    entries = [v for v in verdicts if v[_VERDICT] in _ENTRY_VERDICTS]
    if len(entries) <= top:
        return list(verdicts), ""

    rest_of_list = [v for v in verdicts if v[_VERDICT] not in _ENTRY_VERDICTS]
    if scores:
        entries = sorted(entries, key=lambda v: -scores.get(v[_COIN], 0.0))

    shown, others = entries[:top], entries[top:]
    names = " ".join(f"<code>{v[_COIN]}</code>" for v in others)
    line = (f"🔁 Ещё {len(others)} в ту же сторону: {names} — "
            f"показаны {top} лучших, остальные ждут очереди.")
    return shown + rest_of_list, line
