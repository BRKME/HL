"""Премия за волатильность (19.09.2026, второй этап).

Отвечает на единственный вопрос политики по опционам: какая из двух премий
перевешивает — за риск актива (в пользу покупателя) или за волатильность
(в пользу продавца).

Реализованная считается оценкой Паркинсона: по максимумам и минимумам, а
не по закрытиям. День, сходивший на 5% вверх и вернувшийся, при оценке по
закрытиям засчитывается как нулевой — а наблюдений у нас мало, и терять их
нельзя.

Подразумеваемая берётся по СЕРЕДИНЕ стакана: на Aevo разрыв достигает 9.9
пункта по биткоину, и выбор любой стороны сдвинул бы замер на половину
спреда.
"""
import math

import pytest

from src.vol_premium import (
    MIN_OBSERVATIONS, PremiumPoint, atm_implied, close_to_close_volatility,
    parkinson_volatility, summarise,
)


def _flat(n=60, price=100.0):
    return [{"h": price, "l": price, "c": price} for _ in range(n)]


def _swinging(n=60, price=100.0, daily=0.04):
    return [{"h": price * (1 + daily), "l": price * (1 - daily), "c": price}
            for _ in range(n)]


# ------------------------------------------------ реализованная волатильность

def test_flat_market_has_zero_volatility():
    assert parkinson_volatility(_flat()) == pytest.approx(0.0, abs=1e-9)


def test_swinging_market_has_high_volatility():
    v = parkinson_volatility(_swinging())
    assert v > 0.5


def test_parkinson_catches_what_closes_miss():
    """Ключевое: день сходил вверх и вернулся. По закрытиям — ноль
    движения, по максимумам и минимумам — настоящая волатильность."""
    swings = _swinging(daily=0.05)
    assert parkinson_volatility(swings) > 0.5
    assert close_to_close_volatility(swings) == pytest.approx(0.0, abs=1e-9)


def test_too_few_observations_returns_none():
    """Оценка по трём дням — не оценка, и ноль тут был бы ложью."""
    assert parkinson_volatility(_flat(n=MIN_OBSERVATIONS - 1)) is None
    assert close_to_close_volatility(_flat(n=5)) is None


def test_broken_candles_are_skipped():
    rows = _swinging(n=40) + [None, {"h": 0, "l": 0}, {"c": 1}]
    assert parkinson_volatility(rows) is not None


# ----------------------------------------- подразумеваемая из стакана

def test_implied_is_mid_of_spread():
    """Спред на Aevo до 9.9 пункта — любая сторона сдвинула бы замер."""
    bid = [["7347.5", "1.5", "0.296761"]]
    ask = [["8152.5", "1.5", "0.395666"]]
    assert atm_implied(bid, ask) == pytest.approx(0.3462, abs=1e-3)


def test_implied_from_one_side_when_other_empty():
    assert atm_implied([["1", "1", "0.4"]], []) == pytest.approx(0.4)


def test_implied_none_without_iv_field():
    assert atm_implied([["1", "1"]], [["2", "1"]]) is None


def test_implied_skips_zero_iv():
    assert atm_implied([["1", "1", "0"], ["1", "1", "0.5"]], []) == 0.5


# --------------------------------------------------------------- премия

def test_premium_is_in_percentage_points():
    p = PremiumPoint(0, "BTC", implied=0.40, realized=0.30, horizon_days=30)
    assert p.premium_pp == pytest.approx(10.0)


def test_negative_premium_when_realized_exceeds_implied():
    """В январе 2026 премия уходила глубоко в минус — замер обязан это
    показывать, а не считать аномалией."""
    p = PremiumPoint(0, "BTC", implied=0.30, realized=0.55, horizon_days=30)
    assert p.premium_pp < 0


