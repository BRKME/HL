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
from src.tactical_backtest import replay, summarise  # noqa: E402
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

import os

LOOKBACK_DAYS = int(os.environ.get("BACKTEST_LOOKBACK_DAYS") or 700)


def main() -> int:
    all_trades = []
    print(f"# Бэктест тактического слоя · {LOOKBACK_DAYS} дней · "
          f"{len(FOCUS_COINS)} монет\n")

    for coin in FOCUS_COINS:
        try:
            candles = fetch_candles(coin, interval="1d",
                                    lookback_days=LOOKBACK_DAYS)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin:7} свечи недоступны: {e}")
            continue
        if not candles:
            print(f"  {coin:7} нет данных")
            continue
        trades = replay(coin, candles)
        all_trades.extend(trades)
        s = summarise(trades)
        avg = f"{s['avg_r']:+.3f}" if s["avg_r"] is not None else "—"
        print(f"  {coin:7} свечей {len(candles):4} · сделок {s['n']:3} · "
              f"avg R {avg}")

    print()
    s = summarise(all_trades)
    if not s["n"]:
        print("Сделок не получилось — проверить данные.")
        return 1

    lo, hi = s["ci"]
    print(f"ВСЕГО сделок      : {s['n']}")
    print(f"WR                : {s['wr']:.0%}")
    print(f"avg R / медиана   : {s['avg_r']:+.3f} / {s['median_r']:+.3f}")
    print(f"95% интервал avg R: [{lo:+.3f}, {hi:+.3f}]")
    print(f"выходы            : {s['by_reason']}")
    for side, d in s["by_side"].items():
        print(f"{side:5} avg R (n)    : {d['avg_r']:+.3f} ({d['n']})")

    # Итог в Telegram: результат нужен оператору, а не в логе Actions.
    try:
        from src.telegram_sender import send_messages
        verdict = ("зарабатывает" if (lo or 0) > 0
                   else "теряет" if (hi or 0) < 0
                   else "не доказано")
        send_messages([
            f"📐 <b>Бэктест тактики</b> — {s['n']} сделок за {LOOKBACK_DAYS} дн\n"
            f"WR {s['wr']:.0%} · avg R {s['avg_r']:+.3f} · "
            f"медиана {s['median_r']:+.3f}\n"
            f"95% интервал [{lo:+.3f}, {hi:+.3f}] → <b>{verdict}</b>\n"
            f"<i>без комиссий и фандинга — верхняя оценка</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[backtest] отправка не удалась: {e}")

    print()
    if lo is not None and lo > 0:
        print("ВЫВОД: слой зарабатывает — интервал целиком выше нуля.")
    elif hi is not None and hi < 0:
        print("ВЫВОД: слой теряет — интервал целиком ниже нуля.")
    else:
        print("ВЫВОД: интервал накрывает ноль — преимущество не доказано "
              "даже без комиссий.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
