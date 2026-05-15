"""Fetch historical OracAI snapshots via GitHub commit history.

last_output.json is overwritten on every OracAI run; to get yesterday's state
we look at the git history of that file and read it at the commit that was
HEAD ~24h ago.

GitHub public API: 60 req/h unauth. Daily monitor makes 2 calls per run
(commits list + raw file at SHA) = ~12 calls/day. Well within limits.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests


_GITHUB_API = "https://api.github.com"
_OWNER = "BRKME"
_REPO = "OracAI"
_PATH = "state/last_output.json"
_RAW_BASE = "https://raw.githubusercontent.com"


class OracAIHistoryError(RuntimeError):
    pass


def _gh_request(url: str, params: Optional[dict] = None) -> Any:
    """Wrapper with optional GITHUB_TOKEN to lift the unauth rate limit."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise OracAIHistoryError(f"GitHub API error {url}: {e}") from e


def _find_commit_at_or_before(target_ts: datetime) -> Optional[str]:
    """Return SHA of the latest commit touching `state/last_output.json` that
    landed at or before `target_ts`. None if no such commit exists yet.

    Strategy: ask for commits with `until=target_ts`, return the first SHA.
    """
    url = f"{_GITHUB_API}/repos/{_OWNER}/{_REPO}/commits"
    params = {
        "path": _PATH,
        "until": target_ts.isoformat().replace("+00:00", "Z"),
        "per_page": 1,
    }
    commits = _gh_request(url, params=params)
    if not isinstance(commits, list) or not commits:
        return None
    return commits[0].get("sha")


def fetch_snapshot_at_sha(sha: str) -> dict[str, Any]:
    """Fetch state/last_output.json at a specific commit SHA."""
    url = f"{_RAW_BASE}/{_OWNER}/{_REPO}/{sha}/{_PATH}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise OracAIHistoryError(f"raw fetch error: {e}") from e
    except ValueError as e:
        raise OracAIHistoryError(f"snapshot at {sha} is not JSON: {e}") from e


def fetch_snapshot_days_ago(days: int, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Snapshot that was current `days` ago (e.g. 1 = yesterday).

    Returns None if no commit exists in that window yet (e.g. fresh repo).
    Raises OracAIHistoryError on any other failure.
    """
    now = now or datetime.now(timezone.utc)
    target = now - timedelta(days=days)
    sha = _find_commit_at_or_before(target)
    if sha is None:
        return None
    return fetch_snapshot_at_sha(sha)


def regime_changed(prev: Optional[dict], curr: Optional[dict]) -> Optional[tuple[str, str]]:
    """If regime flipped between two snapshots, return (prev_regime, curr_regime).

    None means: missing data, or regime unchanged. Caller treats None as "no
    alert" — we never alert based on partial data.
    """
    if not prev or not curr:
        return None
    p = prev.get("regime")
    c = curr.get("regime")
    if not p or not c:
        return None
    if p == c:
        return None
    return (p, c)


def phase_changed(prev: Optional[dict], curr: Optional[dict]) -> Optional[tuple[str, str]]:
    """Like regime_changed but for cycle.phase."""
    if not prev or not curr:
        return None
    p = (prev.get("cycle") or {}).get("phase")
    c = (curr.get("cycle") or {}).get("phase")
    if not p or not c:
        return None
    if p == c:
        return None
    return (p, c)
