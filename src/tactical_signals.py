"""Событийные тактические сигналы: «система поймала движение» → пуш в Telegram.

Слой над вердиктным движком (eth_focus.compute_verdict_pair):
  • алерт уходит в момент СМЕНЫ финального вердикта на LONG/SHORT — не
    дайджестом и не каждые 2 часа по кругу (память в state/tactical_state.json,
    кулдаун на монету);
  • фандинг — уже внутри вердикта (экстремум = exhaustion-флаг) и явно в тексте;
  • киты — ФИЛЬТР ЭМИССИИ и контекст, не компонент вердикта: вес китов в
    вердикте сознательно обнулён до валидации, и A/B-журнал raw-vs-final
    остаётся чистым. Сигнал LONG не уходит против нетто-SHORT китов (и наоборот);
  • иерархия стратегия→тактика наследуется автоматически: финальный вердикт
    проходит enforce_hierarchy (BULL → SHORT невозможен в принципе).

Источники — только Hyperliquid + OracAI: свечи и фандинг из HL info API,
киты из state/whale_signals.jsonl (whale_monitor, каждые 4ч), режим из OracAI.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = _REPO_ROOT / "state" / "tactical_state.json"
WHALE_SIGNALS_PATH = _REPO_ROOT / "state" / "whale_signals.jsonl"
TACTICAL_JOURNAL = _REPO_ROOT / "state" / "tactical_journal.jsonl"


def _append_tactical_journal(row: dict) -> None:
    """KPI-журнал: каждый эмитированный сигнал и каждый подавленный (shadow).
    Сигнал без измеренного исхода — шум; исходы считает tactical_eval."""
    try:
        with TACTICAL_JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[tactical] journal write failed: {e}")

# Монеты тактического слоя. Сознательно узко: качество сигнала важнее охвата;
# киты трекаются в основном на мейджорах.
TACTICAL_COINS = [c.strip() for c in
                  os.environ.get("TACTICAL_COINS", "BTC,ETH").split(",") if c.strip()]

COOLDOWN_HOURS = 12          # не чаще одного алерта на монету за этот срок
WHALE_LOOKBACK_HOURS = 48    # окно свежести whale-сигналов
WHALE_MIN_NOTIONAL = 10_000  # $ — мелочь не формирует stance (ZEC-флипы по $176)
WHALE_MIN_COUNT = 2          # минимум согласных сигналов для stance


# ── киты ─────────────────────────────────────────────────────────────────────

def _signal_side(sig: dict) -> Optional[str]:
    d = sig.get("details") or {}
    side = d.get("to_side") or d.get("direction") or d.get("whale_side")
    if isinstance(side, str):
        s = side.lower()
        if s in ("long", "short"):
            return s
    return None


def whale_stance(signals: list[dict], coin: str,
                 now: datetime) -> Optional[str]:
    """Нетто-позиция китов по монете за последние WHALE_LOOKBACK_HOURS.

    'long' / 'short' при явном перевесе (и достаточном notional), иначе None
    (смешанно / нет данных / шум) — None НЕ блокирует сигнал.
    """
    cutoff = now - timedelta(hours=WHALE_LOOKBACK_HOURS)
    longs = shorts = 0
    long_usd = short_usd = 0.0
    for s in signals:
        if s.get("coin") != coin:
            continue
        try:
            ts = datetime.fromisoformat(str(s.get("run_ts", "")))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        notional = float((s.get("details") or {}).get("notional_usd") or 0)
        if notional < WHALE_MIN_NOTIONAL:
            continue
        side = _signal_side(s)
        if side == "long":
            longs += 1
            long_usd += notional
        elif side == "short":
            shorts += 1
            short_usd += notional
    if longs >= WHALE_MIN_COUNT and longs > shorts:
        return "long"
    if shorts >= WHALE_MIN_COUNT and shorts > longs:
        return "short"
    return None


def whale_stance_note(signals: list[dict], coin: str, now: datetime,
                      stance: Optional[str]) -> str:
    if stance is None:
        return "киты: смешанно/нет данных"
    cutoff = now - timedelta(hours=WHALE_LOOKBACK_HOURS)
    n = usd = 0
    for s in signals:
        if s.get("coin") != coin:
            continue
        try:
            if datetime.fromisoformat(str(s.get("run_ts", ""))) < cutoff:
                continue
        except ValueError:
            continue
        notional = float((s.get("details") or {}).get("notional_usd") or 0)
        if notional >= WHALE_MIN_NOTIONAL and _signal_side(s) == stance:
            n += 1
            usd += notional
    return f"киты нетто-{stance.upper()} ({n} сигн., ${usd/1000:.0f}k за 48ч)"


def whale_filter(direction: str, stance: Optional[str]) -> tuple[bool, str]:
    """Сигнал не уходит против явной нетто-позиции китов."""
    if stance == "short" and direction == "LONG":
        return False, "сигнал LONG подавлен: киты нетто-SHORT"
    if stance == "long" and direction == "SHORT":
        return False, "сигнал SHORT подавлен: киты нетто-LONG"
    return True, ""


# ── эмиссия ──────────────────────────────────────────────────────────────────

def tactical_state_summary(state: dict, now: datetime) -> str:
    """Краткая сводка текущих вердиктов для heartbeat: что система «думает»
    сейчас и сколько держит без смены. Делает молчание читаемым — оператор
    видит, что слой держит позицию-мнение, а не завис.
    """
    if not state:
        return "Тактика: вердиктов пока нет"
    bits = []
    for coin in sorted(state):
        st = state[coin] or {}
        # действенный вердикт (LONG/SHORT), а не мелькнувший WAIT
        v = st.get("last_action_verdict") or st.get("last_verdict", "?")
        ch = st.get("last_change_ts")
        days_txt = ""
        if ch:
            try:
                d = (now - datetime.fromisoformat(ch)).days
                days_txt = f", без смены {d}д"
            except ValueError:
                pass
        bits.append(f"{coin} {v}{days_txt}")
    return "Тактика: " + " · ".join(bits)


def should_emit(verdict: str, prev_verdict: Optional[str],
                last_alert_ts: Optional[str], now: datetime) -> bool:
    """Алерт = смена вердикта на действие + кулдаун. WAIT не алертится."""
    if verdict not in ("LONG", "SHORT"):
        return False
    if verdict == (prev_verdict or ""):
        return False
    if last_alert_ts:
        try:
            last = datetime.fromisoformat(last_alert_ts)
            if (now - last) < timedelta(hours=COOLDOWN_HOURS):
                return False
        except ValueError:
            pass
    return True


def sl_for(direction: str, entry: float, atr: Optional[float],
           swing_low: Optional[float], swing_high: Optional[float]) -> Optional[float]:
    """SL: за 2·ATR, но не дальше ближайшего swing-уровня."""
    if not entry or entry <= 0:
        return None
    a = float(atr or 0)
    if direction == "LONG":
        base = entry - 2 * a if a else None
        cands = [c for c in (base, swing_low) if c]
        return round(max(cands), 6) if cands else None
    if direction == "SHORT":
        base = entry + 2 * a if a else None
        cands = [c for c in (base, swing_high) if c]
        return round(min(cands), 6) if cands else None
    return None


def build_alert(*, coin: str, direction: str, entry: float, sl: Optional[float],
                rationale: str, funding_apr_pct: Optional[float],
                whale_note: str, regime: Optional[str]) -> str:
    emoji = "🟢" if direction == "LONG" else "🔴"
    f_txt = (f"фандинг {funding_apr_pct:+.1f}% APR"
             if funding_apr_pct is not None else "фандинг n/a")
    sl_txt = f"SL {sl:,.0f}" if sl and sl >= 100 else (f"SL {sl}" if sl else "SL вручную")
    lines = [
        f"{emoji} ТАКТИКА: {direction} {coin} @ {entry:,.0f}" if entry >= 100
        else f"{emoji} ТАКТИКА: {direction} {coin} @ {entry}",
        f"{sl_txt} · {f_txt} · режим {regime or '?'}",
        f"→ {rationale}",
        f"{whale_note}",
        "Горизонт: дни. Размер — тактический, не из лестницы.",
    ]
    return "\n".join(lines)



def correlation_note(directions: list[str]) -> Optional[str]:
    """BTC и ETH в одну сторону за один проход — одна бета-ставка дважды."""
    acts = [d for d in directions if d in ("LONG", "SHORT")]
    if len(acts) >= 2 and len(set(acts)) == 1:
        return ("⚠️ Несколько монет в одну сторону за проход — это одна "
                "бета-ставка на рынок: дели тактический размер между ними, "
                "не удваивай риск.")
    return None


# ── состояние и оркестрация ─────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _load_whale_signals() -> list[dict]:
    if not WHALE_SIGNALS_PATH.exists():
        return []
    out = []
    for line in WHALE_SIGNALS_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def run() -> list[str]:
    """Один проход: вердикты по TACTICAL_COINS → событийные алерты.

    Возвращает список отправленных сообщений (для логов/тестов).
    """
    from src import hl_api, oracai
    from src.eth_focus import compute_verdict_pair
    from src.ta import compute_indicators

    now = datetime.now(timezone.utc)
    state = _load_state()
    whale_sigs = _load_whale_signals()

    try:
        snapshot = oracai.fetch_snapshot()
        regime = snapshot.get("regime")
        phase = ((snapshot.get("cycle") or {}).get("phase"))
    except Exception as e:  # noqa: BLE001 — без снапшота тактика молчит
        print(f"[tactical] OracAI snapshot недоступен: {e} — пропуск прохода")
        return []

    meta = hl_api.fetch_meta_and_ctxs()
    sent: list[str] = []
    directions: list[str] = []

    for coin in TACTICAL_COINS:
        hl_sym = hl_api.resolve_symbol(coin, meta)
        if hl_sym is None:
            continue
        ctx = meta.get(hl_sym, {})
        funding = ctx.get("funding_apr_pct")
        try:
            candles = hl_api.fetch_candles(hl_sym, interval="1d",
                                           lookback_days=220)
        except Exception as e:  # noqa: BLE001
            print(f"[tactical] {coin}: candles error {e}")
            continue
        if len(candles) < 200:
            continue
        ta = compute_indicators(candles, swing_lookback=30)

        (_, _), (verdict, rationale) = compute_verdict_pair(
            ta=ta, funding_apr_pct=funding,
            whale_net_long=None,        # вес китов в вердикте обнулён — A/B чист
            whale_cluster_count=0,
            regime=regime, phase=phase,
        )

        st = state.get(coin, {})
        # Сравниваем с последним ДЕЙСТВЕННЫМ вердиктом (LONG/SHORT), а не с
        # буквально предыдущим: проход через WAIT (нейтраль) не должен считаться
        # сменой и порождать ложный перевход (баг 21.06: SHORT→WAIT→SHORT слало
        # повторный алерт). last_action_verdict переживает периоды WAIT.
        prev = st.get("last_action_verdict") or st.get("last_verdict")
        emit = should_emit(verdict, prev, st.get("last_alert_ts"), now)

        stance = whale_stance(whale_sigs, coin, now)
        if emit:
            ok, block_note = whale_filter(verdict, stance)
            if not ok:
                print(f"[tactical] {coin}: {block_note}")
                entry0 = float(ta.get("last") or ctx.get("mark") or 0)
                _append_tactical_journal({
                    "ts": now.isoformat(), "coin": coin, "direction": verdict,
                    "entry": entry0,
                    "sl": sl_for(verdict, entry0, ta.get("atr14"),
                                 ta.get("swing_low"), ta.get("swing_high")),
                    "regime": regime, "phase": phase,
                    "funding_apr_pct": funding,
                    "whale_stance": stance,
                    "emitted": False, "suppressed_by": "whales",
                })
                emit = False

        if emit:
            entry = float(ta.get("last") or ctx.get("mark") or 0)
            sl = sl_for(verdict, entry, ta.get("atr14"),
                        ta.get("swing_low"), ta.get("swing_high"))
            msg = build_alert(
                coin=coin, direction=verdict, entry=entry, sl=sl,
                rationale=rationale, funding_apr_pct=funding,
                whale_note=whale_stance_note(whale_sigs, coin, now, stance),
                regime=regime,
            )
            sent.append(msg)
            directions.append(verdict)
            _append_tactical_journal({
                "ts": now.isoformat(), "coin": coin, "direction": verdict,
                "entry": entry, "sl": sl,
                "regime": regime, "phase": phase,
                "funding_apr_pct": funding,
                "whale_stance": stance,
                "emitted": True, "suppressed_by": None,
            })
            state[coin] = {"last_verdict": verdict,
                           "last_action_verdict": verdict,
                           "last_alert_ts": now.isoformat(),
                           "last_change_ts": now.isoformat()}
        else:
            # Вердикт не эмитили. last_change_ts отражает смену ДЕЙСТВЕННОГО
            # вердикта: WAIT (нейтраль) НЕ считается сменой и не сбрасывает
            # счётчик «без смены N дней» и не затирает last_action_verdict.
            prev_action = st.get("last_action_verdict") or st.get("last_verdict")
            changed = st.get("last_change_ts")
            new_action = prev_action
            if verdict in ("LONG", "SHORT") and prev_action != verdict:
                changed = now.isoformat()      # реальная смена действия без эмиссии (кулдаун/киты)
                new_action = verdict
            state[coin] = {**st, "last_verdict": verdict,
                           "last_action_verdict": new_action,
                           "last_change_ts": changed or now.isoformat()}

        print(f"[tactical] {coin}: verdict={verdict} prev={prev} "
              f"whales={stance or '—'} emitted={bool(emit)}")

    _save_state(state)

    corr = correlation_note(directions)
    if corr:
        sent.append(corr)

    if sent:
        try:
            from src.telegram_sender import send_messages
            send_messages(sent)
        except Exception as e:  # noqa: BLE001
            print(f"[tactical] telegram send failed: {e}")
    return sent


if __name__ == "__main__":
    run()
