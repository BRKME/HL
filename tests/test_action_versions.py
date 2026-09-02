"""Версии действий GitHub во всех воркфлоу (02.09.2026).

Actions предупредил о выводе Node 20 из эксплуатации: `actions/checkout@v4`
и `actions/setup-python@v5` работают на нём. Прогоны пока не падают —
раннер подставляет Node 24, — но однажды поддержку снимут, и упадут ВСЕ
шестнадцать воркфлоу разом, включая сбор данных.

Проверено по `action.yml`: checkout@v5 и setup-python@v6 объявляют
`using: node24`. Это консервативный минимум — не самые новые версии, а
первые, где проблема снята.

Тест держит версии едиными: раньше при правке одного воркфлоу второй
молча оставался на старом (свечи в дайджест, маркер в git add — тот же
класс ошибки, третий раз за неделю).
"""
import pathlib
import re

WORKFLOWS = sorted(
    (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows")
    .glob("*.yml"))

MIN_VERSIONS = {"actions/checkout": 5, "actions/setup-python": 6}


def _used_versions() -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for f in WORKFLOWS:
        for action, ver in re.findall(r"uses:\s+([\w-]+/[\w-]+)@v(\d+)",
                                      f.read_text(encoding="utf-8")):
            out.setdefault(action, set()).add(int(ver))
    return out


def test_no_action_runs_on_deprecated_node():
    used = _used_versions()
    stale = {a: sorted(v) for a, v in used.items()
             if a in MIN_VERSIONS and min(v) < MIN_VERSIONS[a]}
    assert not stale, (
        f"версии на Node 20: {stale}. Минимум — "
        f"{MIN_VERSIONS}: при отключении Node 20 упадут все воркфлоу разом.")


def test_versions_are_consistent_across_workflows():
    """Одно действие — одна версия во всех воркфлоу."""
    split = {a: sorted(v) for a, v in _used_versions().items() if len(v) > 1}
    assert not split, f"версии разъехались: {split}"


def test_every_workflow_pins_a_major_version():
    for f in WORKFLOWS:
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "uses:" in line and "@" in line:
                assert re.search(r"@(v\d+|[0-9a-f]{40})", line), (
                    f"{f.name}: версия действия не закреплена — «{line.strip()}»")
