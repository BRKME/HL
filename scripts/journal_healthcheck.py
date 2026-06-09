#!/usr/bin/env python3
"""Journal healthcheck — quick diagnostic of state/verdict_journal.jsonl.

Run from repo root: `python3 scripts/journal_healthcheck.py`

Designed for the 23 June / 30 June checkpoints. Answers:
- Are entries accumulating daily?
- Is verdict_raw populated (post 9 June fix)?
- Is rs_30d / rs_90d populated (post 16 June)?
- What phases have been observed (variance for regime analysis)?
- Verdict distribution per coin (any source bias)?
- raw vs final disagreements (does regime change verdicts?)

Output is plain text — meant to be eyeballed.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_entries(path: Path) -> list[dict]:
    entries = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    journal_path = repo_root / "state" / "verdict_journal.jsonl"
    entries = load_entries(journal_path)

    print(f"# Journal Healthcheck — {datetime.now(timezone.utc).isoformat()}")
    print(f"Path: {journal_path}")
    print()

    if not entries:
        print("EMPTY — no entries. Bot is not writing.")
        return 1

    print(f"Total entries: {len(entries)}")

    # --- Time coverage ---
    ts_list = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_list.append(ts)
        except (ValueError, TypeError, KeyError):
            continue

    if ts_list:
        first = min(ts_list)
        last = max(ts_list)
        span_days = (last - first).days
        print(f"Span: {first.date()} → {last.date()}  ({span_days} days)")

        # Daily coverage — count entries per day, find gaps
        per_day: dict = Counter(t.date() for t in ts_list)
        all_days = [(first + timedelta(days=i)).date()
                    for i in range(span_days + 1)]
        gaps = [d for d in all_days if d not in per_day]
        print(f"Days with entries: {len(per_day)} / {len(all_days)}")
        if gaps:
            print(f"GAPS (days with no entries): {len(gaps)}")
            for d in gaps[:10]:
                print(f"  - {d}")
            if len(gaps) > 10:
                print(f"  ... and {len(gaps) - 10} more")
        else:
            print("No gaps — daily coverage complete.")

    print()

    # --- Sources ---
    print("By source:")
    for src, n in Counter(e.get("source") for e in entries).most_common():
        print(f"  {src}: {n}")
    print()

    # --- Per-coin verdict distribution ---
    print("Verdicts per coin (final):")
    matrix = defaultdict(Counter)
    for e in entries:
        matrix[e.get("coin")][e.get("verdict")] += 1
    print(f"  {'coin':<6} {'LONG':>5} {'SHORT':>5} {'WAIT':>5} {'NODATA':>6}")
    for c in sorted(matrix.keys()):
        m = matrix[c]
        print(f"  {c:<6} {m.get('LONG', 0):>5} {m.get('SHORT', 0):>5} "
              f"{m.get('WAIT', 0):>5} {m.get('NODATA', 0):>6}")
    print()

    # --- Phase variance ---
    print("Regime/phase observed:")
    phase_counts = Counter((e.get("regime"), e.get("phase")) for e in entries)
    for (r, p), n in phase_counts.most_common():
        print(f"  {r} · {p}: {n}")
    if len(phase_counts) == 1:
        print("  ⚠️  Only one phase observed — no variance for regime backtest")
    print()

    # --- verdict_raw coverage (post 9 June fix) ---
    raw_recorded = sum(1 for e in entries if e.get("verdict_raw"))
    print(f"verdict_raw recorded: {raw_recorded} / {len(entries)} "
          f"({raw_recorded/len(entries)*100:.0f}%)")
    if raw_recorded:
        disagree = sum(1 for e in entries
                       if e.get("verdict_raw")
                       and e.get("verdict_raw") != e.get("verdict"))
        print(f"  raw vs final disagreements: {disagree} "
              f"({disagree/raw_recorded*100:.0f}%)")
        # When disagreement happens, what does regime do?
        regime_blocks = sum(1 for e in entries
                            if e.get("verdict_raw") in ("LONG", "SHORT")
                            and e.get("verdict") == "WAIT")
        regime_enables = sum(1 for e in entries
                             if e.get("verdict_raw") == "WAIT"
                             and e.get("verdict") in ("LONG", "SHORT"))
        print(f"  regime blocks raw entry: {regime_blocks}")
        print(f"  regime enables (raw=WAIT → LONG/SHORT): {regime_enables}")
    else:
        print("  ⚠️  No raw verdicts yet — fix from 9 June not active")
    print()

    # --- RS coverage (post 16 June) ---
    rs_30_count = sum(1 for e in entries if e.get("rs_30d") is not None)
    rs_90_count = sum(1 for e in entries if e.get("rs_90d") is not None)
    print(f"RS_30d recorded: {rs_30_count} / {len(entries)} "
          f"({rs_30_count/len(entries)*100:.0f}%)")
    print(f"RS_90d recorded: {rs_90_count} / {len(entries)} "
          f"({rs_90_count/len(entries)*100:.0f}%)")
    if rs_30_count == 0:
        print("  ⚠️  No RS yet — fix from 16 June not active")
    else:
        # RS distribution per coin
        print("\n  Mean RS_30d per coin:")
        rs_by_coin = defaultdict(list)
        for e in entries:
            r = e.get("rs_30d")
            if r is not None:
                rs_by_coin[e.get("coin")].append(r)
        for c in sorted(rs_by_coin.keys()):
            vals = rs_by_coin[c]
            mean = sum(vals) / len(vals)
            print(f"    {c:<6} mean={mean:+6.1f}pp  n={len(vals)}  "
                  f"min={min(vals):+6.1f}  max={max(vals):+6.1f}")
    print()

    # --- Health summary ---
    print("=" * 60)
    print("HEALTH SUMMARY")
    issues = []
    if raw_recorded == 0:
        issues.append("verdict_raw never recorded (9 June fix not working)")
    if rs_30_count == 0:
        issues.append("RS never recorded (16 June fix not working)")
    if len(phase_counts) == 1:
        issues.append("Only one phase observed — can't compare regime impact")
    if ts_list and (datetime.now(timezone.utc) - max(ts_list)).days > 2:
        issues.append(f"Last entry is {(datetime.now(timezone.utc) - max(ts_list)).days} days old — bot may have stopped writing")

    if not issues:
        print("OK — no critical issues detected")
    else:
        for i in issues:
            print(f"  ⚠️  {i}")
    print()
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
