"""Heartbeat: 'жив + режим/фаза + есть ли позиции', но ТОЛЬКО если за сутки
в канал ничего не отправлялось. В дни с сигналами/отчётами молчит.
"""
from datetime import datetime, timedelta, timezone

from src import heartbeat as hb

NOW = datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)


class TestShouldSend:
    def test_silent_24h_sends(self):
        last = (NOW - timedelta(hours=25)).isoformat()
        assert hb.should_send_heartbeat(last_send_ts=last, now=NOW) is True

    def test_recent_send_suppresses(self):
        last = (NOW - timedelta(hours=3)).isoformat()
        assert hb.should_send_heartbeat(last_send_ts=last, now=NOW) is False

    def test_never_sent_sends(self):
        assert hb.should_send_heartbeat(last_send_ts=None, now=NOW) is True

    def test_exactly_24h_sends(self):
        last = (NOW - timedelta(hours=24)).isoformat()
        assert hb.should_send_heartbeat(last_send_ts=last, now=NOW) is True


class TestText:
    def test_text_has_regime_phase_positions(self):
        msg = hb.build_heartbeat(regime="BEAR", phase="CAPITULATION",
                                 has_positions=False, now=NOW)
        assert "BEAR" in msg
        assert "CAPITULATION" in msg
        assert "позиц" in msg.lower()
        assert "✅" in msg or "жив" in msg.lower() or "работает" in msg.lower()

    def test_text_positions_flag(self):
        msg = hb.build_heartbeat(regime="BULL", phase="MARKUP",
                                 has_positions=True, now=NOW)
        assert "есть" in msg.lower() or "открыт" in msg.lower()

    def test_degrades_without_snapshot(self):
        msg = hb.build_heartbeat(regime=None, phase=None,
                                 has_positions=False, now=NOW)
        assert msg  # всё равно подтверждает, что жив
        assert "n/a" in msg.lower() or "нет данных" in msg.lower() or "?" in msg


class TestHeartbeatResetsTimer:
    def test_heartbeat_send_marks_last_send(self, monkeypatch, tmp_path):
        """Heartbeat — тоже сообщение в канал: идёт через send_messages и
        обновляет last-send, поэтому следующий heartbeat только после новых
        24ч тишины (защита от задвоения при повторном прогоне)."""
        import src.telegram_sender as ts
        marker = tmp_path / "last_channel_send.txt"
        monkeypatch.setattr(ts, "_LAST_SEND_PATH", str(marker))
        monkeypatch.setattr(ts, "_send", lambda *a, **k: None)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
        ts.send_messages(["✅ HL бот жив"])
        assert marker.exists()        # heartbeat пометил таймер

