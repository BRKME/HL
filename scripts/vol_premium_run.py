#!/usr/bin/env python3
"""Замер премии за волатильность на данных Aevo. Раз в сутки.

Отвечает на вопрос политики по опционам: какая премия перевешивает — за
риск актива или за волатильность.

Чего этот замер НЕ решает: где торговать. Спред на Aevo — 9.9 пункта
волатильности по биткоину, пересечь его туда и обратно дороже всей
задокументированной премии. Здесь выясняется, существует ли преимущество;
где его брать — отдельный вопрос.

КРИТЕРИИ, ЗАПИСАННЫЕ ДО ДАННЫХ:
  — премия считается подтверждённой при средней >= 5 п.п. и положительной
    доле >= 70% при n >= 30 наблюдений;
  — худшее наблюдение хуже -30 п.п. означает, что профиль «малые выигрыши,
    крупные потери» подтверждён: продажа волатильности исключается
    независимо от средней;
  — расхождение двух оценок реализованной волатильности больше чем вдвое
    означает, что данные подозрительны, и вывод не делается.
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

from src.vol_premium import (  # noqa: E402
    PremiumPoint, atm_implied, close_to_close_volatility,
    parkinson_volatility, summarise,
)

BASE = "https://api.aevo.xyz"
STATE = REPO / "state" / "vol_premium.jsonl"
ASSETS = ("BTC", "ETH")
TARGET_DAYS = 30              # экспирация, ближайшая к месяцу
MIN_N = 30
EDGE_PP = 5.0
WORST_LIMIT = -30.0


def _get(path, params=None):
    url = f"{BASE}{path}" + ("?" + urllib.parse.urlencode(params)
                             if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "hl/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _pick_atm(rows, target_days: int):
    """Опцион, ближайший к деньгам и к целевому сроку.

    Вне денег подразумеваемая волатильность искажена улыбкой: сравнивать
    её с реализованной нельзя.
    """
    now = datetime.now(timezone.utc).timestamp() * 1000
    best, best_key = None, None
    for m in rows:
        try:
            spot = float(m.get("index_price") or 0)
            expiry = float(m.get("expiry") or 0)
            name = m.get("instrument_name") or ""
            strike = float(name.split("-")[2])
        except (TypeError, ValueError, IndexError):
            continue
        if spot <= 0 or expiry <= now:
            continue
        days = (expiry - now) / 86_400_000
        key = (abs(days - target_days), abs(strike / spot - 1))
        if best_key is None or key < best_key:
            best, best_key = m, key
    return best


def main() -> int:
    print("# Премия за волатильность · Aevo\n")
    fresh, summary_rows = [], {}

    for asset in ASSETS:
        try:
            rows = _get("/markets", {"asset": asset,
                                     "instrument_type": "OPTION"})
            rows = rows if isinstance(rows, list) else rows.get("data") or []
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: рынки недоступны ({type(e).__name__})")
            continue

        m = _pick_atm([r for r in rows if r.get("is_active")], TARGET_DAYS)
        if not m:
            print(f"  {asset}: подходящего опциона нет")
            continue
        name = m["instrument_name"]

        try:
            ob = _get("/orderbook", {"instrument_name": name})
            iv = atm_implied(ob.get("bids"), ob.get("asks"))
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: стакан недоступен ({type(e).__name__})")
            continue
        if iv is None:
            print(f"  {asset}: подразумеваемой волатильности в стакане нет")
            continue

        try:
            from src.hl_api import fetch_candles
            candles = fetch_candles(asset, interval="1d", lookback_days=90)
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: свечи недоступны ({e})")
            continue

        rv_park = parkinson_volatility(candles, window=TARGET_DAYS)
        rv_close = close_to_close_volatility(candles, window=TARGET_DAYS)
        if rv_park is None:
            print(f"  {asset}: наблюдений мало для оценки реализованной")
            continue

        # Сверка оценок: сильное расхождение означает подозрительные данные.
        suspicious = (rv_close is not None and rv_close > 0
                      and (max(rv_park, rv_close) / min(rv_park, rv_close) > 2))

        print(f"  {asset} ({name})")
        print(f"    подразумеваемая : {iv * 100:>5.1f}%")
        print(f"    реализованная   : {rv_park * 100:>5.1f}% (Паркинсон)"
              + (f" · {rv_close * 100:.1f}% (закрытия)" if rv_close else ""))
        print(f"    премия          : {(iv - rv_park) * 100:>+5.1f} п.п."
              + ("  ⚠️ оценки расходятся вдвое" if suspicious else ""))

        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        fresh.append({"ts_ms": ts, "asset": asset, "instrument": name,
                      "implied": iv, "realized_parkinson": rv_park,
                      "realized_close": rv_close, "horizon_days": TARGET_DAYS,
                      "suspicious": suspicious})
        summary_rows[asset] = (iv - rv_park) * 100

    if fresh:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            with STATE.open("a", encoding="utf-8") as fh:
                for row in fresh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"\nдописано в журнал: {len(fresh)}")
        except OSError as e:
            print(f"\nзапись не удалась: {e}")

    # Накопленная история: вывод делается по ней, а не по одному дню.
    history = []
    if STATE.exists():
        for line in STATE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                history.append(PremiumPoint(
                    ts_ms=r["ts_ms"], asset=r["asset"],
                    implied=float(r["implied"]),
                    realized=float(r["realized_parkinson"]),
                    horizon_days=int(r.get("horizon_days", TARGET_DAYS))))
            except (ValueError, KeyError):
                continue

    s = summarise(history)
    print(f"\nнаблюдений всего: {s['n']}")
    if s["n"]:
        print(f"  средняя премия : {s['mean_pp']:+.1f} п.п.")
        print(f"  медиана        : {s['median_pp']:+.1f} п.п.")
        print(f"  положительных  : {s['positive_share']:.0%}")
        print(f"  худшее         : {s['worst_pp']:+.1f} п.п.")

    if s["n"] < MIN_N:
        verdict = (f"СУДИТЬ НЕЛЬЗЯ: {s['n']} наблюдений при минимуме {MIN_N} "
                   f"— копим")
    elif s["worst_pp"] is not None and s["worst_pp"] < WORST_LIMIT:
        verdict = (f"ПРОФИЛЬ ОПАСЕН: худшее {s['worst_pp']:+.0f} п.п. — "
                   f"малые выигрыши, крупные потери; продажа исключается")
    elif s["mean_pp"] >= EDGE_PP and s["positive_share"] >= 0.70:
        verdict = (f"ПРЕМИЯ ПОДТВЕРЖДЕНА: {s['mean_pp']:+.1f} п.п. "
                   f"у {s['positive_share']:.0%} наблюдений")
    else:
        verdict = (f"не подтверждено: {s['mean_pp']:+.1f} п.п. при пороге "
                   f"{EDGE_PP:+.0f}")
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        today = "\n".join(f"{a}: {v:+.1f} п.п." for a, v in summary_rows.items())
        send_messages([
            f"📐 <b>Премия за волатильность</b> · Aevo\n"
            f"<pre>{today or 'данных за сегодня нет'}</pre>\n"
            f"накоплено {s['n']} наблюдений\n"
            f"<b>{verdict}</b>\n"
            f"<i>торговать на Aevo нельзя: спред 9.9 п.п. по BTC съедает "
            f"премию. Замер отвечает, есть ли преимущество, а не где его "
            f"брать.</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[vol] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
