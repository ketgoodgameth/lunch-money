# xautest — XAU/USD tick backtesting and live forward testing

One engine, one simulated broker, two tick sources:

```
historical ticks (Dukascopy .bi5 / CSV) ─┐
                                        ├─► Engine ─► SimBroker (fills, SL/TP, costs) ─► Strategy hooks
live ticks (OANDA stream)  ─────────────┘        │
                                                 └─► reports: trades.csv, equity.csv, summary.json, equity.png
```

Because the backtest and the live test share the same code path, a strategy behaves identically in both.
Every live session is recorded to CSV, and re-running that recording as a backtest reproduces the live
trades exactly. That guarantee has its own test (`tests/test_engine.py`).

Live testing is **forward testing**: real quotes in, simulated fills out. No orders are sent anywhere.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"          # add ,s3 for the Dukascopy S3 downloader
.venv/bin/python -m pytest -q
```

## Getting real historical ticks

| Source | How | Notes |
|---|---|---|
| Dukascopy S3 bucket | `python -m xautest fetch --start 2024-01-01 --end 2025-01-01` | Requester-pays: needs your AWS credentials (`aws configure`) and `pip install boto3`. Cost is a few cents per year of XAUUSD. Saved to `data/dukascopy/XAUUSD/YYYY/MM/DD_ticks.bi5` (month is **zero-based**). |
| Dukascopy files you already have | Copy them into `data/dukascopy` in either the daily layout above or the hourly `…/DD/HHh_ticks.bi5` layout | `--source dukascopy` (default) |
| HistData.com XAUUSD tick CSV | Download manually, then `--source csv --csv-format histdata --data path/` | Times are EST with no DST; converted automatically (`--server-tz -5`). |
| MetaTrader 5 "Export ticks" (desktop terminal: View → Symbols → Ticks) | `--source csv --csv-format mt5 --server-tz NY+7` | Timestamps are broker server time. `inspect` detects the right `--server-tz` from gold's daily break. Most brokers use `NY+7`; some use `Europe/Athens`. The MT5 iPad/App Store app can't export ticks. |
| Your own CSV / recorded live ticks | `--source csv --data file.csv` | Columns: `ts_ms` (or ISO `time`), `bid`, `ask`, optional `bid_vol`, `ask_vol`. |

Dukascopy throttles scripted downloads from `datafeed.dukascopy.com` and directs automated clients to the S3
bucket, so this tool doesn't download from the datafeed host.

Always check new data before trusting a backtest on it:

```bash
python -m xautest inspect --start 2024-01-01 --end 2024-02-01
python -m xautest inspect --source csv --csv-format mt5 --data ~/Downloads/XAUUSD_ticks.csv   # also detects server time
```

For XAUUSD the Dukascopy price divisor is 1000. A wrong divisor gives prices outside a plausible range for
gold, and the loader stops with an error. Override it with `--price-scale` if needed.

## Backtest

```bash
python -m xautest backtest \
  --strategy strategies/ema_cross.py:EmaCross --param fast=12 --param slow=48 \
  --start 2024-01-01 --end 2024-07-01 \
  --commission 3.5 --latency-ms 100 --slippage 0.02 --swap-long -6 --swap-short 2 \
  --out results/ema_2024h1
```

## Live forward test

Create a free OANDA practice account, generate an API token, then:

```bash
export OANDA_TOKEN=...  OANDA_ACCOUNT_ID=101-...
python -m xautest live --strategy strategies/ema_cross.py:EmaCross --latency-ms 100 --slippage 0.02
```

It prints a status line and rewrites `results/.../summary.json` every 60 s. It records every tick to
`recordings/`, reconnects on drops, and on Ctrl+C flattens positions and writes the final report.
At the end it prints the command that re-runs the session as a backtest.

To rehearse the live pipeline offline on historical ticks, played at 60× real time:

```bash
python -m xautest live --feed replay --strategy strategies/ema_cross.py --start 2024-03-04 --end 2024-03-05 --speed 60
```

## Writing a strategy

```python
from xautest import Strategy
from xautest.indicators import EMA, ATR

class Breakout(Strategy):
    timeframe = "15min"                       # omit for tick-only strategies
    params = {"lots": 0.1, "lookback": 20}   # overridable with --param

    def on_bar(self, bar):                    # called when the bar is complete
        hi = max(b.high for b in list(self.bars)[-self.p.lookback:])
        if not self.positions and self.tick.ask > hi:
            self.buy(self.p.lots, sl=self.tick.bid - 5, tp=self.tick.ask + 10)

    def on_tick(self, tick): ...              # every quote (tick.bid, tick.ask, tick.spread, tick.ts)
    def on_trade(self, trade): ...            # a position closed (trade.net_pnl, trade.reason)
```

Actions: `buy`, `sell`, `buy_limit`, `sell_limit`, `buy_stop`, `sell_stop` (all with `sl`, `tp`;
pending orders also take `expiry_ms`), `close`, `close_all`, `cancel`, `cancel_all`, `modify(pos, sl=, tp=)`.
State: `self.tick`, `self.now`, `self.time`, `self.bars`, `self.positions`, `self.orders`, `self.net_lots`,
`self.balance`, `self.equity`, `self.live`.

## How fills are simulated (`xautest/broker.py`)

- Buys fill at the **ask** and sells at the **bid**. Longs exit on the bid and shorts on the ask, so the real
  spread of every tick is paid.
- Market and stop orders get `--slippage` against you. Stops and stop losses that gap fill at the gapped
  price, not the stop level. Limits and take profits fill at the touching quote, never worse than the limit.
- `--latency-ms`: an order or close sent at time *t* executes on the first tick at or after *t + latency*.
- Commission is per lot per side. Swap is charged at 17:00 New York (DST-aware), triple on Wednesday.
  Margin is checked on every fill, and a stop-out triggers below a 50 % margin level. One lot is 100 oz.
- There is no intrabar guessing: SL/TP are evaluated on every real quote, in time order.

## Known limits

- **Data ≠ your broker.** Dukascopy, OANDA and your broker quote different spreads and ticks. Use
  `--extra-spread` to match your broker's typical spread, and treat results within a few spreads of
  zero P&L as noise.
- Bars close on the first tick of the next bar, which is the same in backtest and live. In a quiet market
  a bar can be reported late.
- Fills don't model order-book depth; size large orders conservatively with `--slippage`.
- A live session's open positions aren't restored after a restart.
- Pure Python: about 1M ticks/s on the sample strategy, so a year of XAUUSD ticks takes about a minute
  per run. Heavy per-tick logic will be slower.
