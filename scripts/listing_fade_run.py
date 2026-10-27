#!/usr/bin/env python3
"""Шорт новых листингов Hyperliquid со второго дня. Запускается вручную.

КРИТЕРИИ, ЗАПИСАННЫЕ ДО ПРОГОНА (docs/OPERATING_POLICY.md, 27.10.2026):
событий ≥ 30; худшая сделка лучше −40%; обгон шорта в случайный день тех же
монет ≥ 3 п.п.; обгон положителен в ОБЕИХ половинах событий по времени —
иначе лучший из 24 вариантов сетки найдётся и на шуме.

Без комиссий и фандинга. Снятые с торгов монеты включены: без них выборка
смещена — рухнувшие и снятые и есть удачные шорты.
"""
import os
import random
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles, fetch_meta_and_ctxs  # noqa: E402
from src.listing_fade import (  # noqa: E402
    DAY_MS, ENTRY_IDX, listing_index, short_return, split_halves_edge,
)

LOOKBACK_DAYS = int(os.environ.get("LISTING_LOOKBACK_DAYS") or 1000)
HORIZONS = (3, 7, 14, 30)
STOPS = (None, 10, 15, 20, 25, 35)
MIN_EVENTS = 30
EDGE_PP = 3.0
WORST_LIMIT = -40.0
CONTROL_FROM_D = 30
CONTROL_STEP_D = 7
FETCH_PAUSE_S = float(os.environ.get("LISTING_FETCH_PAUSE_S") or 0.3)


