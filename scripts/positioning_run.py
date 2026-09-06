#!/usr/bin/env python3
"""Позиционирование толпы: собрать историю и проверить предсказание.

Раз в сутки: пишет оба среза OKX в журнал и сразу считает, предсказывает
ли позиционирование доходность следующего дня.

Собирается суточная точка, а не часовая: за час позиционирование толпы не
меняется осмысленно, часовые дали бы 24 почти одинаковых числа в день.

КРИТЕРИЙ, ЗАПИСАННЫЙ ДО ПРОГОНА: экстремальная группа должна обгонять «все
дни» минимум на 0.5 п.п. средней доходности следующего дня при n>=30 в
группе. Расчёт ведётся ПО КАЖДОЙ МОНЕТЕ отдельно, а результаты
сравниваются — складывать девять коррелированных монет как независимые
наблюдения нельзя, эту ошибку мы уже делали с китами.
"""
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles  # noqa: E402
from src.positioning import (  # noqa: E402
    OKX_ACCOUNTS, OKX_TOP_POS, evaluate, forward_returns, merge, parse_okx,
)
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

STATE = REPO / "state" / "positioning.jsonl"
MIN_N = 30
EDGE_PP = 0.5
DAY = 86_400_000


def _get(url: str, params: dict):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "hl/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode()).get("data") or []


def main() -> int:
    print("# Позиционирование толпы · суточные точки\n")
    fresh, results = [], {}

    for coin in FOCUS_COINS:
        try:
            acc = parse_okx(_get(OKX_ACCOUNTS,
                                 {"ccy": coin, "period": "1D", "limit": "500"}))
            top = parse_okx(_get(OKX_TOP_POS,
                                 {"instId": f"{coin}-USDT-SWAP",
                                  "period": "1D", "limit": "500"}))
        except Exception as e:  # noqa: BLE001
            print(f"  {coin:8} недоступно: {type(e).__name__}")
            continue
        if not acc and not top:
            print(f"  {coin:8} нет данных на OKX")
            continue

        points = merge(acc, top)
        # Свежую точку — в журнал, чтобы история накапливалась своя.
        if points:
            last = points[-1]
            fresh.append({"ts_ms": last.ts_ms, "coin": coin,
                          "accounts_ratio": last.accounts_ratio,
                          "top_pos_ratio": last.top_pos_ratio})

        try:
            candles = fetch_candles(coin, interval="1d", lookback_days=400)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin:8} свечи недоступны: {e}")
            continue
        prices = {}
        for c in candles or []:
            ts = c.get("t") if c.get("t") is not None else c.get("T")
            if ts is not None and c.get("c"):
                # выравниваем на границу суток, метки источников не совпадают
                prices[int(ts) // DAY * DAY] = float(c["c"])
        aligned = [type(p)(p.ts_ms // DAY * DAY, p.accounts_ratio,
                           p.top_pos_ratio) for p in points]

        pairs = forward_returns(aligned, prices, horizon_d=1)
        sig = evaluate(pairs)
        results[coin] = sig
        print(f"  {coin:8} точек {len(points):>4} · сопоставлено с ценой "
              f"{len(pairs):>4}")

    if fresh:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            with STATE.open("a", encoding="utf-8") as fh:
                for row in fresh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"\nдописано в журнал: {len(fresh)} точек")
        except OSError as e:
            print(f"\nзапись не удалась: {e}")

    if not results:
        print("данных для оценки нет")
        return 1

    # Сводим по монетам: обгон над «все дни» в каждой, затем смотрим,
    # у скольких монет он положителен. Складывать монеты нельзя.
    print(f"\n{'группа':30} {'монет':>6} {'сред. обгон':>12} {'плюсовых':>9}")
    summary = []
    labels = [s.label for s in next(iter(results.values()))]
    for label in labels:
        if label == "все дни":
            continue
        edges, ns = [], []
        for coin, sigs in results.items():
            base = next((s for s in sigs if s.label == "все дни"), None)
            grp = next((s for s in sigs if s.label == label), None)
            if (base and grp and grp.n >= 5 and grp.avg_fwd_pct is not None
                    and base.avg_fwd_pct is not None):
                edges.append(grp.avg_fwd_pct - base.avg_fwd_pct)
                ns.append(grp.n)
        if not edges:
            continue
        pos = sum(1 for e in edges if e > 0) / len(edges)
        summary.append((label, len(edges), statistics.mean(edges), pos,
                        sum(ns)))
        print(f"{label:30} {len(edges):>6} {statistics.mean(edges):>+11.2f}пп "
              f"{pos:>8.0%}")

    best = max(summary, key=lambda x: x[2]) if summary else None
    if not best or best[4] < MIN_N:
        verdict = (f"СУДИТЬ НЕЛЬЗЯ: {best[4] if best else 0} наблюдений "
                   f"при минимуме {MIN_N}")
    elif best[2] >= EDGE_PP and best[3] >= 0.6:
        verdict = (f"ПРИЗНАК: «{best[0]}» даёт {best[2]:+.2f} п.п. "
                   f"у {best[3]:.0%} монет")
    else:
        verdict = (f"не подтверждено: лучший обгон {best[2]:+.2f} п.п. "
                   f"при пороге {EDGE_PP:+.1f}")
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(f"{l:26} {e:+.2f}пп · плюс у {p:.0%} монет"
                         for l, _, e, p, _ in summary)
        send_messages([
            f"👥 <b>Позиционирование толпы</b> · суточно\n"
            f"<pre>{rows}</pre>\n<b>{verdict}</b>\n"
            f"<i>экстремум считается по своей истории монеты · расчёт по "
            f"каждой отдельно</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[pos] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
