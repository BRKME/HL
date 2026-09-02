"""Смоук-прогон скриптов-раннеров (02.09.2026).

Перебор упал в Actions с `UnboundLocalError: best_p` — я вставил блок
издержек ДО того места, где переменная определяется. Preflight этого не
поймал: тесты покрывают `src/`, а скрипты только разбираются синтаксически.
Синтаксис был верен, порядок — нет.

Здесь раннер исполняется целиком на подставных данных: сеть и Telegram
заглушены, история синтетическая. Проверяется не результат перебора, а то,
что скрипт доходит до конца, не спотыкаясь о порядок вычислений.
"""
import sys

import pytest


def _fake_candles(n=600, base=100.0, drift=0.0015):
    import random
    rng = random.Random(4)
    out, p = [], base
    for _ in range(n):
        p *= (1 + drift + rng.uniform(-0.03, 0.03))
        out.append({"t": 0, "o": p * 0.99, "h": p * 1.02,
                    "l": p * 0.98, "c": p})
    return out


@pytest.fixture
def stubbed(monkeypatch):
    """Сеть и отправка заглушены, история синтетическая."""
    import src.hl_api as hl_api
    import src.telegram_sender as tg

    monkeypatch.setattr(hl_api, "fetch_candles",
                        lambda *a, **k: _fake_candles())
    monkeypatch.setattr(tg, "send_messages", lambda msgs: None)
    monkeypatch.setenv("SWEEP_COINS", "BTC,ETH")
    monkeypatch.setenv("SWEEP_LOOKBACK_DAYS", "600")
    for mod in [m for m in sys.modules if m.startswith("scripts.")]:
        del sys.modules[mod]


def _load(path):
    import importlib.util
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("runner", root / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_momentum_sweep_runner_completes(stubbed, monkeypatch):
    """Ровно та поломка: переменная использовалась до присваивания."""
    mod = _load("scripts/momentum_sweep_run.py")
    monkeypatch.setattr(mod, "fetch_candles", lambda *a, **k: _fake_candles())
    monkeypatch.setattr(mod, "COINS", ["BTC", "ETH"])
    assert mod.main() == 0


def test_tactical_backtest_runner_completes(stubbed, monkeypatch):
    mod = _load("scripts/tactical_backtest_run.py")
    monkeypatch.setattr(mod, "fetch_candles", lambda *a, **k: _fake_candles())
    assert mod.main() in (0, 1)


def test_h1_checkpoint_runner_completes():
    mod = _load("scripts/h1_checkpoint.py")
    assert mod.main() == 0
