# Research log: XAU/USD strategies under the $10 / $20 daily rules

## Saved candidates

| Candidate | Settings | Status |
|---|---|---|
| **Reaction zone, wick ≥ 30%** (your method, rule-based) | `results/candidates/reaction_zone_wick30.json` | **Most promising, saved.** Clean, locked tests: 2020 +$123 PASS, 2021 +$140 PASS, 2019 −$23 narrow FAIL (+$12 before costs). 2019–2026: 6 of 8 years positive, about +$1,130 (+$1,074 in full-year runs with MT5-style exact take-profit fills). Settings map: all 48 neighbouring settings positive over 2021–2026. Volatility-scaled sizing rejected. 2018 test locked, waiting for data. |
| New York opening-range breakout, half-range stop | `results/candidates/ny_breakout_half_stop.json` | Weakened. +$837 over 2022–2026 but **failed the clean 2021 test (−$361)**. |
| Scalping bot (fade / breakout / pullback, 1–15 min bars) | `strategies/scalper.py` | **Rejected.** Built on 2021 (most stable year); round-2 pick +$110 in 2021, then lost in all 7 other years (−$560). |
| Direction-filtered scalps (reaction zone picks the side) | `strategies/bias_scalp.py` | **Rejected.** All 72 versions lost in every year 2019–2026. The direction is worth about +$0.04/oz per scalp (flipped versions lose more in every year), but the $0.20 spread is 5× that. |

All tests use real Dukascopy ticks (2022–2025), the simulated broker in `xautest/`, and the same rules:
$10 risk per trade, stop after the first win, max 2 trades, −$20 daily cap enforced by a $20/day account
with negative balance protection, unlimited leverage, 100 ms latency. R = the $10 risked per trade.

Costs tested: zero (to see the edge before costs), Exness Standard ($0.20 spread), and an assumed
Raw account ($0.05 spread + $3.50 per lot per side; Exness quotes "from 0.0 pips" so the real raw spread is unknown).

Take-profit fills: sections before "High win rate" were run when take profits filled at the crossing tick
(slightly better than MT5 when price jumps through the target, about +$0.15 per take profit). From the
high-win-rate section on, take profits fill exactly at the TP price, like MT5.

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


### Clean 2019 test (locked in `results/locked_for_2019.json`, run once)

2019: gold +18%, $1,266–1,557, the lowest price level tested. Reaction zone, wick ≥ 30%, unchanged:
zero cost +$12 (break-even), **$0.20 −$23 (FAIL, narrowly)**, +$0.10 slippage −$53; 167 trades, 49% winners.

How trades ended (single-run check): 2019 — 27 of 170 hit the $10 target (16%), 137 closed at 20:00 UTC
(−$214), 6 account stop-outs; typical best move in favour $3.38/oz. 2021 — 63 of 154 hit the target (41%),
82 closed at 20:00 (−$318); typical best move $7.08/oz. The entries break even in 2019; the fixed-dollar
target does not fit years when gold's moves are small. Scaling the target with volatility is a new idea and
would need a fresh year (e.g. 2018) to test cleanly.

### The user's risk framework: the account is the stop loss (`experiments/rr_framework.py`)

$10 account per trade (stop-out at $0 = −$10 max, negative balance protection), 0.01 lots, no stop loss,
take profit at 1:3 / 1:5 / 1:8 (+$30 / +$50 / +$80). Entries: the reaction-zone candidate, unchanged.
Targets were set by the user in advance, not fitted.

| $0.20, 2020–2026 | Total | Years positive | Win rate (break-even) | Per trade | Worst drawdown |
|---|---|---|---|---|---|
| **1:5, held until target or stop-out** | **+$1,330** | 6/7 | 21% (17%) | +$1.95 | −$338 |
| 1:3, held | +$1,171 | 6/7 | 29% (25%) | +$1.43 | −$288 |
| 1:8, held | +$985 | 5/7 | 15% (11%) | +$1.69 | −$408 |
| 1:3 / 1:5 / 1:8, closed by 20:00 UTC | +$575 to +$642 | 5/7 | ~42% | ~+$0.55 | about −$270 to −$316 |

