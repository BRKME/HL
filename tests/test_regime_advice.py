"""Tests for _render_regime_advice — actionable advice from regime × phase."""
from datetime import datetime, timezone

from src.daily_report import _render_regime_advice, render_daily_report


NOW = datetime(2026, 5, 31, 18, 2, tzinfo=timezone.utc)


def test_advice_bear_early_bear_reduces_risk():
    snap = {"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "риск" in a.lower() or "снижай" in a.lower()
    assert "🛑" in a or "медвеж" in a.lower()


def test_advice_bull_mid_bull_trade_trend():
    snap = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "тренд" in a.lower() or "📈" in a


def test_advice_bull_late_bull_warns_to_lock_profits():
    snap = {"regime": "BULL", "cycle": {"phase": "LATE_BULL"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "приб" in a.lower() or "стоп" in a.lower() or "⚠️" in a


def test_advice_transition_no_new_positions():
    snap = {"regime": "TRANSITION", "cycle": {"phase": "EARLY_BEAR"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "не нараш" in a.lower() or "перехо" in a.lower() or "⏸" in a


def test_advice_returns_none_for_unknown_combo():
    """Unmapped combination → no advice (better silent than wrong)."""
    snap = {"regime": "WEIRD", "cycle": {"phase": "UNKNOWN"}}
    assert _render_regime_advice(snap) is None


# ---------- Wyckoff phases ----------

def test_advice_capitulation_says_buy_zone():
    """BEAR/CAPITULATION = buy panic, don't sell into it."""
    snap = {"regime": "BEAR", "cycle": {"phase": "CAPITULATION"}}
    a = _render_regime_advice(snap)
    assert a is not None
    text = a.lower()
    # Should hint at buying/long, not blocking
    assert ("дне" in text or "long" in text or "лонг" in text
            or "входа" in text or "🔥" in a)
    # Should NOT say "snizhay risk" — that's the old EARLY_BEAR advice
    assert "снижай риск" not in text


def test_advice_accumulation_says_set_positions():
    snap = {"regime": "BEAR", "cycle": {"phase": "ACCUMULATION"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "🎯" in a or "набирай" in a.lower() or "лесенк" in a.lower()


def test_advice_euphoria_says_take_profits():
    """BULL/EUPHORIA = top, sell into it."""
    snap = {"regime": "BULL", "cycle": {"phase": "EUPHORIA"}}
    a = _render_regime_advice(snap)
    assert a is not None
    text = a.lower()
    assert ("фиксируй" in text or "leave" in text or "🚨" in a
            or "верш" in text)


def test_advice_distribution_in_bull_warns_top():
    """BULL/DISTRIBUTION = whales selling to retail at the top."""
    snap = {"regime": "BULL", "cycle": {"phase": "DISTRIBUTION"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "⚠️" in a or "фиксируй" in a.lower() or "продают" in a.lower()


def test_advice_markup_is_active_uptrend():
    """BULL/MARKUP = trade pullbacks in the trend."""
    snap = {"regime": "BULL", "cycle": {"phase": "MARKUP"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert ("тренд" in a.lower() or "📈" in a or "pullback" in a.lower())


def test_advice_transition_capitulation_present():
    """TRANSITION/CAPITULATION — handled."""
    snap = {"regime": "TRANSITION", "cycle": {"phase": "CAPITULATION"}}
    a = _render_regime_advice(snap)
    assert a is not None
    assert "капитул" in a.lower() or "дно" in a.lower()


def test_advice_returns_none_when_phase_missing():
    snap = {"regime": "BULL"}
    assert _render_regime_advice(snap) is None


def test_advice_returns_none_when_snapshot_none():
    assert _render_regime_advice(None) is None


def test_advice_appears_in_full_report():
    """End-to-end: BEAR/EARLY_BEAR snapshot produces visible advice line."""
    snap = {"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}}
    msgs = render_daily_report(
        matches=[], alerts=[], marks={},
        current_snapshot=snap, total_account_value=1275, now=NOW,
        performance=None,
    )
    text = "\n".join(msgs)
    assert "🛑" in text or "Снижай риск" in text or "медвеж" in text


def test_advice_inserted_between_wallets_and_alerts():
    """Order check: header → веса → кошельки → ADVICE → алерты → доходность."""
    from src.matcher import MatchResult
    from src.monitor_rules import Alert, SEV_WARN
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition("ZEC", 4.5, 470, -19,
        contributors=[("Arkadii", 4.5)], avg_leverage=3,
        max_liquidation_distance_pct=50.0)
    alerts = [Alert(rule="X", severity=SEV_WARN, coin="ZEC",
                    message="🔴 ZEC: SL вышибет внутри дня (0.2× ATR)",
                    details={})]
    snap = {"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}}
    msgs = render_daily_report(
        matches=[MatchResult(pos, None, "orphan")],
        alerts=alerts, marks={"ZEC": 470}, current_snapshot=snap,
        total_account_value=1275, now=NOW, performance=None,
        wallet_values={"Arkadii": 1275},
    )
    text = "\n".join(msgs)
    # Find anchor positions
    wallets_idx = text.find("Кошельки")
    advice_idx = text.find("Снижай риск")
    if advice_idx == -1:
        advice_idx = text.find("🛑")
    alerts_idx = text.find("Алерты")
    assert 0 <= wallets_idx < advice_idx < alerts_idx
