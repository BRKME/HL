# NEXT SESSION

## Цель
Заработать на HyperLiquid. Найти альфу и действовать. Не визуализация ради визуализации.

## Что сделано
- Daily-monitor — портфель + советы regime/phase, каждые 2ч 10-22 MSK
- Утром (10:00 MSK) в составе portfolio: whitelist verdicts по 6 монетам + whale stance
- Per-position verdict markers (⚠️ если позиция против вердикта)
- Verdict journal в state/verdict_journal.jsonl — пишет каждое утро, даже без позиций

## Methodology after analyst review (9 June 2026)

### Что было изменено по фидбеку
1. **Trend vs Exhaustion разделены.** Раньше RSI/funding/swing голосовали за direction наравне с EMA — это смешивало contrarian с trend-following и ломало результат на сильных трендах. Теперь trend — отдельный score (EMA-only), exhaustion — отдельный флаг который **снижает уверенность** в counter-trend случаях.

2. **Whale weight = 0.** Whale signals (cluster events, net_long) больше **не влияют** на verdict. Они остаются визуально в digest, но в score веса нет. До тех пор пока journal не покажет что whale data даёт WR edge — будем считать их шумом.

3. **Журналируется verdict_raw отдельно от verdict_final.** raw = trend + exhaustion без regime. final = raw + regime/phase blockers. Через 2 недели сравним WR(raw) vs WR(final). Если final хуже — OracAI regime layer выкидываем.

### Что осталось без изменений
- 6 фокус-монет: HYPE, BTC, ETH, NEAR, ZEC, TAO
- Дневной таймфрейм
- Append-only journal
- Reversal phases (CAPITULATION/EUPHORIA) overridе regime blocker

## Следующая задача — Phase 4.5: Backtester эффективности модели

### Когда делать
**Не раньше 1 июля 2026** (было 17 июня — пересмотрено по фидбеку аналитика).

Причина сдвига: 150 наблюдений по 6 коррелированным монетам — это не 150 независимых, эффективная выборка ~30-50. Нужно **300-500 записей** в journal чтобы делать заявления о WR. К 1 июля наберётся 400+.

### Что считать
Для каждой комбинации `(coin, source, verdict)` через journal:
- WR через 24h / 48h / 7d (цена двинулась в сторону вердикта?)
- Avg return
- **WR(raw) vs WR(final)** — главный вопрос: добавляет ли OracAI regime edge?

### Threshold actionable (пересмотрено)
**N ≥ 30 AND WR ≥ 60%.** Раньше было N≥10 — статистически шум. 60% на 10 событиях значит ничего.

## Phase 5 (после backtester) — открытые вопросы

Не план, а **возможные** направления в зависимости от того что покажет backtester:

- Если WR(raw) ≥ WR(final): убрать OracAI regime из decision. Использовать только trend + exhaustion.
- Если какой-то coin × verdict даёт стабильно WR 65%+ при N≥30 → возможно автоматический action (с tight SL)
- Если whale stance с поправкой на close events начнёт показывать edge → вшить в score с весом 1
- Если exhaustion downgrade оказался слишком частым (много WAIT когда тренд работал) → ослабить пороги

## Открытые проблемы (для будущих сессий)

1. **Корреляция между монетами** — BTC доминирует, альты следуют. Эффективная выборка << номинальной. Backtester должен учитывать это в confidence intervals.
2. **Веса пока эвристические** — пороги (60/40% bias, ±5%/±15% funding, ≤3% swing) нужно калибровать через grid search **на out-of-sample данных**, после накопления.
3. **Cycle detection (OracAI)** — внешний компонент, его собственная точность не валидирована. Раздельное журналирование raw/final поможет ответить.

## Напоминания
- Не добавлять метрики ради метрик
- Минимум кода, максимум информации из существующих данных
- Юзер не любит длинные объяснения
