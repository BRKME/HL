"""Whale monitor entry point — runs every 4 hours.

Pipeline:
  1. fetch_leaderboard → pick_candidates → top N whales
  2. For each candidate: incremental fetch_whale_fills (cursor-aware)
  3. Append new fills to state/whale_fills.jsonl (rotate by month, prune 90d)
  4. Score every whale that produced new fills (JSONL primary, API fallback)
  5. Load user's wallets + positions (same as daily_monitor) for OVERLAP
  6. detect_all() → signals
  7. Logging-only: append signals to state/whale_signals.jsonl
     (Telegram comes after Phase 2 observation week)

Designed to fail gracefully: one whale's fetch error doesn't kill the run;
leaderboard outage just skips this run; HL outage doesn't crash anything.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from src.daily_monitor import load_accounts, _build_portfolio
from src.hl_client import HLClient
from src.telegram_sender import send_messages
from src.whale_correlation import (
    CorrelationConfig,
    Signal,
    detect_all,
)
from src.whale_report import render_digest, render_instant_alerts, split_by_mode
from src.whale_scoring import score_whale, WhaleScore
from src.whale_source import (
    CandidateFilters,
    WhaleSourceError,
    fetch_leaderboard,
    pick_candidates,
)
from src.whale_tracker import (
    FillCursor,
    cleanup_old_archives,
    fetch_whale_fills,
    load_cursor,
    rotate_if_month_changed,
    save_cursor,
    write_fills,
)


logger = logging.getLogger("whale_monitor")

# Per-run pacing: leaderboard returns lots of whales; we don't want to hammer HL.
_INTER_WHALE_SLEEP_SEC = 0.15
_DEDUP_TTL_HOURS = 24
_DIGEST_INTERVAL_HOURS = 24


# ---------------------------------------------------------- seen-signals dedup

@dataclass
class SeenSignals:
    """Persisted (rule, whale, coin, ts) tuples within the dedup window."""
    entries: list[dict] = field(default_factory=list)

    @property
    def recent(self) -> set[tuple[str, str, str]]:
        """Just the (rule, whale, coin) keys, for fast lookup."""
        return {(e["rule"], e.get("whale", ""), e["coin"]) for e in self.entries}


def load_seen_signals(path: Path, now: datetime) -> SeenSignals:
    """Load and prune entries older than 24h. Missing/corrupt -> empty."""
    path = Path(path)
    if not path.exists():
        return SeenSignals()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SeenSignals()
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return SeenSignals()
    cutoff = int((now - timedelta(hours=_DEDUP_TTL_HOURS)).timestamp())
    fresh = [e for e in entries if isinstance(e, dict) and e.get("ts", 0) >= cutoff]
    return SeenSignals(entries=fresh)


def save_seen_signals(state: SeenSignals, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": state.entries}, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- whitelist

def load_whitelist_coins(path: Path) -> set[str]:
    """Extract HL coin names from whitelist.yaml tokens section.

    Prefers hl_symbol over the YAML key (e.g. PEPE key, hl_symbol: kPEPE).
    Skips tokens with hl_symbol: null (not listed on HL).
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, OSError):
        return set()
    tokens = data.get("tokens") or {}
    out: set[str] = set()
    for key, val in tokens.items():
        if isinstance(val, dict):
            hl = val.get("hl_symbol")
            if hl is None:
                continue
            out.add(str(hl))
        else:
            out.add(str(key))
    return out


# ---------------------------------------------------------- signals log

def append_signals_log(signals: list[Signal], path: Path, run_ts: datetime) -> None:
    """Append each signal as one JSONL line with run_ts for time alignment."""
    if not signals:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_iso = run_ts.isoformat()
    with path.open("a", encoding="utf-8") as fh:
        for s in signals:
            fh.write(json.dumps({
                "run_ts": run_iso,
                "rule": s.rule,
                "severity": s.severity,
                "coin": s.coin,
                "message": s.message,
                "details": s.details,
            }, separators=(",", ":")) + "\n")


# -------------------------------------------------------- run metadata

def _write_run_meta(path: Path, now: datetime, **extra) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_run_ts": now.isoformat(), **extra}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ main

