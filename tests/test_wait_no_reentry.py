"""Проход через WAIT не должен порождать ложный перевход.

Баг 21.06: BTC держал SHORT, мелькнул WAIT (нейтраль), вернулся в SHORT —
и система прислала 'новый' SHORT-алерт + сбросила last_change_ts, хотя
позиционно разворота не было (SHORT→WAIT→SHORT). Алерт должен приходить
только на смену между ДЕЙСТВИЯМИ (LONG<->SHORT), а WAIT — это пауза, не
противоположный вердикт.
"""
from datetime import datetime, timezone, timedelta
from src.tactical_signals import should_emit

NOW = datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=3)).isoformat()


def test_short_after_wait_not_reemitted():
    # последний ДЕЙСТВЕННЫЙ вердикт был SHORT; сейчас снова SHORT
    # (между ними был WAIT) -> НЕ алертим
    assert should_emit("SHORT", "SHORT", OLD, NOW) is False


def test_genuine_flip_still_emits():
    assert should_emit("SHORT", "LONG", OLD, NOW) is True
    assert should_emit("LONG", "SHORT", OLD, NOW) is True


def test_wait_itself_never_emits():
    assert should_emit("WAIT", "SHORT", OLD, NOW) is False


def test_state_transition_short_wait_short(monkeypatch):
    """Сценарий целиком на уровне состояния: SHORT (эмит) -> WAIT (пауза) ->
    SHORT снова. Второй SHORT НЕ должен эмититься и НЕ сбрасывать change_ts."""
    # модель обновления состояния, как в основном цикле
    def update(state, coin, verdict, now, emitted):
        st = state.get(coin, {})
        if emitted:
            return {"last_verdict": verdict, "last_action_verdict": verdict,
                    "last_alert_ts": now, "last_change_ts": now}
        prev_action = st.get("last_action_verdict") or st.get("last_verdict")
        changed = st.get("last_change_ts")
        new_action = prev_action
        if verdict in ("LONG", "SHORT") and prev_action != verdict:
            changed = now; new_action = verdict
        return {**st, "last_verdict": verdict, "last_action_verdict": new_action,
                "last_change_ts": changed}

    state = {}
    # день 1: SHORT, эмит (смена с пусто)
    state["BTC"] = update(state, "BTC", "SHORT", "2026-06-18T00:00:00+00:00", True)
    change_after_short = state["BTC"]["last_change_ts"]
    # день 2: WAIT, не эмит
    state["BTC"] = update(state, "BTC", "WAIT", "2026-06-20T00:00:00+00:00", False)
    # день 3: SHORT снова — prev_action всё ещё SHORT -> should_emit False
    prev_action = state["BTC"].get("last_action_verdict")
    assert prev_action == "SHORT"             # WAIT не затёр действенный вердикт
    from src.tactical_signals import should_emit
    assert should_emit("SHORT", prev_action,
                       "2026-06-18T00:00:00+00:00",
                       datetime(2026, 6, 21, tzinfo=timezone.utc)) is False
    state["BTC"] = update(state, "BTC", "SHORT", "2026-06-21T00:00:00+00:00", False)
    # change_ts НЕ сбросился — «без смены» считается от первого SHORT
    assert state["BTC"]["last_change_ts"] == change_after_short