def test_summary_reports_worst_not_only_mean():
    """Среднее прячет хвост: продавец волатильности теряет крупно и редко."""
    pts = [PremiumPoint(0, "BTC", 0.4, 0.3, 30),
           PremiumPoint(0, "BTC", 0.3, 0.9, 30)]
    s = summarise(pts)
    assert s["worst_pp"] < -50
    assert s["positive_share"] == pytest.approx(0.5)


def test_empty_summary_is_not_zero():
    s = summarise([])
    assert s["n"] == 0
    assert s["mean_pp"] is None


# ------------------ форвардная реализованная: исправление ошибки 19.09

def test_forward_realized_looks_ahead_not_back():
    """Премия — это подразумеваемая СЕГОДНЯ против реализованной в
    СЛЕДУЮЩИЕ тридцать дней.

    Первая версия сравнивала с реализованной за ПРОШЕДШИЕ тридцать — это
    другая величина: она отвечает «была ли волатильность выше ожиданий
    вчера», а не «переплачивали ли за опцион». Ошибка была бы невидима:
    числа выглядели бы правдоподобно."""
    from src.vol_premium import forward_realized

    calm = _flat(40)
    wild = _swinging(40, daily=0.06)
    series = calm + wild                    # сначала тихо, потом буря

    assert forward_realized(series, 0, 30) == pytest.approx(0.0, abs=1e-9)
    assert forward_realized(series, 45, 30) > 0.5


def test_forward_realized_none_past_data_end():
    """За горизонтом данных нет — и это не ноль."""
    from src.vol_premium import forward_realized

    assert forward_realized(_flat(40), 20, 30) is None


def test_build_history_pairs_dvol_with_forward():
    """DVOL публикуется в процентах, реализованная — в долях: перепутать
    единицы значит получить премию в сто раз больше."""
    from src.vol_premium import build_history

    DAY = 86_400_000
    candles = [{"t": i * DAY, "h": 100.0, "l": 100.0, "c": 100.0}
               for i in range(80)]
    dvol = [[i * DAY, 40.0, 41.0, 39.0, 40.0] for i in range(10)]

    pts = build_history(dvol, candles, asset="BTC", horizon_days=30)
    assert pts
    assert pts[0].implied == pytest.approx(0.40)
    assert pts[0].realized == pytest.approx(0.0, abs=1e-9)
    assert pts[0].premium_pp == pytest.approx(40.0)


def test_build_history_skips_days_without_candles():
    from src.vol_premium import build_history

    DAY = 86_400_000
    candles = [{"t": i * DAY, "h": 100.0, "l": 100.0, "c": 100.0}
               for i in range(40)]
    dvol = [[999 * DAY, 40.0, 41.0, 39.0, 40.0]]
    assert build_history(dvol, candles, "BTC") == []


def test_build_history_survives_broken_rows():
    from src.vol_premium import build_history

    DAY = 86_400_000
    candles = [{"t": i * DAY, "h": 100.0, "l": 100.0, "c": 100.0}
               for i in range(80)]
    dvol = [None, [], ["x"], [0 * DAY, 40.0, 41.0, 39.0, 40.0]]
    assert len(build_history(dvol, candles, "BTC")) == 1


# ---------------------------- IV Rank: режим, известный ДО решения (19.09)

def test_iv_rank_positions_within_range():
    from src.vol_premium import iv_rank

    hist = [0.2] * 30 + [0.6] * 30
    assert iv_rank(hist, 0.4) == pytest.approx(50.0)
    assert iv_rank(hist, 0.6) == pytest.approx(100.0)
    assert iv_rank(hist, 0.2) == pytest.approx(0.0)


def test_iv_rank_needs_history():
    from src.vol_premium import iv_rank

    assert iv_rank([0.3] * 5, 0.3) is None


def test_percentile_is_robust_to_single_spike():
    """Слабость ранга названа в источниках: один давний всплеск задаёт верх
    диапазона и занижает ранг надолго. Процентиль от этого не зависит."""
    from src.vol_premium import iv_percentile, iv_rank

    hist = [0.3] * 59 + [2.0]          # один выброс
    assert iv_rank(hist, 0.35) < 10     # ранг раздавлен выбросом
    assert iv_percentile(hist, 0.35) > 90


