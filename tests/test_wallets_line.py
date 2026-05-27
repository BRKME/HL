"""Tests for the per-wallet balance line (Кошельки)."""
from datetime import datetime, timezone

from src.daily_report import render_daily_report, _render_wallets


NOW = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)


def test_render_wallets_basic():
    line = _render_wallets({"1": 1050.0, "Marta": 80.0, "Arkadii": 30.0})
    assert line is not None
    assert "Кошельки:" in line
    assert "1 $1 050" in line
    assert "Marta $80" in line
    assert "Arkadii $30" in line


def test_render_wallets_preserves_insertion_order():
    """Order must be the same as whitelist.yaml: 1, Marta, Arkadii."""
    line = _render_wallets({"1": 1000.0, "Marta": 500.0, "Arkadii": 250.0})
    i1 = line.find("1 $")
    i_marta = line.find("Marta")
    i_arkadii = line.find("Arkadii")
    assert 0 <= i1 < i_marta < i_arkadii


def test_render_wallets_shows_zero_balance():
    """Zero balance still rendered — informative (wallet exists, empty)."""
    line = _render_wallets({"1": 1000.0, "Marta": 0.0, "Arkadii": 0.0})
    assert "Marta $0" in line
    assert "Arkadii $0" in line


def test_render_wallets_none_returns_none():
    assert _render_wallets(None) is None


def test_render_wallets_empty_dict_returns_none():
    assert _render_wallets({}) is None


def test_full_report_includes_wallets_line():
    """End-to-end: wallets line appears between Веса and Алерты."""
    msgs = render_daily_report(
        matches=[], alerts=[], marks={},
        current_snapshot=None, total_account_value=1160.0,
        now=NOW, performance=None,
        wallet_values={"1": 1050.0, "Marta": 80.0, "Arkadii": 30.0},
    )
    text = "\n".join(msgs)
    assert "Кошельки:" in text
    assert "1 $1 050" in text


def test_full_report_no_wallets_block_when_not_provided():
    """Backward compatibility: old callers that don't pass wallet_values
    still get a valid report without a Кошельки line."""
    msgs = render_daily_report(
        matches=[], alerts=[], marks={},
        current_snapshot=None, total_account_value=1160.0,
        now=NOW, performance=None,
    )
    text = "\n".join(msgs)
    assert "Кошельки:" not in text
