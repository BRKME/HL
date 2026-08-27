#!/usr/bin/env python3
"""Комплексная проверка перед любым пушем. Одна команда вместо дисциплины.

Заведено 28.08.2026 по требованию оператора: «проверяй всё комплексно после
любых изменений».

Почему скриптом, а не памяткой. За август из 15 дефектов 6 внёс я сам, и
четыре из них — в воркфлоу, единственном месте без автоматических проверок.
Одну и ту же ошибку с некоммитящимся маркером я сделал трижды, причём
второй раз — через две недели после того, как сам записал это правило в
политику. Правило, которое не держится вниманием, надо проверять машиной.

Что проверяется:
  1. тесты зелёные;
  2. тесты зелёные ВТОРОЙ раз подряд — ловит утечки состояния между
     прогонами, из-за которых набор бывает зелёным через раз;
  3. боевой state/ не изменён прогоном тестов;
  4. журнал вердиктов здоров (детектор тихого отказа);
  5. рабочее дерево не содержит секретов;
  6. все модули импортируются;
  7. YAML воркфлоу разбирается и контракт «пишем → коммитим» соблюдён
     (это же покрыто тестами, но проверяется отдельно: сообщение об
     ошибке здесь понятнее).

Коды возврата: 0 — можно пушить, 1 — нельзя.
Журнал может быть несвежим по независящим причинам, поэтому пункт 4
предупреждает, но не блокирует.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {"TELEGRAM_BOT_TOKEN": "stub", "TELEGRAM_CHAT_ID": "stub", "PATH": "/usr/bin:/bin:/usr/local/bin"}

OK, WARN, FAIL = "  OK  ", "  ⚠️  ", "  FAIL"


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=ENV)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_tests_twice() -> tuple[bool, str]:
    for attempt in (1, 2):
        code, out = _run([sys.executable, "-m", "pytest", "tests/", "-q"])
        if code != 0:
            tail = [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))]
            return False, f"прогон {attempt} красный: " + "; ".join(tail[:3])
    last = [l for l in out.splitlines() if "passed" in l]
    return True, last[-1].strip() if last else "зелено дважды"


def check_state_untouched() -> tuple[bool, str]:
    code, out = _run(["git", "status", "--porcelain", "state/"])
    dirty = [l for l in out.splitlines() if l.strip()]
    if dirty:
        return False, f"прогон тестов изменил боевой state/: {dirty[:3]}"
    return True, "боевой state/ чист"


def check_journal_health() -> tuple[bool, str]:
    code, out = _run([sys.executable, "scripts/journal_healthcheck.py"])
    warns = [l.strip() for l in out.splitlines() if "⚠️" in l or "🔴" in l]
    if warns:
        return True, "детектор: " + warns[0][:80] + (f" (+{len(warns)-1})" if len(warns) > 1 else "")
    return True, "журнал здоров"


def check_no_secrets() -> tuple[bool, str]:
    code, out = _run(["git", "grep", "-nE", "github_pat_|ghp_|-----BEGIN", "--",
                      "*.py", "*.yml", "*.md", "*.sh"])
    if out.strip():
        return False, "секрет в рабочем дереве: " + out.splitlines()[0][:80]
    return True, "секретов нет"


def check_imports() -> tuple[bool, str]:
    snippet = (
        "import importlib, pkgutil\n"
        "bad = []\n"
        "for m in pkgutil.iter_modules(['src']):\n"
        "    try:\n"
        "        importlib.import_module('src.' + m.name)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{m.name}: {e}')\n"
        "print('|'.join(bad))\n"
    )
    code, out = _run([sys.executable, "-c", snippet])
    bad = out.strip()
    if bad:
        return False, f"модули не импортируются: {bad[:100]}"
    return True, "все модули импортируются"


def check_workflows() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-m", "pytest",
                      "tests/test_workflow_contract.py", "-q"])
    if code != 0:
        msg = [l for l in out.splitlines() if "AssertionError" in l]
        return False, msg[0][:140] if msg else "контракт воркфлоу нарушен"
    return True, "контракт воркфлоу соблюдён"


CHECKS = [
    ("тесты, два прогона подряд", check_tests_twice, True),
    ("боевой state/ не тронут", check_state_untouched, True),
    ("контракт воркфлоу", check_workflows, True),
    ("импорт модулей", check_imports, True),
    ("секреты", check_no_secrets, True),
    ("здоровье журнала", check_journal_health, False),
]


def main() -> int:
    print("# Preflight\n")
    failed = []
    for name, fn, blocking in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"проверка упала: {e}"
        mark = OK if ok else (FAIL if blocking else WARN)
        print(f"{mark} {name:28} {detail}")
        if not ok and blocking:
            failed.append(name)

    print()
    if failed:
        print(f"НЕЛЬЗЯ ПУШИТЬ: {', '.join(failed)}")
        return 1
    print("Можно пушить.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
