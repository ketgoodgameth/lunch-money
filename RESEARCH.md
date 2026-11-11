# Research log: XAU/USD strategies under the $10 / $20 daily rules

## Saved candidates

| Candidate | Settings | Status |
|---|---|---|
| **Reaction zone, wick ≥ 30%** (your method, rule-based) | `results/candidates/reaction_zone_wick30.json` | **Most promising.** Passed two clean, locked tests: 2020 +$123 and 2021 +$140 at $0.20. 2020–2026: 6 of 7 years positive, about +$1,150. Next clean test locked for 2019. |
| New York opening-range breakout, half-range stop | `results/candidates/ny_breakout_half_stop.json` | Weakened. +$837 over 2022–2026 but **failed the clean 2021 test (−$361)**. |

All tests use real Dukascopy ticks (2022–2025), the simulated broker in `xautest/`, and the same rules:
$10 risk per trade, stop after the first win, max 2 trades, −$20 daily cap enforced by a $20/day account
with negative balance protection, unlimited leverage, 100 ms latency. R = the $10 risked per trade.

Costs tested: zero (to see the edge before costs), Exness Standard ($0.20 spread), and an assumed
Raw account ($0.05 spread + $3.50 per lot per side; Exness quotes "from 0.0 pips" so the real raw spread is unknown).

## Data

| Year | File | Ticks | Gold price | Median 5-min ATR |
|---|---|---|---|---|
| 2022 | `ticks/dukascopy/download/xauusd-tick-2022-01-01-2023-01-01.csv` | 53.9M | $1,615–2,070 | — |
| 2023 | `ticks/dukascopy/xauusd-tick-2023-01-01-2024-01-01.csv` | 36.4M | $1,805–2,145 | $0.99 |
| 2024 | `ticks/dukascopy/xauusd-tick-2024-01-01-2025-01-01.csv` | 56.2M | $1,984–2,790 | $1.44 |
| 2025 | `ticks/dukascopy/xauusd-tick-2025-01-01-2025-12-31.csv` | 71.0M | $2,614–4,550 | $2.68 |

Dukascopy prices match the MT5 broker export to within cents (1-minute moves correlate 0.994), but its
spread is wider (~$0.68), so every test re-quotes ticks at the account's spread (`--spread`).

## What was tested and what happened

| Idea | Developed on | Before costs, other years | After $0.20, other years | Verdict |
|---|---|---|---|---|
| 1m liquidity sweep (v2), five exit methods | 2025 | ≈ 0 | negative | no edge |
| Daily stop variants (2 wins / 2 losses / until −$20) | 2025 | — | worse than "stop after 1st win" | more trades = more loss when the edge is ≤ 0 |
| v2 + 1H SuperTrend filter | 2025 | **+0.061R (2024), +0.028R (2023)** | −$612 (2024), −$983 (2023) | the only edge that repeated; costs 3–8× larger than the edge |
| + $5 minimum stop | 2025 | −0.074R (2024), −0.026R (2023) | negative | overfit to 2025 |
| Multi-timeframe sweep spec | 2025 | — | — | 8–41 trades a year; too rare |
| 5m / 15m sweeps | 2025 | — | negative | many setups need stops over $10 |
| Trend pullback, ATR stops (5m, lb 8, ≥2×ATR) | 2025 (+$328, 4/4 quarters) | −0.053R (2022), −0.077R (2023), −0.006R (2024) | −$702, −$984, −$349 | overfit / 2025-specific |
| Cost filter: skip stops under $6.67 (3% of $0.20) or $4.00 (3% of Raw A) | thresholds from costs, not fitted | — | 4-year totals: A −$102 / −$419, B −$242 / **+$344** | losses shrink mostly by trading less |
| B + filter, nearby thresholds ($3, $5, $5.50) | robustness check | — | −$306, −$218, −$147 | the +$344 at $4 is an isolated spike: luck |

### New rules: one $20 trade a day (−$20 cap unchanged), intraday only (flat 20:00 UTC)

Wider stops (up to $20/oz) cut cost drag to ~0.01–0.03R per trade (R = $20), versus 0.1–0.2R under the
$10 rules, so the rule change did what it was for.

| Idea (2025, $0.20) | Net | Picked? | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|
| Pullback, 15-min candles | +$169 | yes (pre-declared rule) | −$513 | −$10 | −$537 |
| New York open breakout | +$101 | | | | |
| Pullback, 1-hour candles | +$22 | | | | |
| London open breakout | −$56 | | | | |

Nearby settings of the pick (lookback 6/12, stop floor 1.5/2.5×ATR) were all positive in 2025 and negative
in each of 2022–2024 (4-year totals −$785 to −$1,278). In 2025 its longs made +$295 and its shorts −$54.

