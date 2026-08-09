"""Метрики предзарегистрированной гипотезы H1 (см. docs/OPERATING_POLICY.md §3).

Считаются скриптом, а не руками на чекпойнте: метрика, посчитанная глазами
постфактум, слишком легко подгоняется под ожидание. Решающие правила
зафиксированы 08.08 до сбора данных, здесь они просто исполняются.
"""
from datetime import datetime, timedelta, timezone

from src.h1_metrics import (
    H1Result,
    compute_h1,
    reentry_rate_after,
    verdict_h1,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _exit(coin, reason, hours_ago, r=0.0, pv=2):
    return {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "coin": coin, "exit_reason": reason, "pnl_r": r,
            "policy_version": pv, "closed_direction": "LONG"}


def _entry(coin, hours_ago, direction="LONG"):
    return {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "coin": coin, "direction": direction}


# ------------------------------------------------------------- reentry

def test_reentry_within_window_counted():
    rows = [_exit("ASTER", "regime_flip", 30), _entry("ASTER", 20)]
    assert reentry_rate_after(rows, "regime_flip", window_h=24) == 1.0


def test_reentry_outside_window_not_counted():
    rows = [_exit("ASTER", "regime_flip", 60), _entry("ASTER", 20)]
    assert reentry_rate_after(rows, "regime_flip", window_h=24) == 0.0


def test_reentry_opposite_direction_not_counted():
    """Возврат в ту же сторону — дребезг. Разворот — решение."""
    rows = [_exit("ASTER", "regime_flip", 30),
            _entry("ASTER", 20, direction="SHORT")]
    assert reentry_rate_after(rows, "regime_flip", window_h=24) == 0.0


def test_reentry_other_coin_not_counted():
    rows = [_exit("ASTER", "regime_flip", 30), _entry("TAO", 20)]
    assert reentry_rate_after(rows, "regime_flip", window_h=24) == 0.0


def test_reentry_rate_with_no_exits_is_zero():
    assert reentry_rate_after([_entry("ASTER", 5)], "regime_flip") == 0.0


# ---------------------------------------------------- решающие правила

def _res(n, share, reentry, median_r, flips):
    return H1Result(n_exits=n, regime_flip_share=share,
                    reentry_rate=reentry, regime_flip_median_r=median_r,
                    regime_flips_30d=flips)


def test_sample_too_small_defers():
    assert verdict_h1(_res(12, 0.5, 0.6, -0.2, 6)).startswith("НЕДОСТАТОЧНО")


def test_confirmed_when_share_and_reentry_both_high():
    v = verdict_h1(_res(20, 0.35, 0.45, -0.2, 6))
    assert "ПОДТВЕРЖДЕНА" in v


def test_positive_median_r_blocks_confirmation():
    """Частота — не дефект. Дефект — частота, стоящая денег."""
    v = verdict_h1(_res(20, 0.50, 0.80, 0.05, 8))
    assert "ЗАЩИТНЫЕ" in v


def test_low_share_closes_hypothesis():
    assert "НЕ ПОДТВЕРЖДЕНА" in verdict_h1(_res(20, 0.20, 0.9, -0.5, 6))


def test_few_regime_flips_means_outlier_period():
    v = verdict_h1(_res(20, 0.35, 0.5, -0.2, 3))
    assert "ВСПЛЕСК" in v


def test_high_share_but_low_reentry_is_not_churn():
    v = verdict_h1(_res(20, 0.40, 0.10, -0.2, 6))
    assert "ПОДТВЕРЖДЕНА" not in v or "НЕ ПОДТВЕРЖДЕНА" in v


# -------------------------------------------------------------- сборка

def test_compute_h1_on_synthetic_journal():
    rows = ([_exit("A", "regime_flip", 100, -0.1),
             _exit("B", "verdict_flip", 90, 0.2)]
            + [_entry("A", 95)])
    res = compute_h1(rows, now=NOW, regime_changes_30d=5)
    assert res.n_exits == 2
    assert res.regime_flip_share == 0.5
    assert res.reentry_rate == 1.0


def test_compute_h1_ignores_policy_1():
    rows = [_exit("A", "regime_flip", 10, pv=None),
            _exit("B", "verdict_flip", 9)]
    res = compute_h1(rows, now=NOW, regime_changes_30d=5)
    assert res.n_exits == 1


def test_compute_h1_on_empty():
    res = compute_h1([], now=NOW, regime_changes_30d=0)
    assert res.n_exits == 0
    assert verdict_h1(res).startswith("НЕДОСТАТОЧНО")


# ------------------------------------------------------------- гипотеза H2

from src.h1_metrics import H2Result, compute_h2, verdict_h2  # noqa: E402


def _closed(direction, r, n=1):
    return [{"ts": NOW.isoformat(), "coin": "X", "exit_reason": "verdict_flip",
             "pnl_r": r, "closed_direction": direction} for _ in range(n)]


def test_h2_small_sample_defers():
    res = compute_h2(_closed("LONG", -0.1, 5))
    assert verdict_h2(res).startswith("НЕДОСТАТОЧНО")


def test_h2_counts_sides_separately():
    res = compute_h2(_closed("LONG", 0.2, 10) + _closed("SHORT", -0.3, 6))
    assert res.n_long == 10 and res.n_short == 6


def test_h2_ignores_unevaluated_trades():
    rows = _closed("LONG", 0.1, 3) + [{"ts": NOW.isoformat(), "coin": "X",
                                       "exit_reason": "verdict_flip"}]
    assert compute_h2(rows).n_closed == 3


def test_h2_empty():
    res = compute_h2([])
    assert res.n_closed == 0
    assert verdict_h2(res).startswith("НЕДОСТАТОЧНО")


def test_h2_side_gap_is_checked_before_overall_loss():
    """Асимметрия сторон — более узкое действие, чем отключение слоя."""
    res = compute_h2(_closed("LONG", 0.05, 25) + _closed("SHORT", -0.40, 20))
    assert "СТОРОНА" in verdict_h2(res)


def test_h2_side_gap_needs_both_sides_populated():
    res = compute_h2(_closed("LONG", 0.05, 45) + _closed("SHORT", -0.40, 3))
    assert "СТОРОНА" not in verdict_h2(res)


def test_h2_consistent_loss_disables_entries():
    res = compute_h2(_closed("LONG", -0.30, 45))
    assert "СЛОЙ ТЕРЯЕТ" in verdict_h2(res)


def test_h2_interval_spanning_zero_is_undecided():
    rows = _closed("LONG", 0.5, 20) + _closed("LONG", -0.5, 20) + _closed("LONG", 0.0, 5)
    assert "НЕ ДОКАЗАНО" in verdict_h2(compute_h2(rows))


def test_h2_bootstrap_is_deterministic():
    """Отчёт, меняющийся от прогона к прогону, не годится для решения."""
    rows = _closed("LONG", 0.2, 30) + _closed("SHORT", -0.1, 20)
    assert compute_h2(rows).ci_low == compute_h2(rows).ci_low
