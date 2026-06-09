"""Tests for relative_strength — RS vs BTC observability."""
from src.relative_strength import compute_rs, compute_rs_pair


def _ramp(start: float, growth_pct: float, n: int) -> list[float]:
    """Linear price ramp from `start`, ending at growth_pct above start."""
    end = start * (1 + growth_pct / 100)
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


def test_outperformance_positive_rs():
    """Coin +80%, BTC +20% over 30d → RS_30 ≈ +60."""
    coin = _ramp(100, 80, 35)
    btc = _ramp(50_000, 20, 35)
    rs = compute_rs(coin, btc, lookback_days=30)
    # Linear ramps over 35 points: index -1 is 80% above index 0,
    # but lookback is index -31 (30 days back). That index is at
    # ~(35-31)/(35-1) = 4/34 = 11.8% of the way, so price ~111.76.
    # coin_return = (180 - 111.76)/111.76 = 61.07%
    # btc analogous → ~15.27%
    # RS ≈ +45.8
    assert rs is not None
    assert rs > 40  # coin clearly outperformed


def test_underperformance_negative_rs():
    """Coin +5%, BTC +20% over 30d → RS_30 negative."""
    coin = _ramp(100, 5, 35)
    btc = _ramp(50_000, 20, 35)
    rs = compute_rs(coin, btc, lookback_days=30)
    assert rs is not None
    assert rs < 0


def test_same_return_zero_rs():
    """Both coins +20% → RS ≈ 0."""
    coin = _ramp(100, 20, 35)
    btc = _ramp(50_000, 20, 35)
    rs = compute_rs(coin, btc, lookback_days=30)
    assert rs is not None
    assert abs(rs) < 0.5


def test_insufficient_data_returns_none():
    coin = [100, 101, 102]
    btc = [50_000, 50_100, 50_200]
    assert compute_rs(coin, btc, lookback_days=30) is None


def test_empty_input_returns_none():
    assert compute_rs([], [], lookback_days=30) is None
    assert compute_rs([100], [], lookback_days=1) is None


def test_zero_starting_price_returns_none():
    """When the lookback-back index has zero price, return None — can't divide."""
    coin = [100] * 35
    coin[-31] = 0  # the lookback index (30 days back)
    btc = [50_000] * 35
    assert compute_rs(coin, btc, lookback_days=30) is None


def test_pair_returns_both_horizons():
    coin = _ramp(100, 50, 100)
    btc = _ramp(50_000, 20, 100)
    rs_30, rs_90 = compute_rs_pair(coin, btc)
    assert rs_30 is not None
    assert rs_90 is not None
    # Both should show coin outperforming
    assert rs_30 > 0
    assert rs_90 > 0


def test_pair_returns_none_for_short_history():
    coin = _ramp(100, 50, 40)  # enough for 30d, not 90d
    btc = _ramp(50_000, 20, 40)
    rs_30, rs_90 = compute_rs_pair(coin, btc)
    assert rs_30 is not None
    assert rs_90 is None