Gold: 2022 flat, 2023 +13%, 2024 +27%, **2025 +65%**.

### Built on a flat year: 2022 (gold −0%), same new rules

Ten pre-declared variants on 2022. Selection rule: best net at $0.20 with ≥ 100 trades AND longs and
shorts both profitable before costs. Four passed the screen; the best was picked.

**New York opening-range breakout** (`strategies/opening_range.py`, range 13:30–14:30 UTC, stop order at
the range edge in the 1-hour SuperTrend direction, stop at the other edge, trail 0.5R, flat 20:00 UTC):

| $0.20 spread | 2022 (built) | 2023 | 2024 | 2025 | Total |
|---|---|---|---|---|---|
| Net | +$406 | +$88 | +$194 | +$101 | **+$789** |
| Per trade (R = $20) | +0.15R | +0.04R | +0.07R | +0.05R | |
| Trades | 139 | 125 | 135 | 96 | 495 |
| Longs / shorts | +$183 / +$224 | +$42 / +$46 | +$206 / −$12 | +$110 / −$9 | |

- Nearby settings, all positive over four years: 30-min range +$474, 90-min +$486, trail 0.35R +$680,
  trail 0.75R +$848. A plateau, not a spike.
- Stress: +$585 with $0.10 slippage on every fill; +$238 with $0.30; +$669 on Dukascopy's real spread.
- Caveats: 2025 appeared (unselected) in the previous round's table; the edge is small (~$100–400 a year
  at $20 risk); 13:30 UTC is 9:30 New York time in summer but 8:30 (US data releases) in winter.
- **First idea to pass every check.** Next: confirm on 2026 data and forward-test on a demo account.

Validation on 2022–2025 (`experiments/ny_validation.py`):
- Time placebo (same rules, range at 01/04/07/10/16 UTC): only the New York slot was positive in all four
  years; 01:00 made its money only in the 2024–25 rally; 04/07/10 lost.
- Against the 1-hour trend: +$14 in total → the trend direction carries the edge.
- New York local time: 08:30 (US data releases) +$699; 09:30 (stock open) +$123 → tied to data releases.
- Bootstrap of 1,243 market days: P(total ≤ 0) = 0.5% (ignores that it was picked from 10 candidates).
- Shape: 55% winners, median +$1.57, but the best 10 trades made $508 of $804 and without the best 20 the
  total is −$17. Longest stretch without a new equity high: 254 market days.

**Locked 2026 test (Jan–Oct, run once, unchanged):** 21 trades, −$122 at $0.20 (−0.29R per trade).
Gold now trades at $3,870–5,597 and the 13:30–14:30 UTC range is typically $29.52 (83% of days over $20),
so under the $20 risk cap the strategy skipped 83% of days and traded only the quiet ones. Not confirmed;
the rules no longer fit the market this strategy was found in. As gold's price rises, fixed-dollar risk
limits tighten in real terms.

### Half-range stop (`stop_frac`), 2022–2026 (`experiments/half_stop.py`)

Stop halfway between the entry and the far side of the range, so today's ~$30 ranges fit the $20 cap.
0.5 was the version to judge (fixed before running); 0.4 / 0.6 check for a plateau. 2026 had already been
used once for the original version of this strategy, so it is not a fully clean test.

| $0.20 spread | 2022 | 2023 | 2024 | 2025 | 2026 | Total | Trades |
|---|---|---|---|---|---|---|---|
| **0.5 (main)** | +$99 | +$123 | +$132 | +$411 | +$72 | **+$837** | 653 |
| 0.6 | +$134 | +$86 | +$120 | +$197 | +$146 | +$683 | 631 |
| 0.4 | +$306 | +$186 | −$159 | +$251 | +$104 | +$688 | 665 |
| 1.0 (original) | +$406 | +$88 | +$194 | +$101 | −$122 | +$668 | 516 |

- With $0.10 slippage on every fill: 0.5 +$476 (4/5 years), 0.6 +$371, 0.4 +$184. Slippage at the stop-order
  entry is the biggest risk to this edge; real Exness fills need to be measured.
- Per trade the edge is small (+0.03R to +0.16R a year; about +$1.30 per trade on average at $20 risk),
  roughly $170 a year. Worst yearly drawdown about −$210.
- Direction follows the trend filter: 2025 profit mostly from longs (gold +65%), 2026 from shorts (gold −10%).
- **Most consistent result so far.** Next: forward-test on an Exness demo account (MT5) to measure real
  spreads and slippage at the 14:30 UTC entries.

## Lessons

