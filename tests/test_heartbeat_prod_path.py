"""«Итог дня» в проде: табель и незнание о позициях (30.09.2026).

Лог heartbeat 30.09: `scorecard n/a: name 'scorecard' is not defined`.
Функция стояла НИЖЕ `if __name__ == "__main__": main()`, и при запуске
`python -m src.heartbeat` main() выполнялся раньше, чем она объявлена.
Тесты импортируют модуль целиком и этого не видят — поэтому сторож ниже
проверяет все модули, а не один heartbeat.

Второе: при отказе загрузки портфеля письмо писало «позиций нет (вне
рынка)» — незнание выдавалось за факт.
"""
import ast
import pathlib

import src.heartbeat as hb
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 30, 14, 36, tzinfo=timezone.utc)


def test_no_definitions_after_main_guard():
    """Всё, что объявлено после `if __name__ == "__main__"`, при запуске
    скриптом не существует в момент вызова main()."""
    bad = []
    for p in sorted(ROOT.glob("src/*.py")) + sorted(ROOT.glob("scripts/*.py")):
        body = ast.parse(p.read_text()).body
        for i, node in enumerate(body):
            if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
                bad += [f"{p.name}:{getattr(n, 'name', type(n).__name__)}"
                        for n in body[i + 1:]]
    assert bad == [], bad


def test_unknown_positions_are_not_reported_as_none():
    msg = hb.build_heartbeat("BEAR", "MID_BULL", None, NOW)
    assert "позиций нет" not in msg
    assert "позиции: нет данных" in msg


def test_known_flat_still_says_flat():
    msg = hb.build_heartbeat("BEAR", "MID_BULL", False, NOW)
    assert "позиций нет (вне рынка)" in msg
