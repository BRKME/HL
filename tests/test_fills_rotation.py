"""Ротация заполнений китов по размеру (22.09.2026).

whale_fills.jsonl дорос до 99.7 МБ при пределе GitHub в 100 МБ на файл.
Пуш отклонялся — и не сохранялось НИКАКОЕ состояние китов: ни заполнения,
ни отметка «дайджест отправлен», ни очистка буфера. Один дайджест ушёл
семь раз за полтора дня.

Месячная ротация не срабатывала ни разу: она смотрит на время изменения
файла, а checkout в Actions выставляет его в «сейчас». Ротация по размеру
читает метки самих заполнений и от времени файла не зависит.

Оценка китов смотрит на 90 дней, живой файл теперь держит около двух
недель — поэтому загрузчик обязан читать и архивы, иначе оценки тихо
потеряют историю.
"""
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.whale_tracker import rotate_by_size

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _write(path, days_ago_list, notional=100.0):
    with path.open("w") as fh:
        for i, d in enumerate(days_ago_list):
            ts = int((NOW - timedelta(days=d)).timestamp() * 1000)
            # Полный формат строки, как пишет трекер: загрузчик оценки
            # требует все поля и молча отбрасывает неполные строки.
            fh.write(json.dumps({
                "whale": "0xa", "tid": i, "time_ms": ts, "coin": "BTC",
                "side": "B", "direction": "Open Long", "size": 1.0,
                "price": 100.0, "notional_usd": notional, "closed_pnl": 1.0,
                "crossed": True, "oid": i}) + "\n")


def _lines_in(path):
    return [l for l in path.read_text().splitlines() if l.strip()]


def _arch_lines(dirp):
    out = []
    for a in dirp.glob("whale_fills_*.jsonl.gz"):
        with gzip.open(a, "rt") as fh:
            out += [l for l in fh if l.strip()]
    return out


def test_small_file_untouched(tmp_path):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [1, 30, 60])
    assert rotate_by_size(p, NOW, max_bytes=10**9) == 0
    assert len(_lines_in(p)) == 3


def test_large_file_moves_old_keeps_recent(tmp_path):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [1, 5, 20, 40, 70])
    moved = rotate_by_size(p, NOW, max_bytes=1, keep_days=14)
    assert moved == 3
    assert len(_lines_in(p)) == 2


def test_no_line_is_lost(tmp_path):
    """Ключевое: вынесенное + оставленное = исходное. Ротация не теряет."""
    p = tmp_path / "whale_fills.jsonl"
    _write(p, list(range(0, 90, 3)))
    total = len(_lines_in(p))
    rotate_by_size(p, NOW, max_bytes=1, keep_days=14)
    assert len(_lines_in(p)) + len(_arch_lines(tmp_path)) == total


def test_archives_are_monthly_and_appendable(tmp_path):
    """Повторная ротация дописывает в тот же месяц, а не затирает."""
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [40])
    rotate_by_size(p, NOW, max_bytes=1)
    _write(p, [41])
    rotate_by_size(p, NOW, max_bytes=1)
    assert len(_arch_lines(tmp_path)) == 2


def test_does_not_depend_on_file_mtime(tmp_path):
    """Месячная ротация ломалась на mtime от checkout. Эта от него не
    зависит — только от меток внутри заполнений."""
    import os
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [40])
    os.utime(p, None)                       # «свежий» файл, как после checkout
    assert rotate_by_size(p, NOW, max_bytes=1) == 1


def test_scoring_loader_reads_archives(tmp_path):
    """Оценка смотрит на 90 дней, живой файл держит две недели: без чтения
    архивов оценки тихо потеряли бы историю."""
    from src.whale_scoring import _load_jsonl_fills

    p = tmp_path / "whale_fills.jsonl"
    _write(p, [1, 5, 40, 70])
    before = len(_load_jsonl_fills(p))
    rotate_by_size(p, NOW, max_bytes=1, keep_days=14)
    after = len(_load_jsonl_fills(p))
    assert before == after == 4


def test_corrupt_archive_does_not_crash_loader(tmp_path):
    from src.whale_scoring import _load_jsonl_fills

    (tmp_path / "whale_fills_2026-01.jsonl.gz").write_bytes(b"not gzip")
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [1])
    assert len(_load_jsonl_fills(p)) == 1