def test_split_uses_only_past_observations():
    """Ключевое: ранг считается по ПРЕДШЕСТВУЮЩИМ наблюдениям.

    Прежнее разделение на «спокойно/буря» делило по РЕАЛИЗОВАННОЙ
    волатильности — величине, известной только ПОСЛЕ. В момент решения её
    нет, и вывод опирался на заглядывание вперёд."""
    from src.vol_premium import PremiumPoint, split_by_iv_rank

    pts = [PremiumPoint(i, "BTC", implied=0.2, realized=0.2, horizon_days=30)
           for i in range(40)]
    pts += [PremiumPoint(40 + i, "BTC", implied=0.9, realized=0.2,
                         horizon_days=30) for i in range(20)]
    buckets = split_by_iv_rank(pts, lookback_days=180)
    # первые наблюдения не размечены вовсе — истории под ними нет
    assert len(buckets["high"]) + len(buckets["mid"]) + len(buckets["low"]) < len(pts)
    # поздние, с высокой подразумеваемой, попали в верхнюю корзину
    assert buckets["high"]


def test_split_returns_three_buckets():
    from src.vol_premium import PremiumPoint, split_by_iv_rank

    b = split_by_iv_rank([PremiumPoint(0, "BTC", 0.3, 0.3, 30)])
    assert set(b) == {"high", "mid", "low"}


# ------------- дефекты, найденные при разборе собственной работы (19.09)

def test_rank_is_computed_per_asset():
    """Первая версия получала список, где BTC и ETH перемешаны по времени,
    и ранг точки ETH считался по истории, наполовину состоящей из BTC.

    Типичная подразумеваемая у BTC 40-60%, у ETH 50-80% — ранг мерил,
    какой актив попался, а не режим. Сигнал уничтожался до замера."""
    from src.vol_premium import PremiumPoint, split_by_iv_rank

    pts = []
    for i in range(400):
        # BTC всегда низкая, ETH всегда высокая
        pts.append(PremiumPoint(i * 2, "BTC", 0.40, 0.3, 30))
        pts.append(PremiumPoint(i * 2 + 1, "ETH", 0.80, 0.3, 30))

    b = split_by_iv_rank(pts, lookback_days=180)
    # При правильном разделении ни один актив не уходит целиком в край:
    # внутри своей истории обе серии ровные.
    assets_high = {p.asset for p in b["high"]}
    assets_low = {p.asset for p in b["low"]}
    assert not (assets_high == {"ETH"} and assets_low == {"BTC"}), (
        "ранг считается по смеси активов, а не внутри каждого")


def test_lookback_is_in_days_not_observations():
    """`lookback=365` при точках раз в 12 часов давало ПОЛГОДА вместо года;
    стандарт для IV Rank — 52 недели."""
    import inspect

    from src import vol_premium

    src = inspect.getsource(vol_premium.split_by_iv_rank)
    assert "lookback_days" in src
    assert "points_per_day" in src


def test_episode_count_collapses_overlapping_windows():
    """Окна смотрят на 30 дней вперёд и берутся дважды в сутки: один
    провал попадает примерно в шестьдесят соседних наблюдений. «Худшее
    −52» может описывать ОДИН месяц за пять лет."""
    from src.vol_premium import PremiumPoint, episode_count

    DAY = 86_400_000
    cluster = [PremiumPoint(i * DAY // 2, "BTC", 0.3, 0.9, 30)
               for i in range(60)]          # один эпизод, 60 наблюдений
    far = [PremiumPoint((400 + i) * DAY, "BTC", 0.3, 0.9, 30)
           for i in range(60)]              # второй эпизод через год

    assert episode_count(cluster) == 1
    assert episode_count(cluster + far) == 2


def test_episode_count_empty():
    from src.vol_premium import episode_count

    assert episode_count([]) == 0