def run_whale_monitor(
    repo_root: Path,
    now: Optional[datetime] = None,
    top_n: int = 50,
) -> None:
    """One pass through the whale tracker pipeline.

    Never raises on operational failures (HL outages, GitHub issues, etc).
    Programmer errors still surface.
    """
    now = now or datetime.now(timezone.utc)
    repo_root = Path(repo_root)
    state_dir = repo_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    whitelist_path = repo_root / "whitelist.yaml"

    cursor_path = state_dir / "whale_cursor.json"
    fills_path = state_dir / "whale_fills.jsonl"
    signals_path = state_dir / "whale_signals.jsonl"
    seen_path = state_dir / "whale_seen.json"
    meta_path = state_dir / "whale_run_meta.json"
    pending_path = state_dir / "whale_pending_info.jsonl"
    last_digest_path = state_dir / "whale_last_digest.json"

    # Digest first: it must run even on quiet days (no new fills / no candidates).
    _maybe_flush_digest(pending_path, last_digest_path, now=now)

    # rotate + prune old archives BEFORE writing new fills
    rotated = rotate_if_month_changed(fills_path, now=now)
    if rotated:
        logger.info("rotated to %s", rotated.name)
    removed = cleanup_old_archives(state_dir, retention_days=90, now=now)
    if removed:
        logger.info("pruned %d old archives", len(removed))

    # ---- 1. leaderboard -> candidates
    try:
        all_candidates = fetch_leaderboard()
    except WhaleSourceError as e:
        logger.warning("leaderboard fetch failed: %s — skipping this run", e)
        _write_run_meta(meta_path, now, candidate_count=0, status="leaderboard_failed")
        return

    candidates = pick_candidates(all_candidates, CandidateFilters(top_n=top_n))
    logger.info("picked %d candidates from %d leaderboard rows",
                len(candidates), len(all_candidates))

    if not candidates:
        _write_run_meta(meta_path, now, candidate_count=0, status="no_candidates")
        return

    # ---- 2-3. incremental fills per whale
    client = HLClient()
    cursor = load_cursor(cursor_path)

    total_new_fills = 0
    whales_with_new_fills: set[str] = set()
    for c in candidates:
        new = fetch_whale_fills(client, c.address, cursor, now=now)
        if new:
            write_fills(new, fills_path)
            total_new_fills += len(new)
            whales_with_new_fills.add(c.address)
        time.sleep(_INTER_WHALE_SLEEP_SEC)
    save_cursor(cursor, cursor_path)
    logger.info("collected %d new fills across %d whales",
                total_new_fills, len(whales_with_new_fills))

    if total_new_fills == 0:
        _write_run_meta(meta_path, now,
                        candidate_count=len(candidates),
                        new_fills=0, status="no_new_fills")
        return

    # ---- 4. score every whale that produced new fills
    scores: dict[str, WhaleScore] = {}
    for whale_addr in whales_with_new_fills:
        try:
            scores[whale_addr] = score_whale(
                whale_addr, state_dir=state_dir, client=client, now=now,
            )
        except Exception as e:
            logger.warning("scoring failed for %s: %s", whale_addr[:10], e)

    # ---- 5. user portfolio for OVERLAP
    user_positions = []
    try:
        accounts = load_accounts(whitelist_path)
        if accounts:
            portfolio = _build_portfolio(client, accounts)
            user_positions = portfolio.perp
    except Exception as e:
        logger.warning("user portfolio fetch failed (OVERLAP disabled): %s", e)

    # ---- 6. correlation
    whitelist_coins = load_whitelist_coins(whitelist_path)
    seen = load_seen_signals(seen_path, now=now)

    # We need the *recent* fills (since cursor advanced) — re-read just the
    # tail of whale_fills.jsonl that we just wrote. Cheaper than re-reading
    # everything: keep an in-memory list of what was just appended.
    recent_fills = _gather_recent_fills(state_dir, now=now, hours=4)

    signals = detect_all(
        fills=recent_fills,
        scores=scores,
        user_positions=user_positions,
        whitelist=whitelist_coins,
        config=CorrelationConfig(),
        seen_signals=seen.recent,
    )

    # ---- 7. Telegram delivery
    #    warn/critical -> instant (every run, if any)
    #    info          -> accumulate in state/whale_pending_info.jsonl,
    #                     flush as digest when last digest > 24h ago
    if signals:
        append_signals_log(signals, signals_path, run_ts=now)
        ts_int = int(now.timestamp())
        for s in signals:
            seen.entries.append({
                "rule": s.rule,
                "whale": s.details.get("whale", ""),
                "coin": s.coin,
                "ts": ts_int,
            })
        save_seen_signals(seen, seen_path)

    pending_path = state_dir / "whale_pending_info.jsonl"
    last_digest_path = state_dir / "whale_last_digest.json"

    instant, info = split_by_mode(signals)

    # Instant: send immediately
    if instant:
        msg = render_instant_alerts(instant, now=now)
        if msg:
            try:
                send_messages([msg])
                logger.info("sent instant alert with %d signals", len(instant))
            except Exception as e:
                logger.warning("instant telegram send failed: %s", e)

    # Info: park in pending (digest flush already ran at top of function)
    if info:
        _append_pending(info, pending_path, run_ts=now)

    logger.info("emitted %d signals (instant=%d, info=%d)",
                len(signals), len(instant), len(info))

    _write_run_meta(
        meta_path, now,
        candidate_count=len(candidates),
        new_fills=total_new_fills,
        scored_whales=len(scores),
        signals_emitted=len(signals),
        status="ok",
    )


