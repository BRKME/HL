"""LP резюме — тонкий слой над OracAI lp_advisor_report.json.

Никакой собственной математики. Читаем что насчитал OracAI и форматируем
в человеческий русский. Если файла нет / устарел — просто скипаем (не
ломаем основной HL-отчёт).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

_LP_URL = os.environ.get(
    "ORACAI_LP_URL",
    "https://raw.githubusercontent.com/BRKME/OracAI/main/state/lp_advisor_report.json",
)
_STALE_HOURS = 14 * 24  # 14 дней. OracAI Advisor бежит в weekly compact
                        # mode (lp_system.py STAGE 3 skip в ежедневке),
                        # поэтому lp_advisor_report.json обновляется
                        # раз в неделю.


class LPSnapshotError(RuntimeError):
    pass


def fetch_lp_report() -> dict[str, Any] | None:
    """Возвращает LP-отчёт. Помечает stale-флагом если устарел."""
    try:
        r = requests.get(_LP_URL, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[lp] недоступен: {e}", flush=True)
        return None

    # Проверка свежести — но не скипаем, а помечаем флагом
    age_h = None
    ts = data.get("timestamp")
    if ts:
        try:
            if isinstance(ts, (int, float)):
                age_h = (datetime.now(timezone.utc).timestamp() - ts) / 3600
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        except Exception:
            pass

    data["_age_hours"] = age_h
    data["_stale"] = age_h is not None and age_h > _STALE_HOURS
    return data


def _translate_regime_rec(rec: str) -> str:
    """Перевод фразы regime_recommendation на русский."""
    if not rec:
        return ""
    mapping = {
        "Downtrend. IL risk. Prefer stable pairs.":
            "Нисходящий тренд. Высокий риск IL. Лучше стейбл-пары.",
        "Uptrend. Volatile pairs OK.":
            "Восходящий тренд. Волатильные пары допустимы.",
        "Range. Tight spreads work.":
            "Боковик. Работают узкие диапазоны.",
        "Sideways. Tight ranges.":
            "Боковик. Узкие диапазоны.",
    }
    return mapping.get(rec, rec)


def _translate_reason(reason: str) -> str:
    """Перевод английских reason'ов от OracAI на русский."""
    if not reason:
        return ""
    mapping = {
        "High IL risk pair in BEAR regime. Consider safer pairs.":
            "пара с высоким IL-риском, рынок медвежий — лучше стейбл-пары",
        "Position is healthy.":
            "позиция в порядке",
        "Out of range":
            "вне диапазона",
        "High IL risk":
            "высокий риск IL",
    }
    if reason in mapping:
        return mapping[reason]
    # Частичное совпадение
    for en, ru in mapping.items():
        if en.lower() in reason.lower():
            return ru
    return reason  # если не нашли — оставляем как есть


# Маппинг тех. recommendation на человеческий русский
_REC_LABEL = {
    "HOLD":     ("✅", "Держать"),
    "NARROW":   ("⚠️", "Сузить диапазон"),
    "REBALANCE":("🔄", "Перебалансировать"),
    "EXIT":     ("🔴", "Закрыть"),
    "ADD":      ("➕", "Добавить"),
    "WIDEN":    ("↔️", "Расширить диапазон"),
}

_STATUS_EMOJI = {
    "HEALTHY": "🟢",
    "WARNING": "🟡",
    "CRITICAL": "🔴",
}


