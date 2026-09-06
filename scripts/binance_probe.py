#!/usr/bin/env python3
"""Разовая проверка: доступен ли Binance из Actions и в каком виде данные.

Позиционирование толпы — единственный класс данных, которого у нас нет:
цены, свечи, объёмы и сделки китов доступны всем и всеми обработаны.
Hyperliquid таких срезов не отдаёт вовсе, их даёт Binance тремя методами.

Из песочницы Binance недоступен (403, хост не в списке), поэтому написать
сбор вслепую и надеяться нельзя: за последние дни проверки на подставных
данных трижды пропустили то, что вылезло в проде. Сначала смотрим живой
ответ, потом пишем сбор.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "https://fapi.binance.com/futures/data"
METHODS = {
    "все счета": "globalLongShortAccountRatio",
    "топ-счета": "topLongShortAccountRatio",
    "топ-позиции": "topLongShortPositionRatio",
}
SYMBOLS = ("BTCUSDT", "ETHUSDT", "ZECUSDT", "NEARUSDT")


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "hl-probe/1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ok = True
    print("# Проверка доступа к Binance\n")

    for label, path in METHODS.items():
        url = f"{BASE}/{path}?symbol=BTCUSDT&period=1h&limit=3"
        try:
            data = _get(url)
        except urllib.error.HTTPError as e:
            print(f"  {label:14} HTTP {e.code}: {e.reason}")
            ok = False
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {label:14} недоступно: {type(e).__name__}: {e}")
            ok = False
            continue
        if not data:
            print(f"  {label:14} пустой ответ")
            ok = False
            continue
        row = data[-1]
        print(f"  {label:14} OK · записей {len(data)} · поля "
              f"{sorted(row.keys())}")
        print(f"                 пример: long {row.get('longAccount')} "
              f"short {row.get('shortAccount')}")

    print("\nдоступность символов (какие монеты есть на Binance):")
    for s in SYMBOLS:
        try:
            d = _get(f"{BASE}/{METHODS['все счета']}?symbol={s}"
                     f"&period=1h&limit=1")
            print(f"  {s:10} {'есть' if d else 'пусто'}")
        except Exception as e:  # noqa: BLE001
            print(f"  {s:10} нет ({type(e).__name__})")

    # Глубина истории: заявлено 30 дней, проверяем фактическую.
    try:
        d = _get(f"{BASE}/{METHODS['все счета']}?symbol=BTCUSDT"
                 f"&period=1h&limit=500")
        if d:
            import datetime as dt
            t0 = dt.datetime.fromtimestamp(int(d[0]["timestamp"]) / 1000,
                                           dt.UTC)
            t1 = dt.datetime.fromtimestamp(int(d[-1]["timestamp"]) / 1000,
                                           dt.UTC)
            print(f"\nглубина при limit=500: {len(d)} точек, "
                  f"{t0.isoformat()[:16]} → {t1.isoformat()[:16]} "
                  f"({(t1 - t0).days} дн)")
    except Exception as e:  # noqa: BLE001
        print(f"\nглубину проверить не удалось: {e}")

    print(f"\nИТОГ: {'доступ есть, можно писать сбор' if ok else 'доступа нет'}")

    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__)
                              .resolve().parents[1]))
        from src.telegram_sender import send_messages
        send_messages([f"🔌 <b>Проверка Binance</b>: "
                       f"{'доступ есть' if ok else 'ДОСТУПА НЕТ'}"])
    except Exception as e:  # noqa: BLE001
        print(f"[probe] отправка не удалась: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
