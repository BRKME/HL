"""Киты против вердикта в строке монеты (17.09.2026).

17 сентября четыре кита открыли SHORT по BTC на $12.3M и один по ETH на
$3.8M — в тот же день дайджест предлагал ВХОДИТЬ LONG по обоим. Данные о
китах были в шапке письма («🐋 Киты 7d: BTC 52% mix»), но со строками
монет не связаны: слои спорили молча.

Это та же мысль, что «крупные против толпы», но из другого источника —
реальные кошельки Hyperliquid, а не срезы OKX. Указание оператора:
механизм должен работать в единой логике.

Порог берётся готовый: bias даёт направление при 60/40 и не меньше трёх
сделок — середина считается шумом и не помечается.
"""
import pytest

from src.whale_stance import WhaleStance


def _stance(long_usd, short_usd, long_n=3, short_n=3):
    return WhaleStance(coin="BTC", long_notional=long_usd,
                       short_notional=short_usd, long_count=long_n,
                       short_count=short_n)


def test_bias_short_when_whales_sell():
    assert _stance(1_000, 12_300_000).bias == "short"


def test_bias_long_when_whales_buy():
    assert _stance(12_600_000, 1_000).bias == "long"


def test_mixed_is_not_a_bias():
    """52/48 — шум, помечать нечего."""
    assert _stance(5_200_000, 4_800_000).bias is None


def test_too_few_trades_is_not_a_bias():
    """Три сделки минимум: один кит не делает позиции рынка."""
    assert _stance(12_000_000, 0, long_n=1, short_n=0).bias is None


def test_marker_appears_only_on_disagreement():
    """Метка нужна там, где киты ПРОТИВ вердикта: совпадение — норма, и
    перечислять норму мы не перечисляем (§7.8)."""
    import re
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from unittest.mock import patch

    from src.whitelist_focus import render_whitelist_verdicts

    closes = [100.0 * (1.004 ** i) for i in range(220)]
    candles = [{"o": c, "h": c * 1.02, "l": c * 0.98, "c": c} for c in closes]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes,
                  "candles": candles}}

    with patch("src.whale_stance.compute_stance",
               return_value={"BTC": _stance(1_000, 12_300_000)}):
        msg = re.sub(r"<[^>]+>", "", render_whitelist_verdicts(
            now=datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc),
            coin_data=cd, regime_snapshot=None,
            state_dir=Path(tempfile.mkdtemp()), show_whale_stance=True))
    assert "киты в шорте" in msg or "Входов нет" in msg


def test_no_marker_without_stance_data():
    """Данных о китах нет — строка просто короче, письмо не ломается."""
    import re
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from src.whitelist_focus import render_whitelist_verdicts

    closes = [100.0 * (1.004 ** i) for i in range(220)]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes}}
    msg = render_whitelist_verdicts(
        now=datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc), coin_data=cd,
        regime_snapshot=None, state_dir=Path(tempfile.mkdtemp()),
        show_whale_stance=False)
    assert "киты в" not in msg