- Holding to target beats closing intraday; 2026 lost in every version (−$145 to −$213).
- 2019 (used once before): 1:3 held −$95 (21% winners), 1:5 held −$145 (12.5%). Big fixed-dollar targets
  struggle in quiet, low-price years.
- Held trades stay open a long time: 1:5 in 2024 — median 23 hours, 66% held overnight, 178 nights in the
  year; 2025 — median 5 hours, 33% overnight, 99 nights; longest 326 hours. **Swap (overnight fees) is not
  modelled yet** and could take a large share of profit (e.g. $0.50/night at 0.01 lots ≈ −$90 in 2024).
- Outcomes are all-or-nothing (+$50 or −$10): 2025 had 28 targets (+$1,402) and 101 stop-outs (−$1,010),
  so long losing streaks are normal.

### Volatility-scaled size (`experiments/rr_scaled.py`, 2019–2026, $0.20, $10 account)

Idea: in quiet markets buy more lots so the $10 stop-out sits at 25% of the 14-day average daily range,
target = 3× or 5× that distance (still −$10 / +$30 or +$50). Never below 0.01 lots, so busy years are unchanged.

| Version | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Total | Years + | Worst DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1:3 held, scaled | −134 | +150 | −13 | −59 | +108 | +162 | +452 | −198 | +468 | 4/8 | −339 |
| 1:5 held, scaled | −101 | +137 | −33 | −118 | +387 | +217 | +404 | −228 | +664 | 4/8 | −325 |
| **1:3 held, fixed 0.01** | −95 | +169 | +68 | +195 | +217 | +195 | +533 | −208 | **+1,075** | **6/8** | −288 |
| **1:5 held, fixed 0.01** | −145 | +191 | +155 | +154 | +267 | +337 | +424 | −197 | **+1,185** | **6/8** | −338 |

**Rejected.** Scaling did not rescue 2019 or 2026 and cost money in 2021–2023: a tighter stop-out in quiet
markets gets hit by ordinary noise. Fixed 0.01 lots stays. The 2018 lock (`results/locked_for_2018.json`)
was amended before any 2018 data was downloaded to include the fixed-lot 1:3 / 1:5 versions as primary.

### Reaction-zone settings map (`experiments/rz_grid.py`, 2021–2026, $0.20, intraday, $10 TP framework)

48 settings around the candidate: wick 25/30/35/40% × TP $7.5/10/12.5/15 × swing lookback 12/20/32.

- **All 48 settings are positive over 2021–2026** (median +$672); 26 are positive in at least 5 of 6 years.
  The candidate (wick 30%, TP $10, lookback 20: +$1,028) ranks 2nd of 48. Its neighbours are close
  (lookback 12: +$1,073; wick 25%: +$992), so it sits on a plateau, not a lone spike.
- Wick 25–30% is clearly better than 35–40% (averages $844 / $857 vs $549 / $384). TP $10 is the best
  target on average; lookback matters little.
- 2023 is the weak year for almost every setting (only TP $7.5 versions are positive). 2022 and 2025 are
  strong for all.
- Caveat: 2021–2026 were partly used to pick the candidate, so this map shows robustness, not new evidence.
  The clean evidence remains the locked tests (2020 and 2021 pass, 2019 narrow fail) and the pending 2018 test.

### High win rate: small target, no stop loss (`experiments/high_winrate.py`, 2019–2026, $0.20, $20/day account)

Goal from the user: win rate around 60%+. Reaction-zone entries unchanged; full-year runs. Selection rule set
before running: overall win rate ≥ 60%, then highest worst-year win rate, net positive in ≥ 6 of 8 years.

