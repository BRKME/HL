#!/usr/bin/env python3
"""Set or clear a manual hold. Driven by .github/workflows/manual-hold.yml.

Env: COIN (required), ACTION ("set" | "clear"), NOTE (optional).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.manual_hold import clear_hold, load_holds, set_hold  # noqa: E402

STATE_DIR = Path("state")


def main() -> int:
    coin = (os.environ.get("COIN") or "").strip().upper()
    action = (os.environ.get("ACTION") or "set").strip().lower()
    note = (os.environ.get("NOTE") or "").strip()

    if not coin:
        print("COIN не задан", file=sys.stderr)
        return 1
    if action not in ("set", "clear"):
        print(f"неизвестное действие: {action}", file=sys.stderr)
        return 1

    if action == "set":
        set_hold(coin, datetime.now(timezone.utc), note, STATE_DIR)
        print(f"{coin}: флаг «держу против системы» поставлен"
              + (f" — {note}" if note else ""))
    else:
        clear_hold(coin, STATE_DIR)
        print(f"{coin}: флаг снят")

    holds = load_holds(STATE_DIR)
    print("активные флаги:", ", ".join(sorted(holds)) or "нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
