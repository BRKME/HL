#!/usr/bin/env python3
"""Соотношения риск/прибыль против теоретической базы. Вручную.

Отвечает на вопрос, который нельзя решить перебором: даёт ли выбор стопа и
цели преимущество САМ ПО СЕБЕ. Теория говорит, что нет — для случайного
блуждания матожидание тождественно ноль при любом соотношении. Здесь
проверяется, отличается ли реальный рынок от случайного блуждания.

Сравнивается не прибыльность, а РАЗНИЦА между наблюдаемой вероятностью
достижения цели и теоретической B/(A+B). Положительная разница означает
моментум и является преимуществом; нулевая означает, что стопы и цели
можно ставить как угодно — результат не изменится.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.barrier_test import grid  # noqa: E402
from src.hl_api import fetch_candles  # noqa: E402

COINS = [c.strip() for c in (os.environ.get("BARRIER_COINS")
                             or "BTC,ETH").split(",") if c.strip()]
MAX_BARS = int(os.environ.get("BARRIER_MAX_BARS") or 240)   # часов
EDGE_PP_THRESHOLD = 3.0    # порог, записанный ДО прогона


def main() -> int:
    print(f"# Барьеры · {', '.join(COINS)} · окно {MAX_BARS}ч\n")
    all_rows = {}
    for coin in COINS:
        try:
            candles = fetch_candles(coin, interval="1h", lookback_days=700)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin}: свечи недоступны ({e})")
            continue
        rows = grid(candles, max_bars=MAX_BARS)
        all_rows[coin] = rows
        print(f"\n{coin}: {len(candles)} свечей")
        print(f"{'SL':>5} {'TP':>5} {'n':>5} {'цель':>6} {'стоп':>6} "
              f"{'таймаут':>8} {'P набл':>8} {'P теор':>8} {'разница':>9} "
              f"{'ожидание':>10}")
        for r in rows:
            if r.observed_p is None:
                print(f"{-r.sl_pct:>4}% {r.tp_pct:>4}%  данных мало")
                continue
            print(f"{-r.sl_pct:>4}% {r.tp_pct:>4}% {r.n:>5} {r.hit_tp:>6} "
                  f"{r.hit_sl:>6} {r.timeout:>8} {r.observed_p:>7.1%} "
                  f"{r.theoretical_p:>7.1%} {r.edge_pp:>+8.1f} "
                  f"{r.expectancy_pct:>+9.3f}%")

    edges = [r.edge_pp for rows in all_rows.values() for r in rows
             if r.edge_pp is not None]
    if not edges:
        print("\nданных не хватило")
        return 1
    import statistics
    avg_edge = statistics.mean(edges)
    positive = sum(1 for e in edges if e > 0) / len(edges)

    if avg_edge >= EDGE_PP_THRESHOLD and positive >= 0.7:
        verdict = (f"рынок отличается от случайного блуждания: "
                   f"средняя разница {avg_edge:+.1f} п.п.")
    elif abs(avg_edge) < EDGE_PP_THRESHOLD:
        verdict = (f"неотличимо от случайного блуждания ({avg_edge:+.1f} п.п.) "
                   f"— соотношение SL/TP преимущества не даёт")
    else:
        verdict = (f"разница {avg_edge:+.1f} п.п. ПРОТИВ нас — цель "
                   f"достигается реже теоретической")
    print(f"\nположительных комбинаций: {positive:.0%}")
    print(f"ВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        lines = []
        for coin, rows in all_rows.items():
            for r in rows:
                if r.edge_pp is None:
                    continue
                lines.append(f"{coin} {-r.sl_pct:g}/{r.tp_pct:g} n={r.n:>4} "
                             f"P {r.observed_p:.0%} vs {r.theoretical_p:.0%} "
                             f"= {r.edge_pp:+.1f}пп")
        send_messages([
            f"🎯 <b>Барьеры SL/TP против теории</b>\n"
            f"<pre>{chr(10).join(lines[:16])}</pre>\n"
            f"<b>{verdict}</b>\n"
            f"<i>теория: P(цель) = SL/(SL+TP), ожидание тождественно ноль "
            f"при любом соотношении</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[barrier] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