def render_lp_summary(report: dict[str, Any]) -> str:
    """HTML для Telegram. Один компактный блок."""
    if not report:
        return ""

    total_tvl = report.get("total_tvl") or 0
    total_fees = report.get("total_fees") or 0
    healthy = report.get("positions_healthy") or 0
    warning = report.get("positions_warning") or 0
    critical = report.get("positions_critical") or 0
    analyzed = report.get("positions_analyzed") or 0
    regime_rec = report.get("regime_recommendation") or ""

    positions = report.get("position_analyses") or []

    # Сортировка: сначала CRITICAL, потом WARNING с requires action, потом HEALTHY
    def _priority(p: dict) -> int:
        s = p.get("status")
        if s == "CRITICAL":
            return 0
        if s == "WARNING":
            return 1
        return 2

    positions = sorted(positions, key=_priority)

    lines: list[str] = []
    lines.append(f"💧 <b>LP портфель</b>")
    
    # Stale warning — если данные старые, ставим в самом верху
    if report.get("_stale"):
        age_h = report.get("_age_hours") or 0
        age_days = age_h / 24
        if age_days >= 1:
            age_str = f"{age_days:.0f} дней"
        else:
            age_str = f"{age_h:.0f} часов"
        lines.append(
            f"⚠️ <i>Данные устарели на {age_str} — Advisor в OracAI давно не "
            f"запускался. Цифры ниже могут не отражать текущую ситуацию.</i>"
        )
    
    lines.append(
        f"💰 TVL: ${_fmt_money(total_tvl)} · "
        f"📈 Fees: ${total_fees:.2f}"
    )
    lines.append(
        f"Состояние: 🟢 {healthy} · 🟡 {warning} · 🔴 {critical} (всего {analyzed})"
    )
    if regime_rec:
        lines.append(f"<i>{_e(_translate_regime_rec(regime_rec))}</i>")
    lines.append("")

    # Группируем требующие действия и здоровые
    needs_action = [p for p in positions if p.get("recommendation") not in ("HOLD", None)]
    holding = [p for p in positions if p.get("recommendation") in ("HOLD", None)]

    if needs_action:
        lines.append("<b>Требуют действия:</b>")
        for p in needs_action:
            lines.append(_format_position(p, action=True))
        lines.append("")

    if holding:
        lines.append("<b>Держим:</b>")
        for p in holding:
            lines.append(_format_position(p, action=False))

    return "\n".join(lines).strip()


def _format_position(p: dict, *, action: bool) -> str:
    sym = _e(p.get("symbol") or "?")
    chain = _e(p.get("chain") or "?")
    wallet = _e(p.get("wallet_name") or "?")
    balance = p.get("balance_usd") or 0
    fees = p.get("fees_usd") or 0
    in_range = p.get("in_range")
    status = p.get("status") or ""
    rec = p.get("recommendation") or ""
    raw_reason = p.get("reason") or ""
    reason = _e(_translate_reason(raw_reason))
    alt = p.get("better_alternative")
    improve = p.get("potential_improvement")

    status_emoji = _STATUS_EMOJI.get(status, "⚪️")
    rec_emoji, rec_label = _REC_LABEL.get(rec, ("•", rec or "—"))

    # Если recommendation=NARROW и есть better_alternative — это по факту
    # «перейти в другую пару», не «сузить диапазон». Перепишем label.
    if rec == "NARROW" and alt:
        rec_emoji = "🔁"
        rec_label = "Перейти в другую пару"

    range_mark = "" if in_range is None else (" · в range" if in_range else " · вне range")

    head = (
        f"{status_emoji} <b>{sym}</b> "
        f"<i>({chain}, {wallet})</i> — "
        f"${_fmt_money(balance)} · fees ${fees:.2f}{range_mark}"
    )

    if action:
        action_line = f"   {rec_emoji} <b>{_e(rec_label)}</b>"
        if reason:
            action_line += f" — {reason}"
        if alt and improve:
            action_line += f"\n   💡 Лучше: <b>{_e(alt)}</b> (+{improve:.1f}%)"
        elif alt:
            action_line += f"\n   💡 Лучше: <b>{_e(alt)}</b>"
        return f"{head}\n{action_line}"
    else:
        return head


def _fmt_money(v: float) -> str:
    if v is None:
        return "0"
    if v >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    return f"{v:.2f}"


def _e(s: Any) -> str:
    """HTML escape."""
    import html
    return html.escape("" if s is None else str(s), quote=False)
