"""Общие ограждения для всего тестового набора.

Заведены 23.08.2026 после того, как два теста подряд оказались ходящими в
реальный API Hyperliquid. Оба нашлись случайно: один — по красному прогону,
второй — по времени выполнения (12 с против 0.5 с у соседей). Случайная
находка означает, что проверки нет.

Оба ограждения переводят класс ошибок из «однажды заметим» в «падает сразу»:

* сеть в тестах запрещена на уровне сокета. Незапатченный вызов больше не
  висит до таймаута и не даёт ложно-зелёный прогон в офлайне — он падает с
  внятным сообщением и именем теста;
* каталог `state/` боевой. Тест, пишущий туда маркер с сегодняшней датой,
  подавляет настоящий суточный сбор вердиктов; такое уже случалось 08.08.
"""
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / "state"


class NetworkUseInTest(RuntimeError):
    """Тест попытался открыть сетевое соединение."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Запрет реальных соединений. Снимается маркером @pytest.mark.network.

    Одного raise мало: боевой код ловит сетевые сбои широким `except` и
    пишет warning — именно поэтому незапатченный вызов и жил незамеченным,
    оставаясь зелёным. Поэтому попытки ещё и КОПЯТСЯ, а тест валится на
    разборке фикстуры, куда продовый except не дотянется.
    """
    if request.node.get_closest_marker("network"):
        yield
        return

    attempts: list[str] = []

    def blocked(*args, **kwargs):
        attempts.append(str(args[0] if args else kwargs))
        raise NetworkUseInTest(f"{request.node.name}: сетевой вызов запрещён")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield
    assert not attempts, (
        f"{request.node.name}: незапатченный сетевой вызов "
        f"({len(attempts)} попыток, первая — {attempts[0]}). "
        "Пропатчьте src.<модуль>.fetch_*, либо пометьте тест "
        "@pytest.mark.network, если поход в сеть — суть проверки."
    )


@pytest.fixture(autouse=True)
def _no_state_pollution():
    """Боевой state/ не должен меняться прогоном тестов.

    Сравниваем состав каталога до и после: маркер суточного сбора,
    записанный тестом, тихо подавляет настоящий сбор в этот день.
    """
    before = {p.name: p.stat().st_mtime for p in STATE_DIR.glob("*")} \
        if STATE_DIR.exists() else {}
    yield
    if not STATE_DIR.exists():
        return
    after = {p.name: p.stat().st_mtime for p in STATE_DIR.glob("*")}

    created = sorted(set(after) - set(before))
    changed = sorted(n for n in set(after) & set(before)
                     if after[n] != before[n])
    for name in created:
        (STATE_DIR / name).unlink(missing_ok=True)
    assert not created and not changed, (
        f"тест изменил боевой state/: создано {created}, изменено {changed}. "
        "Передайте tmp_path через monkeypatch (см. STATE_DIR в daily_monitor)."
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: тесту нужен реальный сетевой доступ")
