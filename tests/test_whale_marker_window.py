"""Метка китов по двум окнам: сутки первыми (23.09.2026).

Метка смотрела только на неделю и молчала, когда киты развернулись
сегодня. По живым данным 23.09:

             за сутки            за неделю
  HYPE       шорт (243 vs 34)    поровну
  BTC        шорт (282 vs 0)     лонг
  ETH        лонг                шорт

У трёх монет из четырёх окна показывали противоположное. Письмо
предлагало HYPE вторым на покупку, пока китовый дайджест того же утра
показывал три кита в шорте на $7.9M.

Правило: сутки проверяются первыми — разворот сегодня есть повод не
входить сегодня. Если за сутки картины нет, берётся неделя. Окно
называется в метке.
"""
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.whale_stance import WhaleStance


def _st(long_usd, short_usd, n=5):
    return WhaleStance(coin="HYPE", long_notional=long_usd,
                       short_notional=short_usd, long_count=n, short_count=n)


def _render(day, week):
    from src.whitelist_focus import render_whitelist_verdicts

    # Шумный восходящий тренд: гладкая экспонента даёт «перегрев», и вход
    # не возникает — первая версия тестов из-за этого проходила ВХОЛОСТУЮ,
    # ничего не проверяя. Сид подобран так, чтобы вход был гарантирован.
    import random
    rng = random.Random(2)
    p, closes = 60000.0, []
    for _ in range(220):
        p *= 1 + 0.0018 + rng.uniform(-0.015, 0.015)
        closes.append(p)
    candles = [{"o": c, "h": c * 1.015, "l": c * 0.985, "c": c} for c in closes]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes,
                  "candles": candles}}

    def fake(state_dir, coins, now, lookback_days=7, **kw):
        src = day if lookback_days == 1 else week
        return {c: src for c in coins}

    with patch("src.whale_stance.compute_stance", side_effect=fake):
        msg = render_whitelist_verdicts(
            now=datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
            coin_data=cd, regime_snapshot=None,
            state_dir=Path(tempfile.mkdtemp()), show_whale_stance=True)
    return re.sub(r"<[^>]+>", "", msg)


def test_fixture_really_produces_an_entry():
    """Сторож фикстуры: без входа остальные тесты проходили бы вхолостую."""
    out = _render(day=_st(1, 1), week=_st(1, 1))
    assert "ВХОДИТЬ" in out


def test_day_short_marks_even_if_week_mixed():
    """Ровно HYPE 23.09: сутки в шорте, неделя поровну — метка обязана быть."""
    out = _render(day=_st(1_000, 9_000_000), week=_st(5_000_000, 5_000_000))
    assert "киты в шорте за сутки" in out


def test_week_used_when_day_is_silent():
    out = _render(day=_st(5_000_000, 5_000_000), week=_st(1_000, 9_000_000))
    assert "киты в шорте за неделю" in out


def test_fresh_agreement_overrides_stale_disagreement():
    """Сутки согласны с вердиктом — метки нет, даже если неделя против:
    свежая картина важнее устаревшей."""
    out = _render(day=_st(9_000_000, 1_000), week=_st(1_000, 9_000_000))
    assert "киты в шорте" not in out


def test_no_marker_when_both_agree():
    out = _render(day=_st(9_000_000, 1_000), week=_st(9_000_000, 1_000))
    assert "киты в" not in out
