"""Контракт между кодом и воркфлоу (27.08.2026).

Из шести дефектов, внесённых мной за август, четыре легли в одно место:
воркфлоу и детектор. Это единственная часть системы, которую не проверял
никто. 999 тестов покрывают `src/`, но не покрывают ни YAML, ни договор
«код пишет файл — воркфлоу его коммитит».

Дважды подряд одна и та же ошибка: `morning_digest.json` (03.08) и
`verdict_collection.json` (21.08) писались на диск и не коммитились. В
Actions рабочая директория прогон не переживает, поэтому маркер терялся, и
суточный гейт открывался на каждом тике. Второй раз — через две недели
после того, как я записал это правило в политику §4.

Правило, которое нельзя соблюдать вниманием, надо проверять машиной.
"""
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# Файлы состояния, которые сознательно НЕ коммитятся: они либо целиком
# переживают прогон в другом месте, либо являются отладочными.
EPHEMERAL = {
    "_sl_debug.json",       # отладочный дамп, коммитится отдельным шагом
    "lp_advisor_report.json",
    "last_output.json",
    "cycle_ladder.json",
}


def _all_workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOWS)


def _state_files_written_by_code() -> set[str]:
    # Скрипты тоже пишут состояние (positioning.jsonl заводится в
    # scripts/positioning_run.py). Проверка, смотревшая только в src/,
    # объявляла такой файл «коммитим то, чего никто не пишет».
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for folder in ("src", "scripts")
        for p in (ROOT / folder).rglob("*.py"))
    # Файл может писаться и как "state/x.json", и как state_dir / "x.json",
    # поэтому ищем любое имя файла состояния в кавычках, а не только с
    # префиксом каталога — иначе проверка даёт ложные пропуски.
    # Имя может встречаться как "state/x.json", как state_dir / "x.json" и
    # с ведущим подчёркиванием. Ловим все три формы: узкий шаблон давал
    # ложные срабатывания и обесценивал проверку.
    pattern = r'["\'](?:state/)?(_?[a-z][a-z_0-9]*\.(?:json|jsonl|txt))["\']'
    found = set(re.findall(pattern, src))
    found |= set(re.findall(r'(?:STATE_FILE|MARKER)\s*=\s*"([^"]+)"', src))
    return found


# ------------------------------------------------------------- YAML цел

def test_every_workflow_parses():
    for p in WORKFLOWS:
        yaml.safe_load(p.read_text(encoding="utf-8"))


def test_every_workflow_has_a_trigger():
    for p in WORKFLOWS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        # ключ `on` YAML разбирает как булево True
        trig = d.get(True) or d.get("on")
        assert trig, f"{p.name}: нет триггеров"


def test_scheduled_crons_have_five_fields():
    for p in WORKFLOWS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        trig = d.get(True) or d.get("on") or {}
        for entry in (trig.get("schedule") or []):
            cron = entry["cron"]
            assert len(cron.split()) == 5, f"{p.name}: битый cron «{cron}»"


def test_every_workflow_has_at_least_one_step():
    for p in WORKFLOWS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        for name, job in (d.get("jobs") or {}).items():
            assert job.get("steps"), f"{p.name}/{name}: нет шагов"


# ------------------------------------------- точки входа существуют

def test_every_entrypoint_exists():
    missing = []
    for p in WORKFLOWS:
        text = p.read_text(encoding="utf-8")
        for mod in re.findall(r"python3?\s+-m\s+(src\.[\w.]+)", text):
            if not (ROOT / (mod.replace(".", "/") + ".py")).exists():
                missing.append(f"{p.name}: -m {mod}")
        for script in re.findall(r"python3?\s+(scripts/[\w./-]+\.py)", text):
            if not (ROOT / script).exists():
                missing.append(f"{p.name}: {script}")
    assert not missing, f"воркфлоу зовёт несуществующее: {missing}"


# --------------------------------- договор «пишем → коммитим»

def test_every_written_state_file_is_committed_somewhere():
    """Файл, который код пишет, обязан кем-то коммититься.

    Дважды подряд именно здесь: маркер писался, не коммитился, терялся
    между прогонами Actions и открывал суточный гейт на каждом тике.
    """
    wf = _all_workflow_text()
    # Ищем ИМЕННО в `git add`, а не где угодно в тексте: первая версия
    # проверки засчитывала упоминание в комментарии и в `if [ -f ... ]`,
    # то есть пропускала ровно ту ошибку, ради которой писалась.
    added = " ".join(re.findall(r'git add ([^\n]*)', wf))
    # Часть воркфлоу перечисляет файлы в bash-массиве и добавляет их циклом
    # `for f in "${STATE_FILES[@]}"`. Такие строки — тоже коммит, просто
    # записанный иначе.
    arrays = " ".join(re.findall(r'STATE_FILES=\(([^)]*)\)', wf, re.S))
    looped = " ".join(re.findall(r'for f in ([^\n;]+)', wf)) + " " + arrays

    uncommitted = sorted(
        f for f in _state_files_written_by_code()
        if f not in EPHEMERAL
        and f not in added
        and f not in looped
    )
    assert not uncommitted, (
        f"код пишет, но ни один воркфлоу не коммитит: {uncommitted}. "
        "Добавьте в `git add` соответствующего воркфлоу либо в EPHEMERAL "
        "с объяснением, почему файл переживать прогон не должен."
    )


def test_committed_state_files_are_actually_written():
    """Обратная сторона: не коммитим то, чего никто не пишет.

    Ссылка на исчезнувший файл — тихий мусор в воркфлоу, который потом
    принимают за рабочий механизм.
    """
    wf = _all_workflow_text()
    referenced = set(re.findall(r'git add (?:")?state/([a-z_]+\.\w+)', wf))
    written = _state_files_written_by_code()
    ghosts = sorted(f for f in referenced if f not in written)
    assert not ghosts, f"воркфлоу коммитит то, чего код не пишет: {ghosts}"


# ------------------------------------------------ шаг коммита состоятелен

def test_commit_steps_configure_git_identity():
    """Коммит без user.name падает в Actions, а падает он В КОНЦЕ прогона —
    когда работа уже сделана и молча пропала."""
    for p in WORKFLOWS:
        text = p.read_text(encoding="utf-8")
        if "git commit" in text:
            assert "user.name" in text, f"{p.name}: git commit без identity"
