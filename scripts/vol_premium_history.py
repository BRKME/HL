#!/usr/bin/env python3
"""Премия за волатильность по ИСТОРИИ, а не накоплением.

Замечание оператора 19.09: «там же есть вся история, зачем делать текущие
замеры». Верно — и это та же ошибка, что с перпами, где мы копили по 28
сделок в месяц, пока история отвечала за вечер.

Deribit публикует индекс DVOL — ожидаемую волатильность на 30 дней вперёд,
аналог VIX. `public/get_volatility_index_data` отдаёт его свечами с
постраничной выборкой; история с 2021 года. Это около пяти лет вместо
тридцати дней накопления.

ЧТО ИСПРАВЛЕНО ПО ПУТИ. Премия есть подразумеваемая СЕГОДНЯ против
реализованной в СЛЕДУЮЩИЕ тридцать дней. Первая версия сравнивала с
реализованной за ПРОШЕДШИЕ тридцать — другая величина, отвечающая на
вопрос «была ли волатильность выше ожиданий вчера». Ошибка была бы
невидима: числа выглядели бы правдоподобно и накопились бы за месяц.

КРИТЕРИИ, ЗАПИСАННЫЕ ДО ДАННЫХ:
  — премия подтверждена при средней >= 5 п.п. и положительной доле >= 70%
    при n >= 200 наблюдений (история позволяет требовать больше, чем 30);
  — худшее наблюдение хуже -30 п.п. означает профиль «малые выигрыши,
    крупные потери»: продажа исключается независимо от средней;
  — если премия положительна в спокойные периоды и отрицательна в бурные,
    это НЕ преимущество, а плата за страховку — и торговать её нельзя.
"""
import json
import os
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.vol_premium import build_history, summarise  # noqa: E402

DERIBIT = "https://www.deribit.com/api/v2/public"
ASSETS = ("BTC", "ETH")
YEARS = int(os.environ.get("VOL_YEARS") or 5)
HORIZON_DAYS = 30
MIN_N = 200
EDGE_PP = 5.0
WORST_LIMIT = -30.0


def _dvol(currency: str, years: int) -> list:
    """История DVOL с постраничной выборкой.

    Ответ содержит continuation — без него вернётся только первый кусок, и
    «пять лет» окажутся неделей.
    """
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc)
                 - timedelta(days=365 * years)).timestamp() * 1000)
    rows, guard = [], 0
    while guard < 60:
        guard += 1
        url = (f"{DERIBIT}/get_volatility_index_data?currency={currency}"
               f"&start_timestamp={start}&end_timestamp={end}&resolution=43200")
        req = urllib.request.Request(url, headers={"User-Agent": "hl/1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
        result = payload.get("result") or {}
        chunk = result.get("data") or []
        if not chunk:
            break
        rows.extend(chunk)
        cont = result.get("continuation")
        if not cont:
            break
        end = int(cont)
    return rows


def main() -> int:
    print(f"# Премия за волатильность · история {YEARS} лет\n")
    all_points = {}

    for asset in ASSETS:
        try:
            dvol = _dvol(asset, YEARS)
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: DVOL недоступен ({type(e).__name__}: {e})")
            continue
        if not dvol:
            print(f"  {asset}: DVOL пуст")
            continue

        try:
            from src.hl_api import fetch_candles
            candles = fetch_candles(asset, interval="1d",
                                    lookback_days=365 * YEARS + 60)
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: свечи недоступны ({e})")
            continue

        pts = build_history(dvol, candles, asset, HORIZON_DAYS)
        all_points[asset] = pts
        s = summarise(pts)
        print(f"  {asset}: DVOL {len(dvol)} точек, свечей {len(candles)}, "
              f"сопоставлено {s['n']}")
        if s["n"]:
            print(f"    средняя {s['mean_pp']:+.1f} п.п. · "
                  f"медиана {s['median_pp']:+.1f} · "
                  f"положительных {s['positive_share']:.0%} · "
                  f"худшее {s['worst_pp']:+.0f}")

    merged = [p for pts in all_points.values() for p in pts]
    s = summarise(merged)
    if not s["n"]:
        print("\nсопоставить не удалось")
        return 1

    # Режимы: премия может быть платой за страховку, а не преимуществом.
    calm = [p for p in merged if p.realized < statistics.median(
        [q.realized for q in merged])]
    wild = [p for p in merged if p not in calm]
    s_calm, s_wild = summarise(calm), summarise(wild)
    print(f"\n  в спокойные периоды: {s_calm['mean_pp']:+.1f} п.п. "
          f"(n={s_calm['n']})")
    print(f"  в бурные периоды   : {s_wild['mean_pp']:+.1f} п.п. "
          f"(n={s_wild['n']})")

    insurance = (s_calm["mean_pp"] or 0) > 0 > (s_wild["mean_pp"] or 0)

    if s["n"] < MIN_N:
        verdict = f"СУДИТЬ НЕЛЬЗЯ: {s['n']} наблюдений при минимуме {MIN_N}"
    elif s["worst_pp"] < WORST_LIMIT:
        verdict = (f"ПРОФИЛЬ ОПАСЕН: худшее {s['worst_pp']:+.0f} п.п. — "
                   f"продажа исключается независимо от средней")
    elif insurance:
        verdict = ("ЭТО ПЛАТА ЗА СТРАХОВКУ, а не преимущество: премия "
                   "положительна в спокойствии и отрицательна в буре")
    elif s["mean_pp"] >= EDGE_PP and s["positive_share"] >= 0.70:
        verdict = (f"ПРЕМИЯ ПОДТВЕРЖДЕНА: {s['mean_pp']:+.1f} п.п. "
                   f"у {s['positive_share']:.0%} наблюдений")
    else:
        verdict = (f"не подтверждено: {s['mean_pp']:+.1f} п.п. при пороге "
                   f"{EDGE_PP:+.0f}")
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(
            f"{a}: {summarise(p)['mean_pp']:+.1f} п.п. (n={summarise(p)['n']})"
            for a, p in all_points.items() if summarise(p)["n"])
        send_messages([
            f"📐 <b>Премия за волатильность</b> · история {YEARS} лет\n"
            f"<pre>{rows}</pre>\n"
            f"спокойно {s_calm['mean_pp']:+.1f} · буря {s_wild['mean_pp']:+.1f}"
            f" · худшее {s['worst_pp']:+.0f}\n"
            f"<b>{verdict}</b>\n"
            f"<i>подразумеваемая сегодня против реализованной в следующие "
            f"{HORIZON_DAYS} дней</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[vol] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
