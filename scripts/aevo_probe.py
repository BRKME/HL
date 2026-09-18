#!/usr/bin/env python3
"""Проверка доступа к Aevo и состава данных по опционам.

Из песочницы Aevo закрыт (403, хост не в списке) — как Hyperliquid, Binance
и Bybit. Писать сбор вслепую нельзя: за последние дни проверки на
подставных данных трижды пропустили то, что вылезло в проде.

Что выясняем ДО написания сбора:
  — отвечает ли API из Actions;
  — сколько опционов реально торгуется по BTC и ETH;
  — есть ли в стакане подразумеваемая волатильность (третье поле уровня);
  — каков спред — он на опционах съедает премию быстрее, чем на перпах;
  — минимальный размер ордера: при счёте оператора это решает, возможна
    ли сделка в принципе.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://api.aevo.xyz"


def _get(path: str, params: dict = None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "hl-probe/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    print("# Aevo: проверка доступа и данных\n")
    ok = True

    # 1. Доступ и список активов
    try:
        assets = _get("/assets")
        print(f"  активы: {assets if len(str(assets)) < 120 else len(assets)}")
    except urllib.error.HTTPError as e:
        print(f"  ДОСТУПА НЕТ: HTTP {e.code}"
              + (" — региональная блокировка" if e.code == 451 else ""))
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"  ДОСТУПА НЕТ: {type(e).__name__}: {e}")
        return 1

    # 2. Сколько опционов торгуется
    markets = {}
    for asset in ("BTC", "ETH"):
        try:
            rows = _get("/markets", {"asset": asset,
                                     "instrument_type": "OPTION"})
        except Exception as e:  # noqa: BLE001
            print(f"  {asset}: рынки недоступны ({type(e).__name__})")
            ok = False
            continue
        rows = rows if isinstance(rows, list) else rows.get("data") or []
        active = [m for m in rows if m.get("is_active")]
        markets[asset] = active
        expiries = Counter(str(m.get("expiry"))[:10] for m in active)
        print(f"\n  {asset}: инструментов {len(rows)}, активных {len(active)}")
        print(f"    экспираций: {len(expiries)}")
        if active:
            print(f"    поля инструмента: {sorted(active[0].keys())[:10]}")

    # 3. Стакан: есть ли подразумеваемая волатильность и каков спред
    print("\n  === СТАКАН ===")
    for asset, rows in markets.items():
        if not rows:
            continue
        # ближайший к деньгам колл
        name = rows[len(rows) // 2].get("instrument_name")
        try:
            ob = _get("/orderbook", {"instrument_name": name})
        except Exception as e:  # noqa: BLE001
            print(f"    {name}: стакан недоступен ({type(e).__name__})")
            ok = False
            continue
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        print(f"    {name}: заявок {len(bids)}/{len(asks)}")
        if bids and asks:
            b, a = bids[0], asks[0]
            print(f"      лучший бид {b} · аск {a}")
            has_iv = len(b) >= 3
            print(f"      IV в стакане: {'ЕСТЬ' if has_iv else 'НЕТ'}")
            try:
                bp, ap = float(b[0]), float(a[0])
                mid = (bp + ap) / 2
                print(f"      спред {(ap - bp) / mid * 100:.1f}% от середины")
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            if not has_iv:
                ok = False
        else:
            print("      стакан пуст — торговать нечем")

    # 4. Минимальный размер: решает, возможна ли сделка в принципе
    print("\n  === МИНИМАЛЬНЫЙ ОРДЕР ===")
    for asset, rows in markets.items():
        if not rows:
            continue
        name = rows[len(rows) // 2].get("instrument_name")
        try:
            meta = _get("/instrument", {"instrument_name": name})
            keys = ("min_order_value", "amount_step", "tick_size",
                    "min_order_size")
            found = {k: meta.get(k) for k in keys if meta.get(k) is not None}
            print(f"    {name}: {found or 'полей о минимуме нет'}")
        except Exception as e:  # noqa: BLE001
            print(f"    {name}: метаданные недоступны ({type(e).__name__})")

    print(f"\nИТОГ: {'данные пригодны для замера' if ok else 'есть пробелы'}")

    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__)
                              .resolve().parents[1]))
        from src.telegram_sender import send_messages
        total = sum(len(v) for v in markets.values())
        send_messages([f"🔌 <b>Проверка Aevo</b>: доступ есть, "
                       f"активных опционов {total}\n"
                       f"<i>{'данные пригодны' if ok else 'есть пробелы, см. лог'}</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[probe] отправка не удалась: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
