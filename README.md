# hl_weekly_planner

Еженедельная (суббота 10:00 MSK) рекомендация по покупке токенов на Hyperliquid:
читает регим/фазу из OracAI, скорит whitelist по ТА, выдаёт план с SL в Telegram.

## Поток данных

```
OracAI (BRKME/OracAI)
  └→ state/last_output.json  (commits 2x/day)
        ↓
hl_weekly_planner
  ├─ читает snapshot         → регим/фаза/риск/Top%/Bottom%/RSI
  ├─ derive_signal_strength  → STRONG / MODERATE / SKIP / EXIT + leverage
  ├─ HL info API             → mark, funding APR, OI, OHLC 220d
  ├─ TA для каждого токена   → RSI, EMA50, EMA200, ATR, swing low, momentum
  ├─ scoring + filters       → ранжирование + skip-причины
  ├─ allocate_budget         → STRONG: 60%/40%, MODERATE: 100%
  ├─ calculate_sl            → max(entry−2·ATR, swing_low) с -20% floor
  └─ Telegram report         → один HTML message в твой канал
```

## Setup

1. **Secrets** в Settings → Secrets and variables → Actions:
   - `TELEGRAM_BOT_TOKEN` — бот, добавленный админом в твой приватный канал
   - `TELEGRAM_CHAT_ID` — числовой ID канала (`-100...`)
   - `TELEGRAM_OWNER_CHAT_ID` — твой чат для алертов на падения (опционально)

2. **Workflow permissions**: Settings → Actions → General → Workflow permissions → Read and write.

3. Прогон: Actions → weekly-plan → Run workflow.

## Решения и тюнинг

- Веса скоринга и пороги фильтров — в `whitelist.yaml` секция `rules` и в `src/scoring.py`.
- Все решения логируются в `decisions.jsonl` после каждой субботы. Это датасет для оценки качества бота через 2-3 месяца.

## Что НЕ делает

- Не торгует автоматически — только рекомендации.
- Не знает о твоих открытых позициях (stateless каждую субботу).
- Не использует Twitter/новости — только OracAI + классический ТА.

## Зависимость от OracAI

Снапшот читается из `https://raw.githubusercontent.com/BRKME/OracAI/main/state/last_output.json`.
Требуется наличие поля `cycle` (commit OracAI 2d9dfe5+, май 2026).
Если поле отсутствует — бот падает явно с понятной ошибкой.
