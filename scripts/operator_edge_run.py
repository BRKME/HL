#!/usr/bin/env python3
"""Разбор торговли оператора. Запускается вручную.

Единственный источник преимущества, который мы наблюдали за месяц, — это
сам оператор: +46.8% руками, пока модель не обгоняла случайный вход.
Измеряем это теми же мерками, что применяли к модели, включая тот же порог
по числу сделок. Двойного стандарта быть не должно.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_client import HLClient  # noqa: E402
from src.operator_edge import pair_fills, summarise, verdict  # noqa: E402

LOOKBACK_DAYS = int(os.environ.get("EDGE_LOOKBACK_DAYS") or 180)


def _addresses() -> list[str]:
    raw = os.environ.get("HL_ACCOUNTS") or os.environ.get("HL_ADDRESS") or ""
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def main() -> int:
    addrs = _addresses()
    if not addrs:
        print("адреса не заданы (HL_ACCOUNTS)")
        return 1

    start = int((datetime.now(timezone.utc)
                 - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000)
    client = HLClient()
    fills = []
    for a in addrs:
        try:
            fills.extend(client.get_user_fills_by_time(a, start))
        except Exception as e:  # noqa: BLE001
            print(f"  {a[:10]}: {e}")

    trades = pair_fills(fills)
    s = summarise(trades)
    print(f"# Торговля оператора · {LOOKBACK_DAYS} дней\n")
    print(f"исполнений: {len(fills)} · закрытых сделок: {s['n']}")
    if not s["n"]:
        print("сделок не восстановлено")
        return 0

    print(f"WR                  : {s['wr']:.0%}")
    print(f"средняя доходность  : {s['avg_return_pct']:+.2f}%")
    print(f"медиана             : {s['median_return_pct']:+.2f}%")
    print(f"суммарный PnL       : ${s['total_pnl_usd']:+.2f}")
    print(f"среднее удержание   : {s['avg_hold_days']:.1f} дн")
    print("\nпо монетам:")
    for coin, d in sorted(s["by_coin"].items(),
                          key=lambda kv: -kv[1]["pnl"]):
        print(f"  {coin:8} сделок {d['n']:>3} · PnL ${d['pnl']:+.2f}")
    print(f"\nВЫВОД: {verdict(s)}")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(f"{c:8} n={d['n']:>3} PnL ${d['pnl']:+.0f}"
                         for c, d in sorted(s["by_coin"].items(),
                                            key=lambda kv: -kv[1]["pnl"])[:8])
        send_messages([
            f"👤 <b>Торговля оператора</b> — {LOOKBACK_DAYS} дн\n"
            f"сделок {s['n']} · WR {s['wr']:.0%} · средняя "
            f"{s['avg_return_pct']:+.2f}% · PnL ${s['total_pnl_usd']:+.0f}\n"
            f"<pre>{rows}</pre>\n"
            f"<b>{verdict(s)}</b>"])
    except Exception as e:  # noqa: BLE001
        print(f"[edge] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
