"""Субботний планнер переведён в тень (24.08.2026, решение оператора).

Результат: +6.1% против +26.5% рынка за 11 недель. Оператор: «на неделю не
сделать чёткий прогноз, убрать».

Диагноз перед отключением, чтобы решение принималось со знанием причины:
последние три субботы планнер выдал EXIT, EXIT, SKIP — все с `coin: None`,
то есть кандидатов не перебирал вовсе. Причина не в качестве прогноза, а в
застрявшем `phase=EARLY_BEAR` — том самом механизме, который H3 починила
23.08 для дневного слоя и не тронула в недельном.

Поэтому тень, а не удаление: сообщений в канал нет, расчёт идёт, решения
пишутся. Метрика, на которой мы бы удаляли, две недели назад показывала
+20.3 пп и перевернулась в −20.5 — принимать необратимое решение по числу,
меняющему знак за две недели, неразумно.

Механизм не новый: `WEEKLY_SILENT` существовал в `src/main.py` и до сих пор
не был покрыт тестами. Заводить второй для той же задачи не стали.
"""
import pytest

from src.main import _maybe_send


def test_shadow_suppresses_telegram(monkeypatch):
    """Канал молчит, расчёт не выключается."""
    sent = []
    monkeypatch.setenv("WEEKLY_SILENT", "1")
    monkeypatch.setattr("src.main.telegram_sender.send_messages",
                        lambda m: sent.extend(m))
    _maybe_send(["еженедельный отчёт"])
    assert sent == []


def test_live_mode_sends(monkeypatch):
    sent = []
    monkeypatch.delenv("WEEKLY_SILENT", raising=False)
    monkeypatch.setattr("src.main.telegram_sender.send_messages",
                        lambda m: sent.extend(m))
    _maybe_send(["еженедельный отчёт"])
    assert sent == ["еженедельный отчёт"]


@pytest.mark.parametrize("val", ["0", "", "no"])
def test_only_explicit_one_silences(monkeypatch, val):
    """Тень включается строго значением «1» — чтобы опечатка не молчала."""
    sent = []
    monkeypatch.setenv("WEEKLY_SILENT", val)
    monkeypatch.setattr("src.main.telegram_sender.send_messages",
                        lambda m: sent.extend(m))
    _maybe_send(["отчёт"])
    assert sent == ["отчёт"]


def test_workflow_sets_shadow_mode():
    """Тень задана в воркфлоу, а не в коде — возврат без правки исходников."""
    import pathlib
    wf = pathlib.Path(".github/workflows/weekly.yml").read_text()
    assert "WEEKLY_SILENT" in wf


def test_alpha_line_is_not_reported():
    """Альфа мерила инвестированность, а не качество выбора: за две недели
    знак перевернулся при неизменной доходности планнера. Расчёт остаётся,
    в сводку не идёт."""
    from src.weekly_kpi import _advisor_kpi_line
    assert _advisor_kpi_line() is None


def test_alpha_is_still_computable():
    """Функция расчёта жива — «а если бы» остаётся измеримым."""
    from src.weekly_kpi import advisor_alpha
    assert callable(advisor_alpha)
