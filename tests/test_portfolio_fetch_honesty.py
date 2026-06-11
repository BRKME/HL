"""Баг 11.06 00:25: все три кошелька показали $0 при живом P&L.

Причина: упавший clearinghouseState-fetch подменялся заглушкой
accountValue:"0", и дайджест рисовал её как реальный ноль. Контракт после
фикса: сбой fetch → кошелёк показывается «n/a⚠️», а не $0; при отказе всех
кошельков заголовок показывает «n/a ⚠️», а не «$0». Настоящий пустой кошелёк
($0 от живого API) по-прежнему показывается как $0 — это информативно.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.daily_report import _render_wallets, _render_header


class TestWalletLineHonesty:
    def test_failed_wallet_shows_na(self):
        line = _render_wallets({"1": 1050.0, "Marta": None, "Arkadii": 30.0})
        assert "Marta n/a⚠️" in line
        assert "$1 050" in line.replace("\u00a0", " ") or "1 050" in line or "1050" in line

    def test_genuine_zero_still_shown(self):
        line = _render_wallets({"1": 0.0})
        assert "$0" in line
        assert "n/a" not in line

    def test_all_failed_all_na(self):
        line = _render_wallets({"1": None, "Marta": None, "Arkadii": None})
        assert line.count("n/a⚠️") == 3
        assert "$0" not in line


class TestHeaderHonesty:
    def test_header_na_when_total_unknown(self):
        h = _render_header(now=datetime(2026, 6, 11, tzinfo=timezone.utc), total_value=None, wallet_count=3)
        assert "n/a" in h and "⚠️" in h
        assert "$0" not in h

    def test_header_normal_when_known(self):
        h = _render_header(now=datetime(2026, 6, 11, tzinfo=timezone.utc), total_value=2347.0, wallet_count=3)
        assert "2 347" in h.replace("\u00a0", " ") or "2347" in h


class TestPortfolioFailedPropagation:
    def _raw(self, failed_labels):
        raw = {}
        for label in ("1", "Marta", "Arkadii"):
            if label in failed_labels:
                raw[label] = {"perp": {"marginSummary": {"accountValue": "0"},
                                       "assetPositions": [],
                                       "_fetch_failed": True},
                              "spot": {"balances": []}}
            else:
                raw[label] = {"perp": {"marginSummary": {"accountValue": "500"},
                                       "assetPositions": []},
                              "spot": {"balances": []}}
        return raw

    def test_failed_wallet_is_none_not_zero(self):
        from src.portfolio import Portfolio
        p = Portfolio.from_raw(self._raw({"Marta"}), spot_resolver=lambda x: x)
        assert p.wallet_values["Marta"] is None
        assert p.wallet_values["1"] == 500.0
        assert p.failed_wallets == ["Marta"]
        assert p.total_account_value == 1000.0     # сумма только живых
        assert p.all_wallets_failed is False

    def test_all_failed_flag(self):
        from src.portfolio import Portfolio
        p = Portfolio.from_raw(self._raw({"1", "Marta", "Arkadii"}),
                               spot_resolver=lambda x: x)
        assert p.all_wallets_failed is True
        assert all(v is None for v in p.wallet_values.values())
