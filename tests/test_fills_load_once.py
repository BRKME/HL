"""Заполнения читаются с диска один раз за прогон (17.11.2026).

whale-monitor падал по таймауту (12 мин) в половине прогонов 11–17.11:
_load_jsonl_fills разбирает живой файл и ВСЕ архивы (~3.1 млн строк,
~30 с) и вызывался заново для каждого кита — 26 китов ≈ 12 минут. Отменённый
прогон не сохраняет состояние вовсе. Кэш по (размер, mtime) каждого файла:
ускорение, логика оценки не меняется. Изменённый файл — перечитывается.
"""
import gzip
from pathlib import Path

import src.whale_scoring as ws
from src.whale_tracker import WhaleFill


def _fill(tid, whale="0xa"):
    return WhaleFill(whale=whale, coin="BTC", side="B", direction="Open Long",
                     size=1.0, price=1.0, notional_usd=1.0, tid=tid,
                     time_ms=1_790_000_000_000 + tid, closed_pnl=0.0,
                     crossed=True, oid=tid)


def _write(p: Path, fills):
    p.write_text("".join(f.to_json_line() + "\n" for f in fills))


def _count_parses(monkeypatch):
    calls = {"n": 0}
    real = ws._line_to_fill

    def counting(line):
        calls["n"] += 1
        return real(line)
    monkeypatch.setattr(ws, "_line_to_fill", counting)
    return calls


def test_second_read_does_not_reparse(tmp_path, monkeypatch):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [_fill(i) for i in range(5)])
    calls = _count_parses(monkeypatch)
    a = ws._load_jsonl_fills(p)
    b = ws._load_jsonl_fills(p)
    assert len(a) == len(b) == 5
    assert calls["n"] == 5


def test_appended_line_is_seen(tmp_path):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [_fill(1)])
    assert len(ws._load_jsonl_fills(p)) == 1
    with p.open("a") as fh:
        fh.write(_fill(2).to_json_line() + "\n")
    assert len(ws._load_jsonl_fills(p)) == 2


def test_new_archive_is_seen(tmp_path):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [_fill(1)])
    assert len(ws._load_jsonl_fills(p)) == 1
    with gzip.open(tmp_path / "whale_fills_2026-10.jsonl.gz", "wt") as fh:
        fh.write(_fill(2).to_json_line() + "\n")
    assert len(ws._load_jsonl_fills(p)) == 2


def test_caller_cannot_corrupt_cache(tmp_path):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [_fill(1)])
    ws._load_jsonl_fills(p).clear()
    assert len(ws._load_jsonl_fills(p)) == 1


def test_scoring_many_whales_parses_once(tmp_path, monkeypatch):
    p = tmp_path / "whale_fills.jsonl"
    _write(p, [_fill(i, whale=f"0x{i % 3}") for i in range(30)])
    calls = _count_parses(monkeypatch)

    class NoApi:
        def get_user_fills_by_time(self, **kw):
            return []
    from datetime import datetime, timezone
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    for w in ("0x0", "0x1", "0x2"):
        ws.load_fills_for_whale(w, tmp_path, NoApi(), now=now)
    assert calls["n"] == 30