| Version | Win rate | Worst year | Break-even | Net 2019–26 | Years + | Worst DD | Overnight |
|---|---|---|---|---|---|---|---|
| **TP $7.5, held (selected)** | **74.3%** | 63.6% (2019) | 72.7% | +$456 | 6/8 | −$463 | 32% |
| TP $7.5, intraday | 63.7% | 50.6% (2019) | 60.2% | +$721 | 6/8 | −$168 | 0% |
| TP $10, intraday (saved candidate) | 59.8% | 48.8% (2019) | 55.0% | +$1,074 | 6/8 | −$211 | 0% |
| TP $10, held | 68.6% | 54.1% | 66.7% | +$562 | 5/8 | −$650 | 40% |
| TP 0.15 × daily range, held | 81.6% | 59.8% (2026) | 80.9% | +$207 | 5/8 | −$348 | — |

- Win rate can be pushed up by holding losers (losses become the full −$20 account) or shrinking the target,
  but the break-even win rate rises with it. The cushion (win rate − break-even) is what pays:
  $10 intraday 4.8 points, $7.5 intraday 3.5, $7.5 held 1.6, 0.15 × range held 0.7.
- Targets scaled to the daily range kept 2019 high but collapsed in 2026 (huge ranges → far targets).
- Held versions keep ~1 in 3 trades overnight; swap is not modelled and would eat into the thin cushion.
- 2019 loses in every version. All three main versions are added to the 2018 lock (still unseen).

### Slippage and delay (`experiments/execution_costs.py`, 2019–2026, $0.20 spread, full-year runs)

Slippage hits market entries, 20:00 closes and stop-outs; take profits fill at their price. Net change vs the
baseline (100 ms, no slippage):

| Setup | $10 intraday | $7.5 intraday | $7.5 held |
|---|---|---|---|
| Baseline | +$1,074 | +$721 | +$456 |
| VPS (5 ms), $0.05 slippage | −$79 | −$75 | −$36 |
| VPS (5 ms), $0.10 slippage | −$192 | −$180 | −$101 |
| Home PC (150 ms), $0.10 | −$199 | −$188 | −$134 |
| Manual on phone (3 s), $0.10 | −$242 | −$226 | −$171 |
| Bad fills, $0.30 slippage | −$596 (5/8 years +) | −$563 (3/8) | −$406 (4/8) |

- Slippage matters far more than speed: VPS vs home PC at the same slippage is only ~$8–33 over 8 years.
- Win rates barely move (≤ 3 points); the cost comes from every entry paying a bit more.
- At realistic fills ($0.05–0.10) all three stay positive in 5–6 of 8 years; at $0.30 the edge mostly goes.
  With a VPS and $0.10 slippage: $10 intraday +$882, $7.5 intraday +$541, $7.5 held +$355.

### MT5 Expert Advisor (`mt5/ReactionZoneEA.mq5`)

