# ReactionZoneEA: forward test on an MT5 demo

`ReactionZoneEA.mq5` is a line-by-line port of `strategies/reaction_zone.py` (the saved candidate:
sweep + FVG zones, wick ≥ 30%, 5m confirmation, entries 07:00–10:00 and 12:30–16:00 UTC). Since v1.20 it
trades the growth version by default: a $20 stop loss on every trade and no daily account.
It refuses to run on a real account unless `InpAllowReal` is switched on.

## Versions (set in the EA inputs)

| Version | `InpTakeProfitUSD` | `InpFlatHourUTC` | Backtest 2019–2026 ($0.20 spread) |
|---|---|---|---|
| $10 intraday | 10 | 20 | 60% win rate, +$1,074 |
| $7.5 intraday | 7.5 | 20 | 64% win rate, +$721 |
| $7.5 held | 7.5 | 24 | 74% win rate, +$456 |

Those figures are for the old rules (`InpStopDist = 0`, `InpDailyAccount = 20`, 0.01 lots). Max 2 orders a
day and stop after a win stay at the defaults in every version.

## Money settings (v1.20)

| Input | Default | What it does |
|---|---|---|
| `InpStopDist` | 20 | Stop loss, $ per oz from the entry price. 0 = no stop loss. |
| `InpRiskPct` | 0 | Size each trade so the stop loses this % of the balance (rounded down to the lot step, never below the minimum lot). 0 = always `InpLots`. |
| `InpLots` | 0.01 | Fixed size, used when `InpRiskPct` is 0. |
| `InpDdBrakePct` | 0 | Halve the risk while the balance is this % or more below its high. 0 = off. |
| `InpDailyAccount` | 0 | Old rules: close everything once the day's loss reaches this many $. 0 = off. |

- Growth version from `experiments/portfolio_growth.py` ($10 intraday, $20 stop): defaults as they are for the
  smallest size, or `InpRiskPct = 3`. On a $1,000 account 0.01 lots with a $20 stop is already 2% per trade, so
  3% stays at 0.01 lots until the balance passes about $1,334.
- Backtest 2018–2026 from $1,000 at $0.20 spread: 0.01 lots → $2,046, 3% → $2,970. Those figures lean on
  2025–2026 and assume perfect fills; see RESEARCH.md before trusting them.
- Old $20-a-day rules: `InpStopDist = 0`, `InpDailyAccount = 20`.
- The balance used for sizing is the whole account's, so use an account the EA has to itself.

## Set up

1. MT5 → File → Open an Account → Exness → **demo, Standard account** (not Cent: the EA checks for a
   100 oz contract). Use a demo you don't trade by hand: the EA only touches its own positions, but with
   `InpRiskPct` it sizes from the whole account balance. A $1,000 demo matches the backtest.
2. The EA is already copied to `MQL5/Experts/LunchMoney/` and compiled. In the Navigator (Ctrl+N) →
   Expert Advisors → LunchMoney → right-click → Refresh if it doesn't show.
3. Open a gold chart (`XAUUSD` or `XAUUSDm`, any timeframe) and drag the EA onto it.
   Inputs tab: pick a version from the table. Common tab: tick "Allow Algo Trading".
4. Turn on the **Algo Trading** button in the toolbar. The chart's top-left shows the 1H trend, today's
   orders, the next trade's size and the balance, and any setup being watched.
5. MT5 must stay open and online. On a Mac that means not sleeping (or use a VPS).

## Checking it against the backtest

- Every signal and trade is written to `MQL5/Files/ReactionZoneEA_<login>.csv`
  (in the MT5 data folder: File → Open Data Folder).
- Strategy Tester first (optional, recommended): View → Strategy Tester → Expert `LunchMoney\ReactionZoneEA`,
  symbol gold, "Every tick based on real ticks", a recent month. Its trades should mostly match
  `results/high_winrate/trades.csv` for the same version and dates; small differences come from
  Exness vs Dukascopy prices.
- After a few weeks of demo, export the demo's ticks and trades; `experiments/trade_log.py` can compare
  them with a Python replay of the same days.

## Measuring slippage and delay (EA v1.10+)

Every entry and exit also writes a `fill` row to the log: the price on screen when the order was sent, the
price it filled at, the difference (slip, $/oz; above zero is a worse fill), how long the order took, and the
spread. To read them:

```bash
.venv/bin/python experiments/demo_slippage.py
```

It shows average and worst slippage for entries, closes, stop losses and take profits, the real spread at entry, and what
the 2019–2026 backtest makes at that slippage (from `results/execution_costs`). Aim for 30+ fills, about six
weeks, before trusting the numbers.

A demo fills at the price on screen more often than a real account does, so demo slippage is a lower bound.
The spread and the delay it measures are real.

Server time: entry windows are in UTC. The EA measures the broker's clock itself (`InpServerClock = Auto`)
and shows it on the chart (e.g. `server UTC+0.0`). Exness runs on UTC; MetaQuotes-Demo, which this MT5 is
logged into now, runs on Athens time (UTC+2/+3). In the Strategy Tester the clock can't be measured, so Auto
assumes UTC for Exness servers and New York + 7 for others; pick it by hand if your broker differs.
