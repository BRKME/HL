"""HL Weekly Planner — главный оркестратор.

Запуск: `python -m src.main` (по cron в субботу 07:00 UTC = 10:00 MSK).
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import oracai, hl_api, ta, scoring, render, telegram_sender, lp_summary, ladder


_REPO_ROOT = Path(__file__).resolve().parent.parent
WHITELIST_PATH = _REPO_ROOT / "whitelist.yaml"
DECISIONS_PATH = _REPO_ROOT / "decisions.jsonl"
WEEKLY_BUDGET_USD = 200.0


def _maybe_send(msgs: list[str]) -> None:
    """Отправка в Telegram, если планировщик не в тихом режиме.

    Субботний пост в канал отключён (оператор: сигнал нужен в моменте, а не по
    расписанию — для этого есть тактический слой). Под WEEKLY_SILENT=1
    планировщик продолжает считать вердикт и писать decisions.jsonl (нужно для
    advisor-alpha KPI в воскресной сводке), но НЕ шлёт сообщение.
    """
    import os
    if os.getenv("WEEKLY_SILENT") == "1":
        print("[weekly] silent mode — decision logged, no telegram post")
        return
    telegram_sender.send_messages(msgs)


def run() -> None:
    started = datetime.now(timezone.utc)

    # 1. OracAI snapshot → сигнал
    snapshot = oracai.fetch_snapshot()
    signal = oracai.derive_signal_strength(snapshot)
    print(f"[oracai] signal={signal['signal']} leverage={signal['leverage']} "
          f"defensive={signal.get('defensive', False)}", flush=True)

    # 1b. Лестница цикла (стратегический слой) — мягкий fail-safe: нет/устарела
    # → блок просто не показывается. Вердикты planner'а она НЕ меняет.
    ladder_contract = ladder.fetch_ladder()
    ladder_ctx = ladder.render_context(ladder_contract)
    if ladder_ctx:
        print(f"[ladder] zone={ladder_ctx['zone']} "
              f"event={'yes' if ladder_ctx['event_line'] else 'no'}", flush=True)
    else:
        print("[ladder] контракт недоступен/устарел — блок пропущен", flush=True)

    # Sanity check: сколько SKIP подряд было до этого
    skip_streak = _count_recent_skip_streak()
    if skip_streak >= 3 and signal["signal"] == "SKIP":
        signal["reasons"].append(
            f"⚠️ Это {skip_streak + 1}-й SKIP подряд — "
            f"проверь регим OracAI вручную, возможно стоит пересмотреть DCA-план"
        )

    # SKIP/EXIT — короткий отчёт без анализа кандидатов
    if signal["signal"] in ("SKIP", "EXIT"):
        had_pos = _had_open_position_last_week()
        msg = render.render_report(signal=signal, picks=[], skipped=[], ladder_ctx=ladder_ctx, had_position=had_pos)
        _maybe_send([msg])
        # LP summary disabled - was sending outdated/useless data
        _persist_decision(started, signal, picks=[], skipped=[], ladder_contract=ladder_contract)
        return

    # 2. Whitelist + HL meta
    whitelist = _load_whitelist()
    rules = whitelist["rules"]
    swing_lookback = rules.get("swing_low_lookback", 30)
    meta = hl_api.fetch_meta_and_ctxs()
    print(f"[hl] universe size: {len(meta)}", flush=True)

    # 3. Для каждого токена whitelist — ТА + скоринг
    candidates: list[dict] = []
    skipped: list[dict] = []

    for sym, info in whitelist["tokens"].items():
        hl_sym = hl_api.resolve_symbol(info.get("hl_symbol") or sym, meta)
        if hl_sym is None:
            skipped.append({"symbol": sym, "reason": "не листится на HL"})
            continue
        hl_ctx = meta.get(hl_sym, {})

        try:
            candles = hl_api.fetch_candles(hl_sym, interval="1d", lookback_days=220)
        except Exception as e:
            skipped.append({"symbol": sym, "reason": f"HL candles error: {e}"})
            continue

        if len(candles) < 200:
            skipped.append({"symbol": sym, "reason": f"мало истории ({len(candles)} свечей)"})
            continue

        ind = ta.compute_indicators(candles, swing_lookback=swing_lookback)
        ok, reason = scoring.passes_filters(ind, hl_ctx, rules, signal["signal"])
        if not ok:
            skipped.append({"symbol": sym, "reason": reason})
            continue

        score = scoring.score_candidate(ind, hl_ctx, info["tier"])
        candidates.append({
            "symbol": sym,
            "hl_symbol": hl_sym,
            "tier": info["tier"],
            "thesis": info.get("thesis", ""),
            "entry": ind["last"],
            "score": score,
            "rsi_d1": ind["rsi_d1"],
            "ema50": ind["ema50"],
            "ema200": ind["ema200"],
            "vs_ema50_pct": ind["vs_ema50_pct"],
            "vs_ema200_pct": ind["vs_ema200_pct"],
            "momentum_7d": ind["momentum_7d"],
            "momentum_30d": ind["momentum_30d"],
            "atr14": ind["atr14"],
            "swing_low": ind["swing_low"],
            "funding_apr_pct": round(hl_ctx.get("funding_apr_pct") or 0, 2),
            "ind": ind,
            "hl_ctx": hl_ctx,
        })

    # 4. Ranking + sizing + SL
    candidates.sort(key=lambda x: x["score"], reverse=True)
    picks_raw = scoring.allocate_budget(
        candidates,
        signal["signal"],
        defensive=signal.get("defensive", False),
        weekly_budget=WEEKLY_BUDGET_USD,
    )

    picks = []
    for p in picks_raw:
        sl = scoring.calculate_sl(p["entry"], p["ind"], rules)
        picks.append({
            **{k: v for k, v in p.items() if k not in ("ind", "hl_ctx")},
            "sl_price": sl["sl_price"],
            "sl_pct": sl["sl_pct"],
            "sl_method": sl["method"],
        })

    # Если после фильтров не нашлось ни одного кандидата — SKIP
    if not picks:
        signal_fallback = {
            "signal": "SKIP", "leverage": 0,
            "reasons": signal["reasons"] + ["После фильтров не осталось ни одного кандидата"],
            "raw": signal["raw"],
            "defensive": False,
        }
        msg = render.render_report(signal=signal_fallback, picks=[], skipped=skipped, ladder_ctx=ladder_ctx)
        _maybe_send([msg])
        # LP summary disabled
        _persist_decision(started, signal_fallback, picks=[], skipped=skipped, ladder_contract=ladder_contract)
        return

    # 5. Render + send
    msg = render.render_report(signal=signal, picks=picks, skipped=skipped, ladder_ctx=ladder_ctx)
    _maybe_send([msg])
    # LP summary disabled
    _persist_decision(started, signal, picks=picks, skipped=skipped, ladder_contract=ladder_contract)


def _send_lp_summary() -> None:
    """Отдельным сообщением — LP-резюме из OracAI. Не ломаем основной отчёт."""
    try:
        report = lp_summary.fetch_lp_report()
        if not report:
            print("[lp] нет свежего отчёта, пропускаем", flush=True)
            return
        msg = lp_summary.render_lp_summary(report)
        if msg:
            _maybe_send([msg])
    except Exception as e:
        # LP-секция не должна валить основной отчёт
        print(f"[lp] не удалось отправить LP-резюме: {e}", flush=True)


def _had_open_position_last_week() -> bool:
    """Мог ли остаться открытый перп с прошлой недели?

    True только если последний вердикт был входным (STRONG/MODERATE).
    После SKIP/EXIT позиций нет — и совет «закрыть перпы» бессмыслен.
    """
    if not DECISIONS_PATH.exists():
        return False
    try:
        with DECISIONS_PATH.open(encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception:
        return False
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            return False
        return rec.get("signal") in ("STRONG", "MODERATE")
    return False


def _count_recent_skip_streak() -> int:
    """Сколько SKIP подряд было до текущего запуска (по decisions.jsonl).

    Считает с конца до первого не-SKIP.
    """
    if not DECISIONS_PATH.exists():
        return 0
    try:
        with DECISIONS_PATH.open(encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return 0
    streak = 0
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            break
        if rec.get("signal") == "SKIP":
            streak += 1
        else:
            break
    return streak


def _load_whitelist() -> dict:
    with WHITELIST_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _persist_decision(started, signal: dict, *, picks: list, skipped: list,
                      ladder_contract: dict | None = None) -> None:
    """История решений — для последующего анализа точности."""
    rec = {
        "ts": started.isoformat(),
        "signal": signal["signal"],
        "leverage": signal["leverage"],
        "reasons": signal.get("reasons"),
        "oracai": signal.get("raw"),
        "picks": [
            {k: v for k, v in p.items() if k not in ("ind", "hl_ctx")} for p in picks
        ],
        "skipped": skipped,
        # стратегический контекст лестницы на момент решения — для будущей
        # оценки, как тактика planner'а соотносилась с фазой цикла
        "ladder": ({"zone": ladder_contract.get("zone"),
                    "mvrv": ladder_contract.get("mvrv"),
                    "signal": (ladder_contract.get("signal") or {}).get("action")}
                   if ladder_contract else None),
    }
    with DECISIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    try:
        run()
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            telegram_sender.alert_owner(
                f"❌ hl_weekly_planner упал:\n<pre>{tb[-2500:]}</pre>"
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
