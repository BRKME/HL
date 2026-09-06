#!/usr/bin/env python3
"""Проверка доступа к позиционированию толпы: Bybit и OKX.

Binance ответил 451 — региональная блокировка, раннеры Actions находятся в
США. Это не чинится настройками.

Bybit по документации отдаёт доли счетов с 20 июля 2020 года — шесть лет
против тридцати дней у Binance. Это меняет дело: идею можно проверить
СЕГОДНЯ тем же аппаратом, что и всё остальное, а не копить месяц.

OKX даёт вдобавок разбивку по топ-трейдерам — тот срез, которого у Bybit
нет, а в статистике оператора он был самым интересным: по счетам
топ-трейдеры были в шорте на 76%, а по размеру позиций почти нейтральны.

Проверяются доступ, поля и ФАКТИЧЕСКАЯ глубина: заявленное в документации
и отданное на практике — разные вещи.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCES = {
    "bybit / счета": (
        "https://api.bybit.com/v5/market/account-ratio",
        {"category": "linear", "symbol": "BTCUSDT", "period": "1h",
         "limit": "50"},
    ),
    "okx / счета": (
        "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
        {"ccy": "BTC", "period": "1H"},
    ),
    "okx / топ-позиции": (
        "https://www.okx.com/api/v5/rubik/stat/contracts/"
        "long-short-position-ratio-contract-top-trader",
        {"instId": "BTC-USDT-SWAP", "period": "1H"},
    ),
}


def _get(url: str, params: dict):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "hl-probe/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    print("# Позиционирование толпы: проверка источников\n")
    available = []

    for label, (url, params) in SOURCES.items():
        try:
            data = _get(url, params)
        except urllib.error.HTTPError as e:
            print(f"  {label:20} HTTP {e.code} — "
                  f"{'региональная блокировка' if e.code == 451 else e.reason}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {label:20} недоступно: {type(e).__name__}")
            continue

        rows = (data.get("result", {}).get("list")
                if isinstance(data.get("result"), dict) else data.get("data"))
        if not rows:
            print(f"  {label:20} пустой ответ: {str(data)[:90]}")
            continue
        print(f"  {label:20} OK · записей {len(rows)} · поля "
              f"{sorted(rows[0].keys()) if isinstance(rows[0], dict) else 'список'}")
        available.append(label)

    # ФАКТИЧЕСКАЯ глубина. Первая версия пробы проверяла её только для
    # Bybit, потому что я ожидал, что сработает именно он. Bybit не
    # ответил, а про OKX выяснилось, что глубина неизвестна — проверка
    # была написана под ожидаемый исход, а не под возможные.
    if any(a.startswith("bybit") for a in available):
        for years_back in (1, 3, 6):
            start = datetime.now(timezone.utc) - timedelta(days=365 * years_back)
            try:
                d = _get(SOURCES["bybit / счета"][0], {
                    "category": "linear", "symbol": "BTCUSDT", "period": "1d",
                    "limit": "50",
                    "startTime": str(int(start.timestamp() * 1000)),
                    "endTime": str(int((start + timedelta(days=40))
                                       .timestamp() * 1000)),
                })
                rows = d.get("result", {}).get("list") or []
                got = (datetime.fromtimestamp(int(rows[-1]["timestamp"]) / 1000,
                                              timezone.utc).date()
                       if rows else None)
                print(f"\n  bybit, {years_back} г назад: "
                      f"{len(rows)} записей" + (f", ранняя {got}" if got else ""))
            except Exception as e:  # noqa: BLE001
                print(f"\n  bybit, {years_back} г назад: ошибка {e}")

    if any(a.startswith("okx") for a in available):
        print("\nглубина OKX:")
        for label, url, params in (
            ("счета", SOURCES["окx / счета"][0] if False
             else SOURCES["okx / счета"][0],
             {"ccy": "BTC", "period": "1D", "limit": "500"}),
            ("топ-позиции", SOURCES["okx / топ-позиции"][0],
             {"instId": "BTC-USDT-SWAP", "period": "1D", "limit": "500"}),
        ):
            try:
                d = _get(url, params)
                rows = d.get("data") or []
                if not rows:
                    print(f"  {label:14} пусто")
                    continue
                # OKX отдаёт массивы [ts, ...]; берём крайние метки времени
                stamps = []
                for r in rows:
                    ts = r[0] if isinstance(r, list) else r.get("ts")
                    if ts:
                        stamps.append(int(ts))
                if not stamps:
                    print(f"  {label:14} метки времени не распознаны: "
                          f"{str(rows[0])[:70]}")
                    continue
                a = datetime.fromtimestamp(min(stamps) / 1000, timezone.utc)
                b = datetime.fromtimestamp(max(stamps) / 1000, timezone.utc)
                print(f"  {label:14} {len(rows)} записей · {a.date()} → "
                      f"{b.date()} ({(b - a).days} дн)")
                print(f"                 формат записи: {str(rows[0])[:70]}")
            except Exception as e:  # noqa: BLE001
                print(f"  {label:14} ошибка: {e}")

    print(f"\nИТОГ: доступно источников {len(available)} из {len(SOURCES)}")
    if available:
        print("  " + ", ".join(available))

    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__)
                              .resolve().parents[1]))
        from src.telegram_sender import send_messages
        send_messages([
            f"🔌 <b>Позиционирование: источники</b>\n"
            f"доступно {len(available)} из {len(SOURCES)}\n"
            + ("<pre>" + "\n".join(available) + "</pre>" if available
               else "ни один источник не отвечает")])
    except Exception as e:  # noqa: BLE001
        print(f"[probe] отправка не удалась: {e}")
    return 0 if available else 1


if __name__ == "__main__":
    raise SystemExit(main())
