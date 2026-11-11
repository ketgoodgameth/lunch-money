"""Risk analysis of a strategy's trade list (default: the New York breakout candidate, 2022-2026, $0.20).

    .venv/bin/python risk_analysis.py [trades.csv] [start_balance]

1. History: worst day / week / month, losing streaks, drawdown.
2. Weekly / monthly loss caps replayed on the real trade sequence.
3. One-year outcomes by reshuffling whole trading weeks (keeps good and bad stretches together).
4. Fixed $20 risk vs a percentage of the balance, from the given starting balance.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
rng = np.random.default_rng(7)


def load(path):
    t = pd.read_csv(path, parse_dates=["entry_time"])
    t["day"] = t.entry_time.dt.tz_convert("UTC").dt.normalize().dt.tz_localize(None)
    t["R"] = t.net_pnl / t.risk_usd.clip(lower=1e-9)
    days = pd.bdate_range(t.day.min(), t.day.max())
    daily = t.groupby("day").net_pnl.sum().reindex(days, fill_value=0.0)
    return t, daily


def history(t, daily):
    eq = daily.cumsum()
    weekly, monthly = daily.resample("W-SUN").sum(), daily.resample("ME").sum()
    streak = best = 0
    for x in t.net_pnl:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    peak_gap = (eq < eq.cummax()).astype(int)
    longest_flat = int(peak_gap.groupby((peak_gap == 0).cumsum()).sum().max())
    print(f"history: {len(t)} trades over {len(daily)} weekdays, net ${t.net_pnl.sum():.0f}")
    print(f"  worst day ${daily.min():.0f}, worst week ${weekly.min():.0f}, worst month ${monthly.min():.0f} "
          f"({monthly.idxmin():%b %Y}); losing months {int((monthly < 0).sum())} of {len(monthly)}")
    print(f"  longest losing streak {best} trades; max drawdown ${float((eq - eq.cummax()).min()):.0f}; "
          f"longest time below a previous high {longest_flat} weekdays")


def replay_caps(t, weekly=0.0, monthly=0.0):
    taken, wk, mo = [], {}, {}
    for _, r in t.iterrows():
        w, m = r.day.isocalendar()[:2], (r.day.year, r.day.month)
        if (weekly and wk.get(w, 0) <= -weekly) or (monthly and mo.get(m, 0) <= -monthly):
            continue
        taken.append(r.net_pnl)
        wk[w] = wk.get(w, 0) + r.net_pnl
        mo[m] = mo.get(m, 0) + r.net_pnl
    x = np.array(taken)
    eq = np.cumsum(x)
    return len(x), x.sum(), float((eq - np.maximum.accumulate(eq)).min())


def bootstrap_year(daily, n=20_000, weeks=52):
    blocks = [g.values for _, g in daily.groupby(pd.Grouper(freq="W-SUN")) if len(g)]
    idx = rng.integers(0, len(blocks), size=(n, weeks))
    totals, dds = np.empty(n), np.empty(n)
    for i in range(n):
        path = np.concatenate([blocks[j] for j in idx[i]])
        eq = np.cumsum(path)
        totals[i], dds[i] = eq[-1], (eq - np.maximum.accumulate(eq)).min()
    return totals, dds


def sizing(t, start, years=1, n=20_000):
    by_week = [g.R.values for _, g in t.groupby(pd.Grouper(key="day", freq="W-SUN")) if len(g)]
    weeks = 52 * years
    idx = rng.integers(0, len(by_week), size=(n, weeks))
    plans = {"fixed $20 per trade": ("fixed", 20.0), "5% of balance": ("pct", 0.05),
             "10% of balance": ("pct", 0.10), "2% of balance": ("pct", 0.02)}
    rows = []
    for name, (kind, v) in plans.items():
        ends, ruined, worst_dd = np.empty(n), 0, np.empty(n)
        for i in range(n):
            bal, peak, dd = start, start, 0.0
            for j in idx[i]:
                for r in by_week[j]:
                    risk = v if kind == "fixed" else bal * v
                    if bal < 20 or risk < 1:                      # can't fund a minimum trade any more
                        break
                    bal += r * min(risk, bal)
                    peak = max(peak, bal); dd = min(dd, bal / peak - 1)
            ends[i], worst_dd[i] = bal, dd
            ruined += bal < 20
        rows.append({"plan": name, "median end": round(np.median(ends)), "5th pct end": round(np.percentile(ends, 5)),
                     "95th pct end": round(np.percentile(ends, 95)), "P(end below start)": f"{(ends < start).mean():.0%}",
                     "P(balance < $20)": f"{ruined / n:.1%}", "median worst drawdown": f"{np.median(worst_dd):.0%}"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results/half_stop/trades/trades_all_years.csv"
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 216.0
    t, daily = load(path)
    history(t, daily)
    print("\nloss caps replayed on the real sequence (trades, net, max drawdown):")
    for label, w, m in [("none", 0, 0), ("week -$40", 40, 0), ("week -$60", 60, 0), ("month -$80", 0, 80),
                        ("month -$120", 0, 120), ("week -$60 + month -$120", 60, 120)]:
        n, net, dd = replay_caps(t, w, m)
        print(f"  {label:<26} {n:4d} trades  net ${net:7.0f}  max drawdown ${dd:6.0f}")
    totals, dds = bootstrap_year(daily)
    print(f"\none year, reshuffled weeks x {len(totals):,}: median ${np.median(totals):.0f}; "
          f"5th-95th pct ${np.percentile(totals, 5):.0f} to ${np.percentile(totals, 95):.0f}; "
          f"P(losing year) {(totals < 0).mean():.0%}")
    print(f"  drawdown within the year: median ${np.median(dds):.0f}, 5% worst ${np.percentile(dds, 5):.0f}; "
          f"P(drawdown worse than $200) {(dds < -200).mean():.0%}")
    for yrs in (1, 3):
        print(f"\nsizing from ${start:.0f}, {yrs} year(s), {20_000:,} reshuffles:")
        print(sizing(t, start, yrs).to_string(index=False))