def test_push_failure_is_loud():
    """Провал пуша был молчаливым: Actions зелёный, состояние не
    сохранялось. Теперь шаг обязан падать."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1]
          / ".github/workflows/whale-monitor.yml").read_text()
    assert "exit 1" in wf and "state NOT saved" in wf


# -------- все потребители читают архивы: урезанный живой файл не теряет данных

def test_live_file_small_after_rotation():
    """14 дней в живом файле давали 87 МБ сразу после ротации — почти
    предел. 5 дней ≈ 31 МБ: запас против всплеска торговли."""
    from src.whale_tracker import KEEP_LIVE_DAYS, MAX_LIVE_BYTES

    per_day_mb = 6.2
    assert KEEP_LIVE_DAYS * per_day_mb < MAX_LIVE_BYTES / 1024 / 1024


def test_stance_sees_archived_fills(tmp_path):
    """Окно позиции китов — 7 дней, живой файл держит 5. Без архивов два
    дня выпадали бы молча."""
    from src.whale_stance import compute_stance

    p = tmp_path / "whale_fills.jsonl"
    # Объём выше порога позиции: фикстура с $100 отсекалась фильтром
    # min_notional_usd, и тест мерил фильтр, а не чтение архивов.
    _write(p, [1, 6], notional=1_000_000)
    rotate_by_size(p, NOW, max_bytes=1, keep_days=5)
    assert len(_lines_in(p)) == 1        # в живом осталось одно
    st = compute_stance(tmp_path, coins=["BTC"], now=NOW, lookback_days=7)
    assert st["BTC"].long_count == 2     # но позиция видит оба


def test_iterator_skips_archives_outside_window(tmp_path):
    """Архивы целиком раньше окна не распаковываются — это дорого."""
    from src.whale_tracker import iter_fill_lines

    p = tmp_path / "whale_fills.jsonl"
    _write(p, [1, 100])
    rotate_by_size(p, NOW, max_bytes=1, keep_days=5)
    since = int((NOW - timedelta(days=7)).timestamp() * 1000)
    assert len([l for l in iter_fill_lines(p, since) if l.strip()]) == 1


# ---------------------------------------------------------------------------
# 12.11.2026 — аудит: предел 100 МБ пробит снова, 10 раз за 21.10–01.11
# ---------------------------------------------------------------------------
# Ротация убирала только то, что старше keep_days, и считала «5 дней ≈ 31 МБ
# при 6 МБ/день». Поток вырос до 17.6 МБ/день: пять дней — 88–101 МБ, убирать
# было нечего, пуш отклонялся («whale_fills.jsonl is 100.63 MB»), состояние
# китов не сохранялось. Число дней — не ограничение размера.

def test_busy_week_is_capped_by_bytes_not_days(tmp_path):
    """Всё свежее keep_days, но файл больше предела — старейшее уходит в
    архив, пока живой файл не влезет. Ни одна строка не теряется."""
    p = tmp_path / "whale_fills.jsonl"
    days = [i / 100 for i in range(400)]          # 400 строк за 4 дня
    _write(p, days)
    size = p.stat().st_size
    cap = size // 4
    moved = rotate_by_size(p, NOW, max_bytes=cap, cap_bytes=cap, keep_days=5)
    assert p.stat().st_size <= cap
    kept = _lines_in(p)
    assert moved + len(kept) == 400
    assert len(_arch_lines(tmp_path)) == moved
    # остались самые СВЕЖИЕ
    cutoff = min(json.loads(l)["time_ms"] for l in kept)
    assert all(json.loads(l)["time_ms"] <= cutoff
               for l in _arch_lines(tmp_path))


def test_archive_rolls_over_to_parts_before_github_limit(tmp_path):
    """Месячный архив тоже файл в репозитории: октябрь — 56 МБ в gz. Новая
    часть заводится, когда текущая достигла предела."""
    from src.whale_tracker import iter_fill_lines
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [40 + i / 100 for i in range(50)])
    rotate_by_size(p, NOW, max_bytes=1, part_bytes=1)
    _write(p, [41 + i / 100 for i in range(50)])
    rotate_by_size(p, NOW, max_bytes=1, part_bytes=1)
    parts = sorted(a.name for a in tmp_path.glob("whale_fills_2026-08*.gz"))
    assert len(parts) >= 2, parts
    assert len([l for l in iter_fill_lines(p) if l.strip()]) == 100


def test_parts_respect_since_filter(tmp_path):
    from src.whale_tracker import iter_fill_lines
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [80, 81])                    # начало июля
    rotate_by_size(p, NOW, max_bytes=1, part_bytes=1)
    _write(p, [82])
    rotate_by_size(p, NOW, max_bytes=1, part_bytes=1)
    since = int((NOW - timedelta(days=10)).timestamp() * 1000)
    assert [l for l in iter_fill_lines(p, since_ms=since) if l.strip()] == []


# ---------------------------------------------------------------------------
# 12.11.2026 — очистка архивов по дате из имени (решение оператора: удалять)
# ---------------------------------------------------------------------------
# cleanup_old_archives смотрела на время изменения файла, а checkout в Actions
# ставит его в «сейчас»: очистка не сработала ни разу, майский архив лежал
# в ноябре. Та же ошибка, что у месячной ротации.

def test_cleanup_goes_by_month_in_name_not_mtime(tmp_path):
    from src.whale_tracker import cleanup_old_archives
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    names = ["whale_fills_2026-05.jsonl.gz", "whale_fills_2026-07.jsonl.gz",
             "whale_fills_2026-07.p2.jsonl.gz",
             "whale_fills_2026-08.jsonl.gz", "whale_fills_2026-11.jsonl.gz"]
    for n in names:
        (tmp_path / n).write_bytes(b"")          # mtime = сейчас, как в Actions
    removed = {p.name for p in cleanup_old_archives(tmp_path, 90, now=now)}
    # Июль кончился 01.08 — старше 90 дней (граница 14.08); август — нет.
    assert removed == {"whale_fills_2026-05.jsonl.gz",
                       "whale_fills_2026-07.jsonl.gz",
                       "whale_fills_2026-07.p2.jsonl.gz"}
    assert (tmp_path / "whale_fills_2026-08.jsonl.gz").exists()
    assert (tmp_path / "whale_fills_2026-11.jsonl.gz").exists()


def test_cleanup_handles_december_rollover(tmp_path):
    from src.whale_tracker import cleanup_old_archives
    now = datetime(2027, 4, 15, tzinfo=timezone.utc)
    (tmp_path / "whale_fills_2026-12.jsonl.gz").write_bytes(b"")
    (tmp_path / "whale_fills_2027-01.jsonl.gz").write_bytes(b"")
    removed = {p.name for p in cleanup_old_archives(tmp_path, 90, now=now)}
    assert removed == {"whale_fills_2026-12.jsonl.gz"}


def test_workflow_stages_archive_deletions():
    """Удалённый архив должен уйти из репозитория: `git add файл` для
    несуществующего файла ничего не делает, и архив воскресал бы на
    следующем checkout."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1]
          / ".github" / "workflows" / "whale-monitor.yml").read_text()
    assert "git add -A -- 'state/whale_fills_*.jsonl.gz'" in wf
