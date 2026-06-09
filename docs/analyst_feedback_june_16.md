# Analyst Feedback — June 16, 2026

Second round of analyst review after first refactor (`ee7e86e`). Documents
critique that's NOT YET acted on — to be evaluated with data after
1 July.

## The hybrid-system critique

Even after trend/exhaustion separation, the system still mixes two trading
philosophies:

**Trend-following factors:**
- EMA50/EMA200 structure
- Regime BULL/BEAR

**Mean-reversion factors:**
- RSI extremes
- Funding extremes
- Swing high/low proximity
- EUPHORIA/CAPITULATION phases

These are different philosophies with different winning patterns:
- Trend systems: win rarely, win big
- Mean-reversion systems: win often, win small

Mixing them in one score loses both edges.

## Missing factor: Relative Strength vs BTC

For altcoins (HYPE, TAO, NEAR, ZEC), the analyst argues RS vs BTC is
**conceptually stronger** than RSI/funding/whales combined:
- BTC +20%, TAO +80% → +60pp RS = real altcoin strength
- BTC +20%, NEAR +5% → -15pp RS = lagging, likely weak

RS is the one factor that's philosophically consistent with "don't fight
the trend" — it's a trend factor (relative).

## Hypothetical clean architecture

Per Occam's razor, the simplest model that could work:

**Core direction (trend-only):**
- Regime (BULL/BEAR)
- EMA structure
- Relative Strength vs BTC

**Entry quality (modifies confidence, doesn't vote on direction):**
- RSI, funding, swing proximity

If RS + trend + regime explains most of the variance, all other factors
should be cut on the basis of parsimony.

## What's been done

1. **Documented this critique** (this file).
2. **Added RS to journal as observability** (`feat/rs-observability`):
   - `src/relative_strength.py` — pure functions `compute_rs()` and
     `compute_rs_pair()`. Lookback 30d and 90d.
   - `VerdictEntry` gains `rs_30d` and `rs_90d` (optional).
   - All three runners (`whitelist_focus_runner`,
     `daily_monitor` digest-only and morning paths,
     `eth_focus_runner`) compute and journal RS.
   - **RS is NOT used in verdict computation.** Only recorded.

## What's NOT been done (deliberately)

- **No refactor of `_compute_verdict`.** Current logic stays. Reason:
  data has only 19 entries (3-4 June). Refactoring twice on the same
  zero-data foundation is faster than analysing data.
- **No removal of RSI/funding/swing.** They stay both in score and
  rationale. May be removed later if data shows they don't add edge
  beyond trend+regime+RS.
- **No promotion of RS to a decision factor.** Wait for data.

## Decision point: 1 July 2026

When journal has ~400 entries with both `verdict_raw` and `rs_30d`/`rs_90d`
recorded, backtester compares:

1. WR(verdict) — current factors
2. WR(verdict_raw) — without regime
3. WR if RS sign matches verdict — does RS predict correctness?
4. Correlation of RSI/funding/swing with RS — do they add signal beyond RS?

Based on these:

- If (1) >> (2): regime adds edge, keep.
- If (1) ≤ (2): regime is noise, drop.
- If (3) > 0.6: RS is strong, promote to decision factor.
- If (4) correlations are high: RSI/funding/swing are redundant with RS,
  drop them.

The clean architecture above becomes the target only if data supports it.

## References

- Original analyst feedback in conversation log (June 9)
- Second analyst feedback (June 16) — this document
- First refactor: `ee7e86e` (trend/exhaustion split, whale weight 0)
- Journal raw verdict: `a8d5db6`
