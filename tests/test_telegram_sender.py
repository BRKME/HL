"""Tests for src/telegram_sender.py — lazy env reads avoid import-time crashes."""
import importlib
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _restore_telegram_sender():
    """Snapshot src.telegram_sender, restore after each test.

    Tests in this file reimport telegram_sender to test lazy-env behavior;
    that mutates sys.modules and can cause sibling tests in other files
    (which patch send_messages on a cached module) to break.
    """
    saved = sys.modules.get("src.telegram_sender")
    yield
    if saved is not None:
        sys.modules["src.telegram_sender"] = saved


def test_telegram_sender_imports_without_env_set(monkeypatch):
    """The whole point of the fix: importing telegram_sender (directly or
    transitively from daily_monitor / whale_monitor) must NOT crash when
    TG env vars are missing."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    # Force a fresh import in this env
    if "src.telegram_sender" in sys.modules:
        del sys.modules["src.telegram_sender"]
    # should not raise
    import src.telegram_sender  # noqa: F401


def test_daily_monitor_imports_without_env(monkeypatch):
    """Same chain as the failing CI: whale_monitor → daily_monitor →
    telegram_sender. None of these imports should crash without env.

    We restore modules after to avoid affecting subsequent tests that
    patch send_messages — patching a freshly-imported copy of
    whale_monitor would miss because tests patch the cached module.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    saved = {}
    for mod in ("src.telegram_sender", "src.daily_monitor", "src.whale_monitor"):
        if mod in sys.modules:
            saved[mod] = sys.modules.pop(mod)
    try:
        import src.daily_monitor  # noqa: F401
        import src.whale_monitor  # noqa: F401
    finally:
        # Restore the originals so test ordering doesn't break sibling tests
        for mod, m in saved.items():
            sys.modules[mod] = m


def test_send_messages_raises_clear_error_without_env(monkeypatch):
    """Actual send attempt without env → clear error, not cryptic KeyError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    if "src.telegram_sender" in sys.modules:
        del sys.modules["src.telegram_sender"]
    import src.telegram_sender as ts
    with pytest.raises(ts.TelegramConfigError):
        ts.send_messages(["hello"])


def test_send_messages_works_when_env_set(monkeypatch):
    """Happy path: env present → send succeeds (mocked HTTP)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    if "src.telegram_sender" in sys.modules:
        del sys.modules["src.telegram_sender"]
    import src.telegram_sender as ts

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    with patch("src.telegram_sender.requests.post", return_value=mock_resp) as mock_post:
        ts.send_messages(["hello"])
        # one call to Telegram, correct URL prefix
        assert mock_post.called
        url = mock_post.call_args.args[0]
        assert "test_token" in url
        assert url.endswith("/sendMessage")


def test_alert_owner_falls_back_to_main_chat_when_owner_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("TELEGRAM_OWNER_CHAT_ID", raising=False)
    if "src.telegram_sender" in sys.modules:
        del sys.modules["src.telegram_sender"]
    import src.telegram_sender as ts

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    with patch("src.telegram_sender.requests.post", return_value=mock_resp) as mock_post:
        ts.alert_owner("oops")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_id"] == "12345"


def test_alert_owner_uses_owner_chat_when_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "67890")
    if "src.telegram_sender" in sys.modules:
        del sys.modules["src.telegram_sender"]
    import src.telegram_sender as ts

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    with patch("src.telegram_sender.requests.post", return_value=mock_resp) as mock_post:
        ts.alert_owner("oops")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_id"] == "67890"


def test_mark_sent_protects_live_state_under_pytest():
    """_mark_sent в pytest НЕ пишет боевой state/last_channel_send.txt:
    тестовый прогон обновлял timestamp -> heartbeat считал канал живым и
    молчал дольше положенного (класс бага calibration_table из Polymarket).
    Подменённый через monkeypatch путь (tmp) при этом писаться ДОЛЖЕН —
    это проверяет test_heartbeat_send_marks_last_send."""
    import os
    import src.telegram_sender as ts
    p = ts._LAST_SEND_PATH
    before = open(p).read() if os.path.exists(p) else None
    ts._mark_sent()
    after = open(p).read() if os.path.exists(p) else None
    assert before == after, "_mark_sent тронул боевой файл во время pytest"


def test_mark_sent_writes_when_path_injected(tmp_path, monkeypatch):
    """Инжектированный путь пишется даже в pytest (нужно heartbeat-тестам)."""
    import src.telegram_sender as ts
    fake = tmp_path / "last_channel_send.txt"
    monkeypatch.setattr(ts, "_LAST_SEND_PATH", str(fake))
    ts._mark_sent()
    assert fake.exists()
