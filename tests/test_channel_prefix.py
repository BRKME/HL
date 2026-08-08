"""Метка канала на всех исходящих HL-сообщениях (08.08.2026).

Сообщения HL переехали в общий канал с Polymarket. Без метки два потока
сливаются: и там и там таблички, доллары и эмодзи направления, а решения
они требуют разных.

Метка ставится в `_send` — единственной точке, через которую проходит всё
исходящее: дневной отчёт, тактические сигналы, whale-дайджест, алерты
гварда, heartbeat, отчёт детектора журнала. Ставить её в билдерах значит
однажды завести новый тип сообщения и забыть.
"""
import pytest

from src.telegram_sender import HL_PREFIX, TG_HARD_LIMIT, with_prefix


def test_prefix_is_prepended():
    assert with_prefix("привет").startswith(HL_PREFIX)


def test_original_text_is_preserved():
    assert "📊 HL Portfolio" in with_prefix("📊 HL Portfolio")


def test_prefix_is_idempotent():
    once = with_prefix("текст")
    assert with_prefix(once) == once


def test_prefix_starts_the_message_for_notification_preview():
    """Превью уведомления показывает начало текста — метка должна быть там."""
    out = with_prefix("🐋 Whale digest за 24ч")
    assert out.index(HL_PREFIX) == 0


def test_empty_text_stays_empty():
    assert with_prefix("") == ""
    assert with_prefix(None) == ""


def test_whitespace_only_stays_empty():
    assert with_prefix("   ") == ""


def test_long_message_stays_within_telegram_limit():
    """Метка не должна выталкивать сообщение за жёсткий лимит Telegram."""
    body = "x" * (TG_HARD_LIMIT - 10)
    out = with_prefix(body)
    assert len(out) <= TG_HARD_LIMIT


def test_oversize_body_is_truncated_not_dropped():
    out = with_prefix("y" * (TG_HARD_LIMIT + 500))
    assert len(out) <= TG_HARD_LIMIT
    assert out.startswith(HL_PREFIX)


def test_html_is_not_broken_by_prefix():
    out = with_prefix("<b>жирный</b>")
    assert out.count("<b>") == out.count("</b>")


# ------------------------------------------------------ применение в _send

def test_send_applies_prefix(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["text"] = json["text"]

        class R:
            status_code = 200

            def raise_for_status(self):
                pass
        return R()

    import src.telegram_sender as ts
    monkeypatch.setattr(ts.requests, "post", fake_post)
    ts._send("token", "chat", "тело сообщения")
    assert captured["text"].startswith(HL_PREFIX)
    assert "тело сообщения" in captured["text"]


def test_owner_alerts_are_also_marked(monkeypatch):
    """Алерт владельцу приходит в тот же клиент — метка нужна и там."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["text"] = json["text"]

        class R:
            status_code = 200

            def raise_for_status(self):
                pass
        return R()

    import src.telegram_sender as ts
    monkeypatch.setattr(ts.requests, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    ts.alert_owner("❌ упал")
    assert captured["text"].startswith(HL_PREFIX)


# ------------------------------------------------ защита от обхода в будущем

def test_no_module_talks_to_telegram_directly():
    """Метка живёт в _send(). Прямой вызов API в обход неё её потеряет.

    Тест сторожит именно это: не текст префикса, а то, что точка входа
    остаётся единственной."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    allowed = {"telegram_sender.py"}
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "api.telegram.org" in text or "/sendMessage" in text:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"обход telegram_sender: {offenders}"


def test_every_sender_module_goes_through_the_choke_point(monkeypatch):
    """Реально прогоняем отправку и смотрим, что ушло в сеть."""
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json["text"])

        class R:
            status_code = 200

            def raise_for_status(self):
                pass
        return R()

    import src.telegram_sender as ts
    monkeypatch.setattr(ts.requests, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(ts, "_mark_sent", lambda: None)

    ts.send_messages(["отчёт", "второе сообщение"])
    ts.alert_owner("авария")

    assert len(sent) == 3
    assert all(t.startswith(HL_PREFIX) for t in sent)
