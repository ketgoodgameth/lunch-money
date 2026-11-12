# ReactionZoneEA: forward test on an MT5 demo

`ReactionZoneEA.mq5` is a line-by-line port of `strategies/reaction_zone.py` (the saved candidate:
sweep + FVG zones, wick ≥ 30%, 5m confirmation, no stop loss, entries 07:00–10:00 and 12:30–16:00 UTC).
It refuses to run on a real account unless `InpAllowReal` is switched on.

## Versions (set in the EA inputs)

| Version | `InpTakeProfitUSD` | `InpFlatHourUTC` | Backtest 2019–2026 ($0.20 spread) |
|---|---|---|---|
| $10 intraday | 10 | 20 | 60% win rate, +$1,074 |
| $7.5 intraday | 7.5 | 20 | 64% win rate, +$721 |
| $7.5 held | 7.5 | 24 | 74% win rate, +$456 |

Everything else stays at the defaults: 0.01 lots, $20 daily account, max 2 orders a day, stop after a win.

## Set up

1. MT5 → File → Open an Account → Exness → **demo, Standard account** (not Cent: the EA checks for a
   100 oz contract). Use a demo you don't trade by hand; the EA only touches its own positions, but its
   $20 daily account is tracked from its own trades.
2. The EA is already copied to `MQL5/Experts/LunchMoney/` and compiled. In the Navigator (Ctrl+N) →
   Expert Advisors → LunchMoney → right-click → Refresh if it doesn't show.
3. Open a gold chart (`XAUUSD` or `XAUUSDm`, any timeframe) and drag the EA onto it.
   Inputs tab: pick a version from the table. Common tab: tick "Allow Algo Trading".
4. Turn on the **Algo Trading** button in the toolbar. The chart's top-left shows the 1H trend, today's
   orders, how much of the daily account is left, and any setup being watched.
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

## Measuring slippage and delay (EA v1.10)

Every entry and exit also writes a `fill` row to the log: the price on screen when the order was sent, the
price it filled at, the difference (slip, $/oz; above zero is a worse fill), how long the order took, and the
spread. To read them:

```bash
.venv/bin/python experiments/demo_slippage.py
```

It shows average and worst slippage for entries, closes and take profits, the real spread at entry, and what
the 2019–2026 backtest makes at that slippage (from `results/execution_costs`). Aim for 30+ fills, about six
weeks, before trusting the numbers.

A demo fills at the price on screen more often than a real account does, so demo slippage is a lower bound.
The spread and the delay it measures are real.

Server time: entry windows are in UTC. The EA measures the broker's clock itself (`InpServerClock = Auto`)
and shows it on the chart (e.g. `server UTC+0.0`). Exness runs on UTC; MetaQuotes-Demo, which this MT5 is
logged into now, runs on Athens time (UTC+2/+3). In the Strategy Tester the clock can't be measured, so Auto
assumes UTC for Exness servers and New York + 7 for others; pick it by hand if your broker differs.
