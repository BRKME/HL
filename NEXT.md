# NEXT SESSION

## Цель проекта
Заработать на HL. Не мониторить, не визуализировать — найти альфу и действовать.

## Что сделано
- Daily monitor (риск, позиции, советы по regime/phase) — каждые 2ч 10-22 MSK
- Whale tracker — каждые 4ч, накапливает signals и fills
- ETH focus → daily verdict (LONG/SHORT/WAIT) 09:00 MSK
- Whitelist daily — verdict по 6 монетам (HYPE/BTC/ETH/NEAR/ZEC/TAO) 09:05 MSK
- Wyckoff phases: CAPITULATION/ACCUMULATION/EUPHORIA правильно интерпретируются
- **Verdict journal** — каждый вердикт логируется в state/verdict_journal.jsonl

## Следующая задача — Phase 4.5: Verdict effectiveness backtester

Когда в `state/verdict_journal.jsonl` накопится ≥100 записей (≈14 дней работы, к ~17 июня), сделать модуль который:

1. Читает journal с момента T_start
2. Для каждого verdict замеряет цену через 24h / 48h / 7d
3. Группирует по `(source × coin × verdict)`
4. Считает: WR (% случаев когда движение совпало с verdict), avg return, max DD
5. Раз в неделю шлёт Telegram отчёт типа:
   ```
   📊 Эффективность модели (14 дней, 168 verdicts)
   ETH LONG (12 ev): WR 67% / 24h, avg +2.1%   🎯
   ETH SHORT (8 ev): WR 50% / 24h, avg -0.3%
   ETH WAIT (45 ev): пропущено $X движения
   ...
   ```

### Threshold для actionable
WR ≥ 60% AND N ≥ 10 — повод доверять модели для этой комбинации.

### Когда писать
Раньше 14-17 июня не имеет смысла — данных мало.

## Phase 5 (после backtester)
- Если эффективность подтверждена → авто-action на high-conviction (WR ≥ 70%, N ≥ 20)
- Position size enforcement: блокировать новые входы при концентрации ≥ 70%

## Напоминания
- Не добавлять метрики ради метрик
- Минимум кода, максимум информации из существующих данных
- Юзер не любит длинные объяснения
