"""Воскресная KPI-сводка HL-проекта: успешность по слоям.

Принципы (общие для всей BRKME-системы):
  • сигналы и реальные деньги меряются раздельно;
  • каждая цифра — с n рядом; ниже порогов мощности — «рано судить»;
  • слой без измерителя = шум: тактика оценивается R-мультиплями из
    tactical_journal, субботний советник — против бейзлайна always-DCA,
    портфель — серверным P&L Hyperliquid.

Advisor alpha: на каждую субботу берём 7д-доходность BTC (прокси рынка);
«планнер» в рынке только в BUY-недели (STRONG/MODERATE), «always» — каждую
неделю. Альфа = разница накопленных доходностей. Это главный KPI субботы:
оправдывает ли пропуск медвежьих недель само существование советника.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
TACTICAL_JOURNAL = _REPO_ROOT / "state" / "tactical_journal.jsonl"
DECISIONS = _REPO_ROOT / "decisions.jsonl"

MIN_WEEKS = 4               # меньше суббот — рано судить про советника
ADVISOR_LOOKBACK_WEEKS = 12
BUY_SIGNALS = ("STRONG", "MODERATE")


# ── субботний советник vs always-DCA ────────────────────────────────────────

def advisor_alpha(decisions: List[dict]) -> dict:
    """decisions: [{'signal': str, 'btc_fwd7': float}, ...] (готовые форварды).

    Возвращает planner_ret / always_ret (суммарные простые доходности),
    alpha_pp и готовую строку с n.
    """
    n = len(decisions)
    if n < MIN_WEEKS:
        return {"n_weeks": n, "planner_ret": None, "always_ret": None,
                "alpha_pp": None,
                "line": (f"Суббота: {n}/{MIN_WEEKS} недель с форвардом — "
                         f"рано судить")}
    planner = sum(d["btc_fwd7"] for d in decisions
                  if str(d.get("signal", "")).upper() in BUY_SIGNALS)
    always = sum(d["btc_fwd7"] for d in decisions)
    alpha_pp = (planner - always) * 100
    return {
        "n_weeks": n, "planner_ret": planner, "always_ret": always,
        "alpha_pp": alpha_pp,
        "line": (f"Суббота: альфа {alpha_pp:+.1f}пп vs always-DCA "
                 f"(n={n} недель · планнер {planner*100:+.1f}% / "
                 f"рынок {always*100:+.1f}%)"),
    }


# ── сборка сообщения ─────────────────────────────────────────────────────────

def build_message(tactical_line: Optional[str],
                  advisor_line: Optional[str],
                  portfolio_line: Optional[str]) -> str:
    lines = ["📐 HL · недельные KPI"]
    for ln in (tactical_line, advisor_line, portfolio_line):
        if ln:
            lines.append(ln)
    if len(lines) == 1:
        lines.append("Данных для KPI пока нет — слои копят историю.")
    return "\n".join(lines)


# ── оркестрация (сеть) ───────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _candles_since(coin: str, since: datetime) -> List[dict]:
    from src import hl_api
    days = max(2, (datetime.now(timezone.utc) - since).days + 2)
    candles = hl_api.fetch_candles(coin, interval="1d",
                                   lookback_days=min(days, 60))
    # свечи с даты сигнала включительно
    out = []
    for c in candles:
        ts = c.get("t") or c.get("T") or 0
        try:
            cdt = datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc)
        except Exception:
            cdt = None
        if cdt is None or cdt >= since - timedelta(days=1):
            out.append(c)
    return out


def _tactical_kpi_line() -> Optional[str]:
    from src import tactical_eval as te
    rows = [r for r in _load_jsonl(TACTICAL_JOURNAL) if r.get("emitted")]
    if not rows:
        return "Тактика: сигналов пока нет"
    evaluated = []
    for r in rows:
        try:
            since = datetime.fromisoformat(str(r["ts"]))
        except Exception:
            continue
        try:
            candles = _candles_since(r["coin"], since)
        except Exception as e:  # noqa: BLE001
            print(f"[kpi] candles {r.get('coin')}: {e}")
            continue
        res = te.evaluate_signal(r.get("direction"), float(r.get("entry") or 0),
                                 r.get("sl"), candles)
        res["direction"] = r.get("direction")
        evaluated.append(res)
    return te.aggregate(evaluated)["line"] if evaluated else None


def _advisor_kpi_line() -> Optional[str]:
    from src import hl_api
    rows = _load_jsonl(DECISIONS)
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=ADVISOR_LOOKBACK_WEEKS)
    candles = hl_api.fetch_candles("BTC", interval="1d", lookback_days=100)
    closes = {}
    for c in candles:
        try:
            d = datetime.fromtimestamp(float(c.get("t") or 0) / 1000,
                                       tz=timezone.utc).date()
            closes[d] = float(c["c"])
        except Exception:
            continue
    ready = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except Exception:
            continue
        if ts < cutoff:
            continue
        d0, d7 = ts.date(), (ts + timedelta(days=7)).date()
        c0 = closes.get(d0) or closes.get(d0 - timedelta(days=1))
        c7 = closes.get(d7) or closes.get(d7 - timedelta(days=1))
        if not c0 or not c7:
            continue                      # форвард ещё не созрел
        ready.append({"signal": r.get("signal"), "btc_fwd7": c7 / c0 - 1.0})
    return advisor_alpha(ready)["line"] if ready else None


def _portfolio_kpi_line() -> Optional[str]:
    try:
        import yaml
        from src.daily_monitor import load_accounts
        from src.portfolio_performance import fetch_combined_performance
        accounts = load_accounts(_REPO_ROOT / "whitelist.yaml")
        if not accounts:
            return None
        perf = fetch_combined_performance([a["address"] for a in accounts])
        wk = perf.week
        roi = wk.roi_pct
        return (f"Портфель: неделя {wk.pnl:+,.0f}$ ({roi:+.1f}%) · "
                f"счёт ${perf.current_account_value:,.0f}")
    except Exception as e:  # noqa: BLE001
        print(f"[kpi] portfolio: {e}")
        return None


def main() -> None:
    msg = build_message(
        tactical_line=_tactical_kpi_line(),
        advisor_line=_advisor_kpi_line(),
        portfolio_line=_portfolio_kpi_line(),
    )
    print(msg)
    try:
        from src.telegram_sender import send_messages
        send_messages([msg])
    except Exception as e:  # noqa: BLE001
        print(f"[kpi] telegram send failed: {e}")


if __name__ == "__main__":
    main()