Line-by-line MQL5 port of the candidate for demo forward testing; compiles with 0 errors / 0 warnings. Same
indicators (own EMA/MACD/Wilder ATR/SuperTrend/RSI, not MT5's built-ins, whose MACD signal is an SMA), daily
$20 account stop, max 2 orders, stop after a win. Detects the broker clock (Exness UTC, MetaQuotes-Demo
Athens time). Refuses real accounts by default. Setup: `mt5/README.md`.

### MT5 Strategy Tester check, October 2025 (`experiments/mt5_parity.py`)

EA in the MT5 tester on Exness (`XAUUSDm`, every tick based on real ticks, ideal execution), $10 intraday:
14 trades, 13 take profits + 1 account stop-out, **+$110.13**.

- vs Python on MetaQuotes-Demo ticks (+$94.06): 11 of 12 trades matched to the minute and side, same outcome.
- vs the Dukascopy backtest (+$122.92): 12 of 15 matched; the rest are price-feed differences of a few cents
  at the 15m rejection or 5m confirmation (e.g. 13 Oct entered 13:05 in Python vs 13:50 in MT5).
- Take profits: MT5 fills exactly at the TP; the Python broker used to fill at the tick price when price
  jumped through it, a little better. That added about +$80 (+$0.15 per win) to the $10 intraday backtest.
  Fixed: the broker now fills take profits exactly at the TP (`tp_fill="exact"`, the default), and the
  high-win-rate, slippage and 1:3 / 1:5 export figures above were re-run with it. Same trades, lower net:
  $10 intraday −$83, $7.5 intraday −$98, $7.5 held −$142, 1:3 held −$45, 1:5 held −$26 over 2019–2026.
- Conclusion: the EA reproduces the backtest's logic; differences come from data, not code.

### MT5 Strategy Tester, 2026 (Jan 1 – Nov 10), and where the losses come from

EA on Exness `XAUUSDm`, $10 intraday: 121 trades, 70% won, **+$95.59** (Python on Dukascopy, Jan–Oct: 117
trades, +$291). Exness only has real ticks from 30 Oct 2026; before that MT5 generated ticks from 1-minute bars
(the Oct 2025 check was also on generated ticks).

- The 97 trades both took are identical: same outcome every time, +$216 in MT5 vs +$212 in Python.
- The whole gap is in trades only one side took (MT5 only: 24 trades, −$120; Python only: 20 trades, +$80), on 15
  shared days where a few cents of price difference picked a different rejection candle. That is feed noise,
  not a code difference: expect roughly ±$100 a year between brokers from this alone.
- September–October 2026 are weak in both (matched trades −$66). 35 of the 39 MT5 losses were full −$20
  stop-outs; the median one took under an hour.
- Why 2026 has so many stop-outs: the daily range is now about $94 (2019: $11), so −$20 is only 0.2 of a day's
  range. Stop-outs went from 3.5% of trades (2019) to 23% (2026); the $10 target got easier at the same time
  (win rate 49% → 74%), which is why the year still makes money.

Tried to fix it (`experiments/loss_analysis.py`, `experiments/breakeven.py`), rules fixed before running:

- **Entry filters**: every trade tagged with 18 entry conditions (time of day, New York clock, wick/body,
  RSI, distance to level and wick in 5m ATRs, 1H trend strength, day's move and range, waiting time, weekday).
  Leave-one-year-out: dropping the worst fifth of trades by any one condition, chosen on the other 7 years,
  lost money on the held-out year for 16 of 18 conditions. The two that "helped" (+$144, +$66) were not
  monotone (the bad bucket sat in the middle) and rested on one year. **No filter adopted.**
- **Fixed stop losses** ($6–15, estimated from each trade's worst drawdown): all worse than no stop
  (+$227 to +$680 vs +$1,074).
- **Break-even stop** (once +$3/5/7, stop to entry or entry +$1): all worse: +$217 to +$871 vs +$1,074.
  Winners usually come back to the entry price before reaching $10, so the stop cuts winners more than it saves
  losers. None beat the baseline in 4 of 6 discovery years. **Rejected.**
- Conclusion: TP $10 intraday, no stop, stays as it is. Its exits are at a local best (also vs held, tighter
  targets and range-scaled targets earlier), and the entry conditions don't predict losers out of sample.

## Scalping bot (`strategies/scalper.py`, `experiments/scalp.py`), built on 2021, tested on the other 7 years

Rules: 0.01 lots, the $20/day account is the only daily limit (no trade cap, no stop after a win), always a stop
loss, take profit and stop sized from the ATR of the signal bars, max hold, all closed by 20:00 UTC. $0.20 spread,
100 ms. Entry types: **fade** (price k ATRs away from EMA20 → trade back), **breakout** (close beyond the last 20
bars' high/low with the EMA trend), **pullback** (EMA trend + RSI(7) dipping and turning back).

Build year picked by a rule fixed first (`experiments/scalp_year_pick.py`: lowest day-to-day variation of the
daily range, trend share < 0.25): **2021** (range CV 0.45, trend share 0.03, median spread $0.35).

Selection rule (fixed first): on 2021, ≥ 250 trades, profit factor ≥ 1.10, profit in ≥ 3 of 4 quarters; highest net.

- **Round 1** (48 versions: 3 entry types × 1m/5m bars × active 07–16 / quiet 01–06 + 16–20 UTC × TP 0.75/1.5 ATR
  × stop 1/2 ATR): **all 48 lost money in 2021**, from −$13 to −$1,275 (up to 6,847 trades). Per trade they lost
  about the spread: before costs the signals are a coin flip. Breakouts were worst (negative even before costs);
  only quiet-hours fades had an edge before costs (~+$0.15 a trade), too small for $0.40–0.80 targets.
- **Round 2** (32 versions, decided after round 1: quiet-hours fades only, 5m/15m bars so targets are a few dollars,
  spread ≤ 15% of the target): most 15m fades made money in 2021. Pick: **15m fade, quiet hours, k = 2, TP 2 ATR,
  stop 3 ATR**: 281 trades, 62% won, +$110, PF 1.23.
- **Unseen years: it lost in all 7.**

| Pick, $0.20 spread | 2019 | 2020 | **2021 (build)** | 2022 | 2023 | 2024 | 2025 | 2026 | 7 test years |
|---|---|---|---|---|---|---|---|---|---|
| Trades | 282 | 234 | 281 | 298 | 295 | 315 | 314 | 203 | |
| Win rate | 53% | 51% | 62% | 52% | 52% | 56% | 51% | 44% | |
| Net | −$75 | −$128 | +$110 | −$33 | −$103 | −$56 | −$5 | −$160 | **−$560** |

  With $0.05 slippage: −$726. On a Raw account ($0.05 spread + $3.50/side): −$507, so cheaper costs don't save
  it: it loses before costs in every test year. The best 15m Asia-only fade: −$112 over the test years (2 of 7
  positive). The best 5m fade: −$1,351, wrecked by the 2025–2026 trends.
- Conclusion: **no scalper survives.** At 0.01 lots and a $0.20 spread, 1–15 minute signals on gold have no edge
  that lasts beyond the year they were tuned on; 2021's mean reversion did not repeat. Not adopted, not locked.
  A scalper would need information these price-only rules don't use (order flow, news timing) or much lower costs
  plus a real edge before costs, which none of these showed.

### More trades a day, 1m entry timing, confidence score (`experiments/more_trades.py`)

27 versions, 2019–2026, TP $10 intraday, $0.20, $20/day account, no stop: max 2 + stop after a win (current) /
max 5 / max 10 (no stop after a win) × entry on the 5m confirmation (current) / 1m MACD cross / limit order
at the swept level ("retest") × confidence off / gate 0.5 / scaled (the day's n-th trade needs a score of
0.25 + 0.125 n). The score is the share of 4 checks on the trade's side: 1H MACD, 15m SuperTrend, today's
closed trades not losing, last 15m candle not a news spike. New strategy options, all off by default:
`exec_mode="m1_retest"`, `conf_base`, `conf_step`, `conf_spike`.

| Version (5m entry unless noted) | Trades/day | Win rate | Net 2019–24 | Net 2025–26 | Net |
|---|---|---|---|---|---|
| **Current: max 2, stop after win** | 1.00 | 59.8% | +$417 | +$657 | **+$1,074** |
| Current + confidence gate 0.5 | 1.00 | 59.9% | +$480 | +$645 | +$1,125 |
| Max 5 or 10, no stop after win | 1.09 | 59.6% | +$466 | +$454 | +$920 |
| Max 5 or 10 + gate 0.5 (rule's pick) | 1.09 | 59.7% | +$519 | +$440 | +$959 |
| 1m MACD entry (best) | 1.00–1.05 | 56.4% | +$162 | +$39 | +$201 |
| Retest limit at the level (best) | 1.00–1.03 | 58.4% | +$130 | +$96 | +$226 |

- **Raising the limit hardly adds trades:** at max 10 the bot traded 1.09 times a day (1,137 days with 1 trade,
  103 with 2, 6 with 3). With no stop, a trade ends at +$10 (median close 14:00 UTC, often after the entry
  windows), at −$20 (account gone), or at 20:00 (46% of trades). So the holding time limits the count,
  not the cap.
- **The extra trades lose:** the 109 second trades after a win made −$154 (56% won). Allowing them cut
  2025–2026 by $200.
- **1m timing is worse:** waiting for the 1m MACD cross drops the good entries (price often leaves without
  coming back): +$201 vs +$1,074. The retest limit fills only 420 times in 8 years.
- **Confidence:** "scaled" never bound (identical to off: the first trade only needs 1 of 4 checks, second trades
  are rare). The gate 0.5 helped a little in 2019–2024 (+$63 on the current version) and cost a little in 2025–26.
- **Rule:** the pick (max 10, 5m, gate 0.5) **failed the 2025–2026 check** (+$440 vs +$657). Nothing adopted;
  the current version stays.
- To trade many times a day like the real account (3,580 orders/year), the trades would have to be much
  shorter (smaller targets or a stop) or overlap (several positions at once). Both change the risk profile;
  the real trade log would show which one the user does.

### Direction-filtered scalps (`strategies/bias_scalp.py`, `experiments/bias_scalp.py`, 2019–2026)

Idea from the real trade history (kept local, not in the repo): fast trades won most often, and trades on the
reaction-zone strategy's side did better than trades against it. So the strategy picks the side, and a scalper
trades only that side, from the strategy's entry (no look-ahead) while its trade is open ("trade") or until
20:00 UTC ("day"). The scalps run on their own $20/day account, 0.01 lots, $0.20 spread, 100 ms, 30 s between scalps,
no trade limit. Grid (72 versions): entry at market / on a 1m pullback candle / on a 1m MACD cross × TP $1/2/3
× stop $2/5 × max hold 2/10 min × window trade/day. Rule fixed before running: 2019–2024, ≥300 trades, profit in
≥4 of 6 years, highest net; checks: 2025–2026 positive, and the side-flipped control must be worse.

| Best of each entry type ($0.20 spread) | Trades/day | Win rate | Per trade | Net 2019–24 | Net 2025–26 |
|---|---|---|---|---|---|
| 1m MACD, TP $3, stop $5, hold 10, trade window | 12.7 | 42.3% | −$0.20 | −$2,698 | −$230 |
| … same, side flipped | | 41.1% | −$0.24 | −$3,120 | −$494 |
| Pullback, TP $3, stop $5, hold 10, trade window | 29.7 | 43.4% | −$0.17 | −$5,913 | −$355 |
| … same, side flipped | | 41.7% | −$0.23 | −$7,444 | −$1,358 |
| Market, TP $3, stop $5, hold 10, trade window | 32.5 | 42.9% | −$0.19 | −$6,984 | −$551 |
| … same, side flipped | | 41.6% | −$0.24 | −$8,564 | −$1,836 |

- **Every one of the 72 versions lost in every year**, so nothing passes the rule. Per scalp they lose about the
  spread ($0.17–0.21 at 0.01 lots, where $ = $/oz).
- **The direction is real but small:** each flipped control loses more in all 8 years, about $0.04–0.06/oz per
  scalp. That is the strategy's edge spread over many tiny trades: a quarter of the cost of one spread.
- **Costs:** with a $0.30 spread or $0.05 slippage the market version loses another ~$3,000. A wider target
  does not help: the 2 and 10 minute hold limits end most scalps before the target.
- **Conclusion:** the strategy's side is useful as a filter for discretionary trading (don't trade against
  it once it has entered), but it is not an edge a mechanical scalper can pay the spread with. **Rejected.**
