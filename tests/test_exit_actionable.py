"""EXIT-советы должны быть условными: 'закрыть перпы' только если они могли
быть открыты (прошлый вердикт — входной STRONG/MODERATE). Иначе EXIT три
недели подряд советует закрывать несуществующие позиции."""
from src.main import _had_open_position_last_week


def test_prior_strong_means_position_possible(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    p.write_text('{"signal": "STRONG"}\n', encoding="utf-8")
    monkeypatch.setattr("src.main.DECISIONS_PATH", p)
    assert _had_open_position_last_week() is True


def test_prior_skip_means_no_position(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    p.write_text('{"signal": "SKIP"}\n{"signal": "EXIT"}\n', encoding="utf-8")
    monkeypatch.setattr("src.main.DECISIONS_PATH", p)
    assert _had_open_position_last_week() is False


def test_prior_moderate_means_position(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    p.write_text('{"signal": "MODERATE"}\n', encoding="utf-8")
    monkeypatch.setattr("src.main.DECISIONS_PATH", p)
    assert _had_open_position_last_week() is True


def test_empty_history_assumes_no_position(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("src.main.DECISIONS_PATH", p)
    assert _had_open_position_last_week() is False


def test_exit_headline_honest_without_position():
    from src import render
    sig = {"signal": "EXIT", "leverage": 0, "reasons": [], "raw": {}}
    msg = render.render_report(signal=sig, picks=[], skipped=[],
                               ladder_ctx=None, had_position=False)
    assert "Остаёмся вне рынка" in msg
    assert "Закрыть открытые перпы" not in msg


def test_exit_with_position_keeps_close_advice():
    from src import render
    sig = {"signal": "EXIT", "leverage": 0, "reasons": [], "raw": {}}
    msg = render.render_report(signal=sig, picks=[], skipped=[],
                               ladder_ctx=None, had_position=True)
    assert "Закрыть открытые перпы" in msg
