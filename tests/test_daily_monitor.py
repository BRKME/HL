"""Tests for src/daily_monitor.py — end-to-end pipeline orchestration."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.daily_monitor import run_daily_monitor, load_accounts


NOW = datetime(2026, 5, 14, 6, 0, tzinfo=timezone.utc)


# ---------- whitelist.yaml accounts loader ----------

def test_load_accounts_reads_yaml_section(tmp_path):
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "accounts:\n"
        "  - address: \"0x17e6D71D30d260e30BB7721C63539694aB02b036\"\n"
        "    label: main\n"
        "  - address: \"0x10082016a94920aBdf410CDB6f98c2Ead2c57340\"\n"
        "    label: second\n"
        "tokens: {}\n"
    )
    accounts = load_accounts(wl)
    assert len(accounts) == 2
    assert accounts[0]["address"].startswith("0x17e6")
    assert accounts[0]["label"] == "main"


def test_load_accounts_returns_empty_when_section_missing(tmp_path):
    wl = tmp_path / "whitelist.yaml"
    wl.write_text("tokens: {}\n")
    assert load_accounts(wl) == []


def test_load_accounts_skips_rows_without_address(tmp_path):
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "accounts:\n"
        "  - address: \"0x17e6D71D30d260e30BB7721C63539694aB02b036\"\n"
        "    label: main\n"
        "  - label: bad\n"  # no address
        "tokens: {}\n"
    )
    accounts = load_accounts(wl)
    assert len(accounts) == 1


# ---------- end-to-end pipeline ----------

@pytest.fixture
def temp_repo(tmp_path):
    """Mini repo with whitelist.yaml and decisions.jsonl set up."""
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "accounts:\n"
        "  - address: \"0x17e6d71d30d260e30bb7721c63539694ab02b036\"\n"
        "    label: main\n"
        "  - address: \"0x10082016a94920abdf410cdb6f98c2ead2c57340\"\n"
        "    label: second\n"
        "tokens: {}\n"
    )
    dec_ts = (NOW - timedelta(days=3)).isoformat()
    dec_row = {
        "ts": dec_ts,
        "signal": "MODERATE",
        "oracai": {"regime": "BULL", "phase": "MID_BULL"},
        "picks": [{
            "symbol": "BTC", "hl_symbol": "BTC", "entry": 80000.0, "alloc_usd": 200.0,
            "sl_price": 76000.0, "sl_pct": -5.0, "sl_method": "atr", "atr14": 2000.0,
        }],
    }
    (tmp_path / "decisions.jsonl").write_text(json.dumps(dec_row) + "\n")
    return tmp_path


def _stub_hl_client():
    """Return an HLClient stub that mimics two wallets, one with BTC long."""
    client = MagicMock()
    def perp_state(addr):
        if addr.endswith("ab02b036"):
            return {
                "marginSummary": {"accountValue": "1000.0"},
                "assetPositions": [{
                    "type": "oneWay",
                    "position": {
                        "coin": "BTC",
                        "szi": "0.0025",
                        "entryPx": "80000",
                        "leverage": {"type": "cross", "value": 10},
                        "liquidationPx": "72000",
                        "marginUsed": "20",
                        "positionValue": "205",
                        "unrealizedPnl": "5.0",
                        "returnOnEquity": "0.025",
                        "cumFunding": {"sinceOpen": "0", "sinceChange": "0", "allTime": "0"},
                    },
                }],
            }
        return {"marginSummary": {"accountValue": "200.0"}, "assetPositions": []}

    def spot_state(addr):
        return {"balances": []}

    client.get_clearinghouse_state.side_effect = perp_state
    client.get_spot_clearinghouse_state.side_effect = spot_state
    client.resolve_spot_coin.side_effect = lambda s: s
    return client


def test_run_daily_monitor_smoke(temp_repo):
    """End-to-end: HL+OracAI mocked, telegram_sender mocked, no exceptions, alerts produced."""
    sent_messages: list[list[str]] = []

    fake_marks = {"BTC": {"mark": 82000.0}}
    fake_today = {"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}, "confidence": 0.65,
                  "asset_allocation": {}, "risk": {}}
    fake_yesterday = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}, "confidence": 0.60}

    with patch("src.daily_monitor.HLClient", return_value=_stub_hl_client()), \
         patch("src.daily_monitor.fetch_meta_and_ctxs", return_value=fake_marks), \
         patch("src.daily_monitor.fetch_spot_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_combined_performance", return_value=None), \
         patch("src.daily_monitor.fetch_sl_orders_for_wallets", return_value=[]), \
         patch("src.daily_monitor.fetch_oracai_snapshot", return_value=fake_today), \
         patch("src.daily_monitor.fetch_snapshot_days_ago", return_value=fake_yesterday), \
         patch("src.daily_monitor.send_messages", side_effect=lambda m: sent_messages.append(m)):
        run_daily_monitor(
            whitelist_path=temp_repo / "whitelist.yaml",
            decisions_path=temp_repo / "decisions.jsonl",
            now=NOW,
        )

    assert len(sent_messages) == 1
    msgs = sent_messages[0]
    body = "\n".join(msgs)
    # contains expected sections
    assert "HL Portfolio" in body
    assert "BTC" in body
    # regime flip alert appears
    assert "BULL" in body and "BEAR" in body


def test_run_daily_monitor_no_positions_still_sends_report(temp_repo):
    """All wallets empty — should still send a report saying so."""
    empty_client = MagicMock()
    empty_client.get_clearinghouse_state.return_value = {
        "marginSummary": {"accountValue": "0.0"}, "assetPositions": [],
    }
    empty_client.get_spot_clearinghouse_state.return_value = {"balances": []}
    empty_client.resolve_spot_coin.side_effect = lambda s: s

    sent: list[list[str]] = []
    with patch("src.daily_monitor.HLClient", return_value=empty_client), \
         patch("src.daily_monitor.fetch_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_spot_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_combined_performance", return_value=None), \
         patch("src.daily_monitor.fetch_sl_orders_for_wallets", return_value=[]), \
         patch("src.daily_monitor.fetch_oracai_snapshot", return_value=None), \
         patch("src.daily_monitor.fetch_snapshot_days_ago", return_value=None), \
         patch("src.daily_monitor.send_messages", side_effect=lambda m: sent.append(m)):
        run_daily_monitor(
            whitelist_path=temp_repo / "whitelist.yaml",
            decisions_path=temp_repo / "decisions.jsonl",
            now=NOW,
        )
    assert len(sent) == 1
    body = "\n".join(sent[0])
    assert "пуст" in body.lower() or "нет позиций" in body.lower()


def test_run_daily_monitor_survives_oracai_failure(temp_repo):
    """OracAI down -> snapshot is None -> bot still runs, just no regime alerts."""
    from src.oracai_history import OracAIHistoryError
    sent: list[list[str]] = []

    def oracai_boom():
        raise RuntimeError("OracAI is down")

    with patch("src.daily_monitor.HLClient", return_value=_stub_hl_client()), \
         patch("src.daily_monitor.fetch_meta_and_ctxs", return_value={"BTC": {"mark": 82000.0}}), \
         patch("src.daily_monitor.fetch_spot_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_combined_performance", return_value=None), \
         patch("src.daily_monitor.fetch_sl_orders_for_wallets", return_value=[]), \
         patch("src.daily_monitor.fetch_oracai_snapshot", side_effect=oracai_boom), \
         patch("src.daily_monitor.fetch_snapshot_days_ago", side_effect=OracAIHistoryError("gh down")), \
         patch("src.daily_monitor.send_messages", side_effect=lambda m: sent.append(m)):
        run_daily_monitor(
            whitelist_path=temp_repo / "whitelist.yaml",
            decisions_path=temp_repo / "decisions.jsonl",
            now=NOW,
        )
    assert len(sent) == 1
    body = "\n".join(sent[0])
    assert "BTC" in body  # core report still produced


def test_run_daily_monitor_survives_one_wallet_failure(temp_repo):
    """If HL API fails for one wallet, continue with the others."""
    flaky = MagicMock()
    call_count = {"perp": 0}
    def flaky_perp(addr):
        call_count["perp"] += 1
        if call_count["perp"] == 1:
            raise RuntimeError("first wallet HL hiccup")
        return {
            "marginSummary": {"accountValue": "500"},
            "assetPositions": [],
        }
    flaky.get_clearinghouse_state.side_effect = flaky_perp
    flaky.get_spot_clearinghouse_state.return_value = {"balances": []}
    flaky.resolve_spot_coin.side_effect = lambda s: s

    sent: list[list[str]] = []
    with patch("src.daily_monitor.HLClient", return_value=flaky), \
         patch("src.daily_monitor.fetch_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_spot_meta_and_ctxs", return_value={}), \
         patch("src.daily_monitor.fetch_combined_performance", return_value=None), \
         patch("src.daily_monitor.fetch_sl_orders_for_wallets", return_value=[]), \
         patch("src.daily_monitor.fetch_oracai_snapshot", return_value=None), \
         patch("src.daily_monitor.fetch_snapshot_days_ago", return_value=None), \
         patch("src.daily_monitor.send_messages", side_effect=lambda m: sent.append(m)):
        run_daily_monitor(
            whitelist_path=temp_repo / "whitelist.yaml",
            decisions_path=temp_repo / "decisions.jsonl",
            now=NOW,
        )
    # one message still sent, despite one wallet failing
    assert len(sent) == 1