1. **Money management can't create an edge.** Expected daily P&L = (trades per day) × (edge per trade).
   With an edge ≤ 0, every rule that adds trades loses more; "stop after the first win" lost least.
2. **Costs are per ounce; risk is fixed at $10, so stops cap at $10/oz (0.01 lot).** Cost as a share of
   risk ≈ cost per oz ÷ stop distance. Gold's 5-minute volatility nearly tripled from 2023 to 2025, so the
   same $0.20 spread cost ~10% of risk on a typical 2×ATR stop in 2023 but ~3.7% in 2025.
3. **Every idea tuned on 2025 looked good on 2025 and failed on other years.** 2025 had high volatility
   (cheap costs relative to stops) and a strong trend. Judge ideas only on years they weren't built on.
4. **Check neighbours before believing a winner.** A real effect survives small changes to its settings;
   a single good value surrounded by losing ones is luck (the cost filter at $4 vs $3 / $5 / $5.50).
5. **A cost filter limits damage but finds no edge.** Filtering out tight stops cut 4-year losses from
   −$2,795 to −$102 for strategy A, almost entirely by taking 160 trades instead of 1,771.
6. **2025's "edge" was gold's +65% rally.** Trend-filtered strategies bought most of the year and their
   profit came from longs. The same rules lost in a flat year (2022) and even in a +27% year (2024).
   Always split results by side and compare with how far the market itself moved.
7. **Holdout status:** 2022, 2023 and 2024 have now each been used for at least one check. New ideas need
   either new data (e.g. 2021) or a fresh walk-forward design.

## Reproduce

```bash
.venv/bin/python experiments/compare_exits.py ticks/dukascopy/xauusd-tick-2025-01-01-2025-12-31.csv
.venv/bin/python experiments/compare_rules.py ticks/dukascopy/xauusd-tick-2025-01-01-2025-12-31.csv
.venv/bin/python experiments/compare_costs.py ticks/dukascopy/xauusd-tick-2025-01-01-2025-12-31.csv 2025
.venv/bin/python experiments/trend_pullback_dev.py dev
.venv/bin/python experiments/trend_pullback_dev.py check 2022
(cd experiments && ../.venv/bin/python cost_filter.py)
```

### Reaction-zone method (the user's discretionary method, made mechanical) — `strategies/reaction_zone.py`

1H SuperTrend (+ MACD) direction; 15m rejection at a sweep of the 20-candle high/low or an unfilled 1H/15m fair
value gap (wick ≥ 40%, RSI > 70 / < 30 or divergence); 5m MACD + SuperTrend confirmation; 1m MACD crossover
execution; TP $10 (or $30); no stop (the $20/day account stop-out is the limit) or a stop beyond the wick;
New York open (and London) only; 0.01 lots, stop after the first win, max 2 trades.

Built on 2022: every variant took only **2–4 trades a year**, so nothing can be judged. Where setups drop out
(2022, London + New York):

| Version | 15m rejections | 5m confirmed | Trades |
|---|---|---|---|
| As described (RSI 70/30) | 83 | 16 | 4 |
| RSI 60/40 | 120 | 21 | 7 |
| No RSI condition | 958 | 259 | 62 |
| No RSI, no 1H MACD | 1,635 | 430 | 110 |

The RSI-extreme condition is the main bottleneck: a pullback into resistance during a downtrend rarely pushes
RSI above 70 (the same conflict seen with the earlier multi-timeframe spec). Many setups are also invalidated
when price keeps going through the wick before the 1m crossover. Which conditions to relax should come from
the user's real trade log, not from trying combinations on 2022.

#### Round 2: relaxed conditions (`experiments/reaction_zone_r2.py`)

No RSI condition, 1H MACD off, sweeps + FVG, London + New York, TP $10, no stop. Built on 2022; picked by the
pre-declared rule: **enter on the 5m confirmation** (instead of waiting for the 1m crossover).

| $0.20 | 2022 (built) | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| Net | +$274 (4/4 quarters, t 2.5) | −$52 | −$23 | +$76 | +$24 |
| Win rate | 63% | 51% | 57% | 64% | 65% |

- 2023–2026 together: +$25 over 507 trades → break-even. Not confirmed.
- Neighbours (5-year totals, all positive mainly because of 2022): TP $7.50 +$264, TP $12.50 +$214, lookback 12
  +$343, lookback 32 +$269, wick ≥ 50% +$404, **wick ≥ 30% +$888** (2023–26: −$74, +$135, +$299, +$309).
  The wick ≥ 30% result was found by looking at the check years, so it is only a lead; it needs a fresh year
  (e.g. 2021) before it means anything.