# --------------------------------------------------- pending info / digest

def _append_pending(signals: list[Signal], path: Path, run_ts: datetime) -> None:
    if not signals:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_iso = run_ts.isoformat()
    with path.open("a", encoding="utf-8") as fh:
        for s in signals:
            fh.write(json.dumps({
                "run_ts": run_iso,
                "rule": s.rule,
                "severity": s.severity,
                "coin": s.coin,
                "message": s.message,
                "details": s.details,
            }, separators=(",", ":")) + "\n")


def _read_pending(path: Path) -> list[Signal]:
    path = Path(path)
    if not path.exists():
        return []
    out: list[Signal] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            out.append(Signal(
                rule=str(row["rule"]),
                severity=int(row.get("severity", 1)),
                coin=str(row["coin"]),
                message=str(row.get("message", "")),
                details=row.get("details") or {},
            ))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


def _clear_pending(path: Path) -> None:
    path = Path(path)
    if path.exists():
        path.unlink()


def _should_flush_digest(last_digest_path: Path, now: datetime) -> bool:
    path = Path(last_digest_path)
    if not path.exists():
        return True  # never sent — go ahead
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last_ts = datetime.fromisoformat(data["sent_at"])
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return True
    return (now - last_ts) >= timedelta(hours=_DIGEST_INTERVAL_HOURS)


def _mark_digest_sent(last_digest_path: Path, now: datetime) -> None:
    path = Path(last_digest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sent_at": now.isoformat()}), encoding="utf-8")


def _maybe_flush_digest(pending_path: Path, last_digest_path: Path, now: datetime) -> None:
    """Send the daily digest if the cadence allows and there's anything to send.

    Called at the top of every run so digest fires on quiet days too.
    """
    if not _should_flush_digest(last_digest_path, now=now):
        return
    pending = _read_pending(pending_path)
    if not pending:
        return
    digest_msg = render_digest(pending, now=now)
    if not digest_msg:
        return
    try:
        send_messages([digest_msg])
        logger.info("sent digest with %d signals", len(pending))
        _clear_pending(pending_path)
        _mark_digest_sent(last_digest_path, now=now)
    except Exception as e:
        logger.warning("digest telegram send failed: %s", e)


def _gather_recent_fills(state_dir: Path, now: datetime, hours: int = 4):
    """Read state/whale_fills.jsonl, return WhaleFill rows from the last N hours."""
    from src.whale_scoring import _load_jsonl_fills  # internal helper
    path = state_dir / "whale_fills.jsonl"
    if not path.exists():
        return []
    cutoff_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)
    return [f for f in _load_jsonl_fills(path) if f.time_ms >= cutoff_ms]



    """Read state/whale_fills.jsonl, return WhaleFill rows from the last N hours."""
    from src.whale_scoring import _load_jsonl_fills  # internal helper
    path = state_dir / "whale_fills.jsonl"
    if not path.exists():
        return []
    cutoff_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)
    return [f for f in _load_jsonl_fills(path) if f.time_ms >= cutoff_ms]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    repo_root = Path(__file__).resolve().parent.parent
    try:
        run_whale_monitor(repo_root=repo_root)
        return 0
    except Exception as e:
        # only programmer errors get here — operational ones are swallowed inside
        logger.error("whale-monitor crashed: %s\n%s", e, traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
