# Statistical validation checklist

This is not a "done" checklist you tick once - it's the bar the strategy
needs to clear (and keep clearing) before its live signals should be
trusted with real money. As of this writing, **that bar has not been
cleared.** This file exists so that claim is checkable rather than taken
on faith.

## Why this matters

The AI scorer's weights (`app/ai/calibration.py`) and the strategy's
actual edge (win rate, average realized R) are two different things.
Weights can be "calibrated" (statistically well-supported) while the
underlying strategy still has no real edge, or a small one that doesn't
survive costs. Both need real evidence, not just a large sample of *some*
kind of evidence.

## Minimum sample sizes

| What | Minimum before treating it as signal, not noise |
|---|---|
| Wins + losses per AI scorer confidence bucket (see `by_confidence_bucket` in backtest output) | 30 of each |
| Total closed trades feeding `calibrate_weights()` (live) or `calibrate_weights_from_backtest()` | >= 15 wins AND >= 15 losses (hard-enforced by `MIN_SAMPLES_PER_GROUP`) - treat this as a floor, not a target; 50+ of each is a more honest bar for a 9-category weight fit |
| Backtest coverage | Multiple symbols (not just BTC/ETH), multiple months, ideally spanning both a trending and a ranging/choppy period - a strategy that only works in one regime isn't validated, it's overfit to that regime |

## What's been run so far (honest status, as of this session)

Manual backtest runs during this session covered 1-6 signals per
symbol/window (BTCUSDT 15m/30d, ETHUSDT 15m/30d, BTCUSDT 1h/60d). **This
is far below the sample sizes above and should not be used to conclude
anything about whether the strategy works.** One of those runs had a
negative `avg_realized_rr` despite a majority win rate - a reminder that
win rate alone is not edge (see below).

A `POST /api/v1/backtest/calibrate` sweep across a 10-symbol basket over
90 days was kicked off but its result was not reviewed against this
checklist before this document was written - do that before trusting the
weights it produced.

## Reading results correctly

- **Win rate alone means nothing without average realized R.** A 70% win
  rate with wins averaging +0.3R and losses averaging -1R is a losing
  strategy (0.7*0.3 - 0.3*1 = -0.09R per trade). Always look at
  `avg_realized_rr` together with `win_rate`.
- **A positive `avg_realized_rr` on a small sample is not evidence.**
  Check the sample size in the same response before reacting to the
  number.
- **Backtest results are optimistic vs live** in one specific, documented
  way: `funding_rate`, `liquidation_pressure`, and `fundamentals` are
  passed as neutral/empty in every backtest step (no free historical
  series for those - see `app/backtest/engine.py`'s module docstring).
  Live signals score on more information than backtested ones did.
- **Correlated symbols inflate apparent sample size.** 10 signals across
  BTC/ETH/BNB/SOL that all fired on the same market-wide move are closer
  to 1 independent data point than 10 - see the correlation risk warning
  added in Phase 1 for the live-signal version of this same idea.

## Before trusting this strategy with real money

1. Run `POST /api/v1/backtest/calibrate` (or accumulate live closed
   trades) until every confidence bucket you care about has >= 30 wins
   and >= 30 losses.
2. Check `avg_realized_rr` is meaningfully positive across that full
   sample, not just in a cherry-picked window.
3. Re-run the backtest across a period that includes at least one clear
   trend and one clear chop/range for each symbol you care about -
   confirm the edge isn't regime-specific.
4. Only then treat MIN_CONFIDENCE=75 and the calibrated weights as
   validated, not just "the numbers the code currently happens to have."

This is an engineering document, not financial advice - it describes how
to check whether the software's own back-tested numbers are trustworthy,
not whether trading is a good idea for any particular person.
