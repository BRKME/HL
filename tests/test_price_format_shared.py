"""Единое форматирование цен во всех сообщениях (30.08.2026).

Heartbeat в новом канале показал ту же поломку, что утром чинили в
дайджесте:

    🟢 ASTER LONG · вход $1 · SL $1 · TP $1 · сейчас $1 (+6.0%)
    🟢 NEAR  LONG · вход $2 · SL $2 · TP $2 · сейчас $2 (-5.8%)

Вход, стоп и цель — одно число. По такой строке нельзя понять ни где стоп,
ни сколько до цели; для TAO за $235 всё было нормально, потому что цена
трёхзначная.

Причина: утренняя правка касалась только `_fmt_price` в дайджесте, а по
системе таких мест семнадцать, каждое со своим `:,.0f`. Одинаковая величина
должна печататься одинаково везде — иначе исправление одного места создаёт
ложное впечатление, что вылечено всё.

Формат вынесен в `src/money.py` и используется всеми.
"""
import pytest

from src.money import fmt_price


@pytest.mark.parametrize("price,expected", [
    (78007.0, "78 007"),
    (2454.0, "2 454"),
    (235.0, "235"),
    (239.4, "239.4"),
    (83.12, "83.12"),
    (2.4471, "2.447"),
    (1.8823, "1.882"),
    (0.6978, "0.6978"),
    (0.3530, "0.353"),
])
def test_significant_precision(price, expected):
    assert fmt_price(price) == expected


def test_entry_sl_tp_stay_distinguishable():
    """Ровно случай ASTER: вход, стоп и цель обязаны различаться."""
    entry, sl, tp = 0.6978, 0.5990, 0.7775
    shown = {fmt_price(entry), fmt_price(sl), fmt_price(tp)}
    assert len(shown) == 3


def test_near_levels_stay_distinguishable():
    assert fmt_price(1.8823) != fmt_price(1.7950)


def test_degenerate():
    assert fmt_price(0) == "—"
    assert fmt_price(None) == "—"


def test_digest_uses_the_shared_formatter():
    """Дайджест и остальные сообщения обязаны печатать одинаково."""
    from src.whitelist_focus import _fmt_price
    for p in (1.8823, 235.0, 78007.0, 0.353):
        assert _fmt_price(p) == fmt_price(p)


def test_no_module_rounds_prices_to_integers():
    """Сторож: `:,.0f` на цене — это и есть та самая поломка."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    offenders = []
    for f in root.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            # Только ЦЕНЫ: суммы (счёт, нотионал китов, PnL в долларах)
            # округлять до целого правильно — там точность не нужна.
            if re.search(r"\$\{[^}]*\b(entry|sl|tp|cur|price|mark|exit_price)"
                         r"[^}]*:,?\.0f\}", line):
                offenders.append(f"{f.name}:{i}")
    assert not offenders, (
        f"цена округляется до целого: {offenders}. Используйте "
        "src.money.fmt_price — NEAR за $1.88 печатался как «$2».")
