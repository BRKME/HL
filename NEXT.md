# NEXT SESSION — пятница 22 мая 2026

## Цель проекта
**Заработать на HL.** Не мониторить, не визуализировать — найти альфу и действовать на ней.

## Что сделано (17 мая)
- Daily monitor (риск + позиции, минимальный формат)
- Whale tracker (50 китов, fills каждые 4h, signals → digest + instant)
- ETH Saturday Focus (descriptive TA + funding + whale activity)
- Leaderboard rank tracking (NEW_ENTRANT / DROP_OFF)

**Это инфраструктура. Альфы пока нет.**

## Что делаем — Phase 4: Signal Backtester

`src/signal_backtester.py` — модуль и weekly workflow.

**Вход:** `state/whale_signals.jsonl` + `state/whale_fills.jsonl` + HL D1 candles.

**Логика:** для каждого исторического сигнала (CLUSTER / FLIP / NEW_OPEN / WHALE_NEW_ENTRANT) посмотреть mark coin через 6h / 24h / 48h / 7d. Сгруппировать по `coin × signal_type × direction`. Посчитать win-rate, avg return, max DD.

**Выход:** Telegram-отчёт раз в неделю (или on-demand workflow_dispatch):
```
🎯 Signal performance — last 14d
ETH:
  CLUSTER long  — 7 ev, WR 71%, avg +3.4% / 24h, max DD -1.2%
  CLUSTER short — 2 ev, WR  0%, avg -2.1%
  FLIP          — 1 ev — мало данных
BTC:
  CLUSTER long  — 4 ev, WR 50%, avg +0.4%
```

**Решение:** сигналы с WR ≥60% и ≥10 событий — действовать. Остальное — шум, повысить пороги или скрыть.

## Перед стартом проверь
1. `state/whale_signals.jsonl` — ≥30 строк (минимум для статистики). Если <30 — отложить ещё на неделю.
2. `state/whale_fills.jsonl` — ≥50k fills.
3. Текущие пороги в `src/whale_correlation.py`: `MIN_WHALE_COUNT`, `MIN_WINRATE`, `MIN_NOTIONAL`. Бэктестер должен **варьировать их** и показать оптимум.
4. Backtester НЕ должен делать сделки — только аналитика.

## После Phase 4 — Phase 5 кандидаты (НЕ решено)
- Trade journal: автоматический лог твоих entry/exit из orphan diff между snapshots + контекст (whale activity, regime). Чтобы понимать "когда я выигрываю vs проигрываю".
- Auto-action на high-conviction сигналах (WR ≥70%, ≥20 событий). Спорно — обсудить риски прежде чем кодить.
- ETH-only режим: убрать остальные coins из whitelist если ETH-focus оправдает себя.

## Напоминания себе
- Цель = деньги. Каждая фича должна сводиться к "это поможет заработать или защитить капитал?". Если нет — не делать.
- Не добавлять метрики ради метрик. Юзер уже один раз сказал "не нужно много объяснений".
- Минимум кода, максимум информации из существующих данных.