def main() -> int:
    meta = fetch_meta_and_ctxs()
    coins = sorted(meta)
    delisted = {c for c, m in meta.items() if (m or {}).get("delisted")}
    window_start = int(time.time() * 1000) - LOOKBACK_DAYS * DAY_MS
    print(f"# Шорт новых листингов со второго дня · {LOOKBACK_DAYS} дн · "
          f"монет на бирже {len(coins)} (снятых {len(delisted)})\n")

    history = {}
    for coin in coins:
        try:
            c = fetch_candles(coin, interval="1d", lookback_days=LOOKBACK_DAYS)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin:10} свечи недоступны: {e}")
            c = None
        if c and listing_index(c, window_start) is not None:
            history[coin] = c
        if FETCH_PAUSE_S:
            time.sleep(FETCH_PAUSE_S)
    n_del = sum(1 for c in history if c in delisted)
    print(f"листингов в окне: {len(history)} (из них снятых с торгов: {n_del})")
    if history:
        d0 = [(float(c[ENTRY_IDX]["c"]) / float(c[0]["o"]) - 1) * 100
              for c in history.values()
              if len(c) > ENTRY_IDX and float(c[0].get("o") or 0) > 0]
        if d0:
            print(f"от открытия листинга до входа: медиана "
                  f"{statistics.median(d0):+.1f}%\n")

    rows = []
    for h in HORIZONS:
        for stop in STOPS:
            ev, ctrl, stopped = [], [], 0
            for coin, cs in history.items():
                r = short_return(cs, ENTRY_IDX, h, stop)
                if r is not None:
                    ev.append((cs[0]["t"], r[0]))
                    stopped += r[1]
                rng = random.Random(f"{coin}:{h}:{stop}")
                idxs = list(range(CONTROL_FROM_D, len(cs) - h, CONTROL_STEP_D))
                for i in rng.sample(idxs, min(len(idxs), 10)):
                    rc = short_return(cs, i, h, stop)
                    if rc is not None:
                        ctrl.append(rc[0])
            if not ev or not ctrl:
                continue
            rets = [r for _, r in ev]
            c_avg = statistics.mean(ctrl)
            avg = statistics.mean(rets)
            lo, hi = split_halves_edge(ev, c_avg)
            rows.append(dict(h=h, stop=stop, n=len(rets), avg=avg,
                             med=statistics.median(rets), worst=min(rets),
                             stopped=stopped / len(rets), ctrl=c_avg,
                             edge=avg - c_avg, lo=lo, hi=hi))

    print(f"{'гориз':>5} {'стоп':>5} {'n':>4} {'средняя':>8} {'медиана':>8} "
          f"{'худшая':>7} {'выбито':>6} {'случ':>7} {'обгон':>7} "
          f"{'1-я пол':>8} {'2-я пол':>8}")
    for r in rows:
        lab = "нет" if r["stop"] is None else f"-{r['stop']}%"
        lo = "—" if r["lo"] is None else f"{r['lo']:+.1f}"
        hi = "—" if r["hi"] is None else f"{r['hi']:+.1f}"
        print(f"{r['h']:>4}д {lab:>5} {r['n']:>4} {r['avg']:>+7.1f}% "
              f"{r['med']:>+7.1f}% {r['worst']:>+6.0f}% {r['stopped']:>5.0%} "
              f"{r['ctrl']:>+6.1f}% {r['edge']:>+6.1f} {lo:>8} {hi:>8}")

    ok = [r for r in rows if r["n"] >= MIN_EVENTS
          and r["worst"] > WORST_LIMIT and r["edge"] >= EDGE_PP
          and (r["lo"] or 0) > 0 and (r["hi"] or 0) > 0]
    n_max = max((r["n"] for r in rows), default=0)
    if n_max < MIN_EVENTS:
        verdict = f"СУДИТЬ НЕЛЬЗЯ: событий {n_max} при минимуме {MIN_EVENTS}"
    elif ok:
        b = max(ok, key=lambda r: r["edge"])
        verdict = (f"ПОДТВЕРЖДЕНО: {b['h']}д, стоп "
                   f"{'нет' if b['stop'] is None else '-' + str(b['stop']) + '%'}"
                   f", обгон {b['edge']:+.1f} п.п. (половины {b['lo']:+.1f} / "
                   f"{b['hi']:+.1f}), худшая {b['worst']:+.0f}% — кандидат на "
                   f"чекпойнт и отдельную категорию с риском 0.25%")
    else:
        surv = [r for r in rows if r["worst"] > WORST_LIMIT]
        best = max(surv or rows, key=lambda r: r["edge"]) if rows else None
        verdict = ("НЕ ПОДТВЕРЖДЕНО: ни один вариант не прошёл все условия"
                   + (f"; лучший выживающий — {best['h']}д обгон "
                      f"{best['edge']:+.1f} п.п., худшая {best['worst']:+.0f}%"
                      if best else ""))
    print(f"\nВЕРДИКТ: {verdict}")

    # Добавлено ПОСЛЕ первого прогона 27.10 как раскрытие, правило не
    # меняется: разброс исходов огромный (почти половина сделок выбита
    # стопом), и «обгон +4.7» без интервала не говорит, отличим ли он от нуля.
    from src.tactical_backtest import _bootstrap_ci
    print("\n95% интервал средней (бутстрэп) для прошедших вариантов:")
    for r in ok:
        evs = []
        for coin, cs in history.items():
            x = short_return(cs, ENTRY_IDX, r["h"], r["stop"])
            if x is not None:
                evs.append(x[0])
        lo_ci, hi_ci = _bootstrap_ci(evs)
        if lo_ci is None:
            continue
        lab = "нет" if r["stop"] is None else f"-{r['stop']}%"
        print(f"  {r['h']:>2}д стоп {lab:>5}: средняя {r['avg']:+.1f}% "
              f"[{lo_ci:+.1f}, {hi_ci:+.1f}] при контроле {r['ctrl']:+.1f}%")
    print("Без комиссий и фандинга · контроль — шорт тех же монет в случайный "
          "день после 30-го")

    try:
        from src.telegram_sender import muted_sender
        muted_sender("research")([f"🆕 <b>Шорт листингов</b>\n{verdict}"])
    except Exception as e:  # noqa: BLE001
        print(f"[listing] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
