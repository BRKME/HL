#!/usr/bin/env python3
"""Перебор моделей моментума. Запускается вручную через Actions.

Отвечает на вопрос, который нельзя решить, глядя на текущую модель: есть ли
ВООБЩЕ преимущество в этих монетах на этих данных, если строить сигнал так,
как это делают в опубликованных работах.

Честность обеспечивается разделением 70/30: лучший набор выбирается ТОЛЬКО
по обучающей части, а судят по проверочной, которую он не видел. При
переборе в сотню вариантов «победитель» найдётся и на чистом шуме — вопрос
лишь в том, переживёт ли он проверку.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles  # noqa: E402
from src.momentum_sweep import (  # noqa: E402
    buy_and_hold_r, default_grid, split, sweep, time_in_market,
)
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

LOOKBACK_DAYS = int(os.environ.get("SWEEP_LOOKBACK_DAYS") or 900)
COINS = [c.strip() for c in (os.environ.get("SWEEP_COINS") or "").split(",")
         if c.strip()] or list(FOCUS_COINS)
MIN_TEST_TRADES = 30      # меньше — на проверке судить не о чем


def main() -> int:
    series = {}
    for coin in COINS:
        try:
            candles = fetch_candles(coin, interval="1d",
                                    lookback_days=LOOKBACK_DAYS)
            closes = [float(k["c"]) for k in candles if k.get("c")]
            if len(closes) > 150:
                series[coin] = closes
        except Exception as e:  # noqa: BLE001
            print(f"  {coin}: {e}")
    if not series:
        print("нет данных")
        return 1

    grid = default_grid()
    print(f"# Перебор моментума · {len(grid)} конфигураций · "
          f"{len(series)} монет · {LOOKBACK_DAYS} дней\n")

    res = sweep(series, grid)
    usable = [(p, tr, te) for p, tr, te in res
              if tr.n >= MIN_TEST_TRADES and te.n >= MIN_TEST_TRADES]
    if not usable:
        print("ни одна конфигурация не набрала сделок")
        return 1

    # Выбор ТОЛЬКО по обучению — проверочную часть победитель не видел.
    usable.sort(key=lambda x: x[1].avg_r, reverse=True)
    best_p, best_tr, best_te = usable[0]

    # БЕНЧМАРК. Без него результат не значит ничего: «только лонг с
    # удержанием 20 дней» на растущем рынке = «купи и держи», прибыль без
    # предсказания. На этом мы уже обожглись с альфой планнера.
    import statistics as _st
    bh_test = [buy_and_hold_r(split(c)[1]) for c in series.values()]
    bh_test = [x for x in bh_test if x is not None]
    bh = _st.mean(bh_test) if bh_test else None
    print(f"«купи и держи» на проверочном периоде: "
          f"{bh:+.3f}" if bh is not None else "бенчмарк недоступен")
    print()

    print("топ-5 по обучающей части:")
    print(f"{'окно':>5} {'держ':>5} {'норм':>5} {'лонг':>5} "
          f"{'обуч n':>7} {'обуч R':>8} {'пров n':>7} {'пров R':>8}")
    for p, tr, te in usable[:5]:
        print(f"{p.lookback:>5} {p.holding:>5} {str(p.vol_scaled):>5} "
              f"{str(p.long_only):>5} {tr.n:>7} {tr.avg_r:>+8.3f} "
              f"{te.n:>7} {te.avg_r:>+8.3f}")

    # Насколько лучший на обучении просел на проверке — мера подгонки.
    degradation = best_tr.avg_r - best_te.avg_r
    median_test = sorted(te.avg_r for _, _, te in usable)[len(usable) // 2]

    tim = [time_in_market(split(c)[1], best_p) for c in series.values()]
    tim = [x for x in tim if x is not None]
    tim_avg = _st.mean(tim) if tim else None

    print(f"\nлучший по обучению: окно {best_p.lookback}, "
          f"держ {best_p.holding}, норм {best_p.vol_scaled}, "
          f"лонг {best_p.long_only}")
    print(f"  обучение : n={best_tr.n} avg R {best_tr.avg_r:+.3f}")
    print(f"  ПРОВЕРКА : n={best_te.n} avg R {best_te.avg_r:+.3f}")
    print(f"  просадка при переходе: {degradation:+.3f}")
    print(f"  медиана avg R по всем конфигурациям на проверке: "
          f"{median_test:+.3f}")

    verdict = ("преимущество переживает проверку"
               if best_te.avg_r > 0 and median_test > 0
               else "лучший держится, но остальные нет — вероятна подгонка"
               if best_te.avg_r > 0
               else "преимущества нет: лучший на обучении проваливает проверку")
    print(f"\nВЫВОД: {verdict}")

    bh_line = (f"«купи и держи» на проверке: {bh:+.3f}\n"
               f"в позиции {tim_avg:.0%} времени\n"
               if bh is not None and tim_avg is not None else "")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(
            f"окно {p.lookback:>2}д держ {p.holding:>2}д "
            f"{'норм' if p.vol_scaled else '    '} "
            f"{'лонг' if p.long_only else 'обе '} · "
            f"обуч {tr.avg_r:+.3f} → пров {te.avg_r:+.3f}"
            for p, tr, te in usable[:5])
        send_messages([
            f"🔬 <b>Перебор моментума</b> — {len(grid)} конфигураций, "
            f"{len(series)} монет, {LOOKBACK_DAYS} дн\n"
            f"<pre>{rows}</pre>\n"
            f"медиана по проверке: {median_test:+.3f}\n"
            f"{bh_line}"
            f"<b>{verdict}</b>\n"
            f"<i>выбор по обучению, судим по проверке — она не видела "
            f"отбора</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[sweep] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