- A stop beyond the wick cut the win rate to 38% and lost the edge; a $20 target did about as well as $10.
- The high win rates (51–66%) resemble the user's real 72%, with small wins and occasional larger losses
  capped by the $20 daily account.

### Risk management for the New York breakout candidate (`experiments/risk_analysis.py`)

On its 654 trades (2022–2026, $0.20): worst day −$20, worst week −$79, worst month −$135; 24 of 58 months
lost money; longest losing streak 7 trades; max drawdown −$225; up to 255 weekdays below a previous high.

- **Weekly / monthly loss caps barely matter** (−$40 week: net +$867 vs +$833; −$80 month: +$906). With one
  trade a day capped at −$20, losses already arrive slowly. Available as `--weekly-loss-limit` /
  `--monthly-loss-limit`, but not needed for this strategy.
- **One year, reshuffling real trading weeks (20,000 times):** median +$170; 5th–95th percentile −$167 to
  +$516; 20% chance of a losing year; median drawdown within the year −$161; 31% chance of a drawdown
  worse than $200.
- **Sizing from a $216 balance:**

| Plan | 1-yr median | 1-yr 5th pct | P(below $20 in 1 yr) | 3-yr median | P(below $20 in 3 yrs) | Typical worst drawdown (3 yrs) |
|---|---|---|---|---|---|---|
| Fixed $20 per trade | $434 | $8 | 14.8% | $835 | 20.3% | −58% |
| 10% of balance | $324 | $44 | 2.1% | $671 | 12.0% | −85% |
| **5% of balance** | $315 | $117 | 0.0% | $652 | 0.1% | −56% |
| 2% of balance | $262 | $176 | 0.0% | $383 | 0.0% | −26% |

$20 per trade is about 9% of a $216 balance — large for a strategy whose normal drawdowns are $160–330.
Risking a percentage of the balance (2–5%) shrinks bets after losses, which almost removes the chance of
blowing the account while keeping most of the growth. The $20 daily cap still applies on top.


### Clean 2021 test (locked in `results/locked_for_2021.json`, run once) — `experiments/test_2021.py`

2021 had never been used by any test (gold −4%, $1,670–1,959).

| Locked version | Trades | $0.20 | +$0.10 slippage | Zero cost | Longs / shorts ($0.20) | Verdict |
|---|---|---|---|---|---|---|
| Reaction zone, wick ≥ 30% | 154 | **+$140** (3/4 quarters) | +$117 | +$188 (4/4) | +$62 / +$78 | **PASS** |
| New York breakout, half-range stop | 121 | **−$361** (1/4) | −$444 | −$216 | −$52 / −$309 | **FAIL** |

Reaction zone with wick ≥ 30% by year at $0.20: 2021 +$140, 2022 +$218, 2023 −$74, 2024 +$135, 2025 +$299,
2026 +$309. Only 2021 is a clean confirmation (the 30% setting was noticed on 2023–2026).


### Clean 2020 test (locked in `results/locked_for_2020.json`, run once)

2020: COVID crash and a record rally (gold +25%, $1,451–2,075). Reaction zone, wick ≥ 30%, unchanged:

| | Trades | Net | Win rate | Quarters positive | Longs / shorts |
|---|---|---|---|---|---|
| Zero cost | 153 | +$164 | 62% | 4/4 | +$128 / +$35 |
| **$0.20** | 153 | **+$123** | 61% | 3/4 | +$96 / +$27 |
| $0.20 + $0.10 slippage | 153 | +$72 | 61% | 3/4 | +$84 / −$12 |

**PASS.** By year at $0.20: 2020 +$123, 2021 +$140, 2022 +$218, 2023 −$74, 2024 +$135, 2025 +$299, 2026 +$309
(6 of 7 positive). Clean confirmations so far: 2020 and 2021.

### New York breakout settings map + walk-forward (`experiments/ny_grid.py`, 2022–2026, $0.20)

48 settings: range start 12:30–14:30 UTC × stop at 0.3–1.0 of the range. Heatmap: `results/ny_grid/heatmap.png`.
- 34 of 48 settings had a positive 5-year total; a broad region around 13:15–13:45 UTC with stops at 0.5–1.0
  of the range (12:30 also positive). 14:30 lost at every stop setting. Only 3 settings were positive in all
  5 years (13:30 | 0.5, 13:30 | 0.6, 13:45 | 1.0).
- **Walk-forward re-tuning** (each quarter, switch to the setting with the best previous 4 quarters):
  2023–2026 total **$0** vs **+$738** for the fixed 13:30 | 0.5 candidate. Re-tuning chases noise; keep
  settings fixed.
- Does not change the verdict: the candidate failed the clean 2021 test (−$361).
