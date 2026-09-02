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
from src.momentum_sweep import run_one  # noqa: E402
from src.momentum_sweep import (  # noqa: E402
    buy_and_hold_curve, buy_and_hold_r, default_grid, equity_curve,
    max_drawdown, random_entry_r, split, sweep, time_in_market,
    walk_forward,
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
    import statistics as _st

    # ПАРНОЕ СРАВНЕНИЕ ФИЛЬТРА. Не «найди лучший из ещё большего перебора»
    # — это ловушка, в которую мы уже дважды почти попали, — а «тот же
    # набор с фильтром и без». Улучшение усредняется по ВСЕМ наборам:
    # если фильтр помогает лишь одному, это шум.
    from src.momentum_sweep import REGIME_BUFFERS, REGIME_MA_LENGTHS
    usable = [(p, tr, te) for p, tr, te in res
              if tr.n >= MIN_TEST_TRADES and te.n >= MIN_TEST_TRADES]
    if not usable:
        print("ни одна конфигурация не набрала сделок")
        return 1

    # Выбор ТОЛЬКО по обучению — проверочную часть победитель не видел.
    usable.sort(key=lambda x: x[1].avg_r, reverse=True)
    best_p, best_tr, best_te = usable[0]


    print("\n=== режимный фильтр: парное сравнение ===")
    filter_rows = []
    for ma_len in REGIME_MA_LENGTHS:
        if ma_len == 0:
            continue
        for buf in REGIME_BUFFERS:
            deltas, kept = [], []
            for p, tr, te in res:
                if te.avg_r is None or te.n < 10:
                    continue
                fp = type(p)(p.lookback, p.holding, p.vol_scaled,
                             p.long_only, ma_len, buf)
                f_rs = []
                for closes in series.values():
                    f_rs.extend(run_one(split(closes)[1], fp))
                if len(f_rs) < 10:
                    continue
                deltas.append(_st.mean(f_rs) - te.avg_r)
                kept.append(len(f_rs) / max(te.n, 1))
            if deltas:
                better = sum(1 for d in deltas if d > 0) / len(deltas)
                line = (f"MA{ma_len} зона {buf:.0%}: прирост "
                        f"{_st.mean(deltas):+.3f}, помог в {better:.0%} "
                        f"наборов, сделок {_st.mean(kept):.0%}")
                print("  " + line)
                filter_rows.append((line, _st.mean(deltas), better))

    # БЕНЧМАРК. Без него результат не значит ничего: «только лонг с
    # удержанием 20 дней» на растущем рынке = «купи и держи», прибыль без
    # предсказания. На этом мы уже обожглись с альфой планнера.
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

    # Правильный контроль — случайный вход с ТОЙ ЖЕ долей времени в рынке.
    # «Купи и держи» обманывает в обе стороны: на росте наказывает за
    # пребывание вне рынка, на падении награждает за него. Именно так
    # «альфа» планнера перевернулась с +20 на -20 при смене направления.
    rnd = [random_entry_r(split(c)[1], best_p.holding, tim_avg or 0.5,
                          long_only=best_p.long_only)
           for c in series.values()]
    rnd = [x for x in rnd if x is not None]
    rand_avg = _st.mean(rnd) if rnd else None

    # ИЗДЕРЖКИ. Тот же лучший набор, но с комиссией и фандингом. Разница
    # и есть цена реальности: измеренное преимущество против случайного
    # входа было +0.44…+0.78 R, а издержки при удержании в 20 дней
    # составляют около 0.24…0.30 R.
    net_rs = []
    for closes in series.values():
        net_rs.extend(run_one(split(closes)[1], best_p, net_of_costs=True))
    net_avg = _st.mean(net_rs) if net_rs else None
    cost_line = ""
    if net_avg is not None and best_te.avg_r is not None:
        drag = best_te.avg_r - net_avg
        print(f"\n=== издержки ===")
        print(f"  до издержек: {best_te.avg_r:+.3f} · после: {net_avg:+.3f} "
              f"· цена {drag:+.3f} R на сделку")
        if rand_avg is not None:
            print(f"  умение после издержек: {net_avg - rand_avg:+.3f}")
        cost_line = (f"после издержек: {net_avg:+.3f} "
                     f"(цена {drag:.3f} R)\n")
        if rand_avg is not None and net_avg <= rand_avg:
            cost_line += "издержки съедают всё преимущество\n"

    if rand_avg is not None:
        print(f"  случайный вход с той же экспозицией: {rand_avg:+.3f}")
        print(f"  умение выбирать момент: {best_te.avg_r - rand_avg:+.3f}")

    # ИТОГ ЗА ПЕРИОД, а не на сделку. Прошлый отчёт сравнивал avg_r модели
    # с итогом «купи и держи» — это разные единицы, и вывод «удержание дало
    # больше» был бессмысленным.
    model_total = _st.mean([best_te.avg_r * best_te.n / max(len(series), 1)]) \
        if best_te.n else 0.0

    # ПРОСАДКА. При плече 5x она важнее доходности: стратегия с меньшей
    # доходностью и вдвое меньшей просадкой допускает вдвое большее плечо
    # при том же риске — то есть даёт БОЛЬШЕ денег.
    dd_model, dd_bh = [], []
    for c in series.values():
        test = split(c)[1]
        d1 = max_drawdown(equity_curve(test, best_p))
        d2 = max_drawdown(buy_and_hold_curve(test))
        if d1 is not None:
            dd_model.append(d1)
        if d2 is not None:
            dd_bh.append(d2)
    ddm = _st.mean(dd_model) if dd_model else None
    ddb = _st.mean(dd_bh) if dd_bh else None
    if ddm is not None and ddb is not None:
        print(f"  просадка модели: {ddm:+.2f} · «купи и держи»: {ddb:+.2f}")
        if ddm < 0 and ddb < 0:
            print(f"  доходность на единицу просадки: модель "
                  f"{model_total / abs(ddm):+.2f} · удержание "
                  f"{(bh or 0) / abs(ddb):+.2f}")

    # Вердикт — по сравнению с КОНТРОЛЕМ, а не по знаку прибыли.
    if rand_avg is None:
        verdict = "контроль недоступен — судить нельзя"
    elif best_te.avg_r <= rand_avg:
        verdict = (f"умения выбирать момент нет: случайный вход с той же "
                   f"экспозицией дал {rand_avg:+.3f}")
    elif median_test <= 0:
        verdict = "хорош только победитель — вероятна подгонка"
    else:
        edge = best_te.avg_r - rand_avg
        verdict = (f"обгоняет случайный вход на {edge:+.3f} "
                   f"при {tim_avg:.0%} времени в позиции")
        if ddm is not None and ddb is not None and ddm > ddb:
            verdict += (f"; просадка вдвое меньше удержания "
                        f"({ddm:+.2f} против {ddb:+.2f})"
                        if abs(ddm) * 2 <= abs(ddb) else
                        f"; просадка {ddm:+.2f} против {ddb:+.2f}")

    print(f"\nВЫВОД: {verdict}")

    # СКОЛЬЗЯЩАЯ ПРОВЕРКА. Разделение 70/30 отвечает, работало ли это в
    # конце периода. Скользящая — работало ли ПОВТОРЯЕМО: набор выбирается
    # по прошлому и меряется на следующем отрезке, и так четыре раза. Если
    # лучший набор скачет, а результаты вперёд около нуля, устойчивого
    # преимущества нет, каким бы удачным ни был один сплит.
    wf_all = []
    for c in series.values():
        wf_all.extend(walk_forward(c, grid, folds=4, with_drawdown=True))
    wf_line = ""
    if wf_all:
        fwds = [row[2] for row in wf_all]
        wf_mean = _st.mean(fwds)
        wf_pos = sum(1 for f in fwds if f > 0) / len(fwds)
        windows = {row[0].lookback for row in wf_all}

        # Разрешение конфликта: доходность может быть неустойчивой, а
        # преимущество по просадке — держаться, потому что оно структурное.
        pairs = [(row[3], row[4]) for row in wf_all
                 if row[3] is not None and row[4] is not None]
        if pairs:
            better = sum(1 for m, b in pairs if m > b) / len(pairs)
            print(f"  доход/просадка выше, чем у удержания, в "
                  f"{better:.0%} отрезков ({len(pairs)})")
            wf_ratio_line = (f"доход/просадка лучше удержания в "
                             f"{better:.0%} отрезков\n")
        else:
            wf_ratio_line = ""
        print(f"\nскользящая проверка: {len(wf_all)} отрезков, "
              f"средний результат вперёд {wf_mean:+.3f}, "
              f"положительных {wf_pos:.0%}")
        print(f"  выбранные окна: {sorted(windows)}")
        wf_line = (f"скользящая: {len(wf_all)} отрезков, вперёд "
                   f"{wf_mean:+.3f}, плюсовых {wf_pos:.0%}\n"
                   + wf_ratio_line)
        if wf_mean <= 0:
            verdict += "; но скользящая проверка не подтверждает"
        elif wf_pos < 0.6:
            verdict += f"; скользящая плюсова лишь в {wf_pos:.0%} отрезков"

    bh_line = ""
    if bh is not None and tim_avg is not None:
        bh_line = (f"«купи и держи»: {bh:+.3f} · в позиции "
                   f"{tim_avg:.0%} времени\n")
    if rand_avg is not None:
        bh_line += f"случайный вход той же экспозиции: {rand_avg:+.3f}\n"
    if ddm is not None and ddb is not None:
        bh_line += f"просадка: модель {ddm:+.2f} · удержание {ddb:+.2f}\n"
        if ddm < 0 and ddb < 0 and bh is not None:
            bh_line += (f"итог за период: модель {model_total:+.2f} · "
                        f"удержание {bh:+.2f}\n"
                        f"доход на единицу просадки: "
                        f"{model_total / abs(ddm):+.2f} против "
                        f"{bh / abs(ddb):+.2f}\n")
    bh_line += wf_line + cost_line

    # Итог парного сравнения — В СООБЩЕНИЕ, а не только в лог Actions.
    # Величина, посчитанная и не показанная, для оператора не существует:
    # это уже третий такой случай.
    filter_block = ""
    if filter_rows:
        best_f = max(filter_rows, key=lambda x: x[1])
        rows_txt = "\n".join(r[0] for r in filter_rows)
        filter_block = (f"\n<b>Режимный фильтр (парно)</b>\n"
                        f"<pre>{rows_txt}</pre>\n")
        if best_f[1] <= 0:
            filter_block += "фильтр не помогает ни в одной конфигурации\n"
        elif best_f[2] < 0.6:
            filter_block += (f"помог лишь в {best_f[2]:.0%} наборов — "
                             f"вероятно шум\n")

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
            f"{filter_block}"
            f"<i>выбор по обучению, судим по проверке — она не видела "
            f"отбора</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[sweep] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
