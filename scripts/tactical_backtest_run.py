#!/usr/bin/env python3
"""Прогон тактического слоя по истории. Запускается вручную.

Смысл: живая выборка растёт по 28 сделок в месяц, до статистически
осмысленных 400 — одиннадцать месяцев. История даёт ту же выборку за один
прогон. Живая торговля после этого работает как проверка вне выборки.

Чего в этих числах НЕТ: комиссий, фандинга, проскальзывания. Результат —
верхняя оценка. Если слой не окупается здесь, в живой торговле не окупится
тем более.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles  # noqa: E402
from src.tactical_backtest import EXIT_MODES, replay, summarise  # noqa: E402
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

import os

LOOKBACK_DAYS = int(os.environ.get("BACKTEST_LOOKBACK_DAYS") or 700)


def main() -> int:
    print(f"# Бэктест тактического слоя · {LOOKBACK_DAYS} дней · "
          f"{len(FOCUS_COINS)} монет\n")

    # Свечи качаем ОДИН раз: все варианты выхода обязаны сравниваться на
    # одних и тех же данных и одних и тех же входах.
    history = {}
    for coin in FOCUS_COINS:
        try:
            c = fetch_candles(coin, interval="1d", lookback_days=LOOKBACK_DAYS)
            if c:
                history[coin] = c
        except Exception as e:  # noqa: BLE001
            print(f"  {coin:7} свечи недоступны: {e}")
    if not history:
        print("Нет данных.")
        return 1

    results = {}
    for mode in EXIT_MODES:
        trades = []
        for coin, candles in history.items():
            trades.extend(replay(coin, candles, exit_mode=mode))
        results[mode] = summarise(trades)

    print(f"{'режим':10} {'n':>5} {'WR':>6} {'avg R':>8} {'медиана':>9} "
          f"{'95% интервал':>22}")
    for mode, s in results.items():
        if not s["n"]:
            print(f"  {mode:10} нет сделок")
            continue
        lo, hi = s["ci"]
        print(f"{mode:10} {s['n']:>5} {s['wr']:>5.0%} {s['avg_r']:>+8.3f} "
              f"{s['median_r']:>+9.3f}   [{lo:+.3f}, {hi:+.3f}]")

    best = max((m for m in results if results[m]["n"]),
               key=lambda m: results[m]["avg_r"])
    b = results[best]
    lo, hi = b["ci"]
    print(f"\nлучший по avg R: {best}")
    if lo > 0:
        print("→ интервал целиком выше нуля даже без издержек")
    else:
        print("→ интервал всё ещё накрывает ноль")
    print("\nВНИМАНИЕ: сравнение вариантов на одних данных — множественное")
    print("сравнение. Результат это ГИПОТЕЗА, а не вывод: подтверждать надо")
    print("вне выборки. И трейлинг усиливает преимущество, но не создаёт его.")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(
            f"{m:9} n={results[m]['n']:>4} · avg R {results[m]['avg_r']:+.3f} "
            f"· WR {results[m]['wr']:.0%}"
            for m in EXIT_MODES if results[m]["n"])
        send_messages([
            f"📐 <b>Бэктест: варианты выхода</b> — {LOOKBACK_DAYS} дн\n"
            f"<pre>{rows}</pre>\n"
            f"лучший: <b>{best}</b>, интервал [{lo:+.3f}, {hi:+.3f}]\n"
            f"<i>без издержек · одни входы · это гипотеза, не вывод</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[backtest] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
