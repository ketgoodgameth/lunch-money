"""Trend following across 14 markets with equal risk per market. Trained on 2022, tested on the other years.

    .venv/bin/python trend_multi.py check    # which data files are present, date ranges only
    .venv/bin/python trend_multi.py train    # 2022 only -> results/trend_multi/selection.json
    .venv/bin/python trend_multi.py test     # the rules from `train` on 2023-2026, then 2015-2021

Everything below was fixed on 2026-11-13, before any of this data was downloaded.

Markets (Dukascopy names): eurusd gbpusd usdjpy audusd usdcad usdchf | xauusd xagusd | brentcmdusd lightcmdusd |
usa500idxusd usatechidxusd deuidxeur jpnidxjpy. Hourly bid candles from 2014 (2014 only warms the indicators up).
Price: one a day per market, the close of the last hourly candle that starts between 13:00 and 19:00 UTC
(so the price at about 20:00 UTC; a market with no candle in that span that day keeps its position and earns 0).

Position: signal (+1 long, -1 short) x 1% / the market's daily volatility over the last 60 days (capped at 5x
notional), so every market risks about the same. The portfolio's daily result is the average over the markets
that have a signal that day. Costs, round trip, charged on every change of position size: FX 1.5 bp, gold 2 bp,
silver 15 bp, oil 8 bp, indices 3 bp. Financing: none in the base case (the user's account history shows no swap
on gold); also shown at 2% and 4% a year on the notional held, long or short.

Part A, no fitting: two rules from published work on time-series momentum.
  A1  12-month momentum: long if the price is above its level 250 trading days ago, else short
  A2  the average of 1-, 3- and 12-month momentum signals (21, 63 and 250 days)

Part B, trained on 2022 (the user's method; the year was picked at random among stable gold years). 11 candidates,
all long/short: momentum over 10, 20, 40, 60, 120, 250 days; price against its 10, 20, 50, 100, 200-day average.
A candidate qualifies on 2022 if after costs it is positive for the year and in both half-years and its
neighbours (next shorter and next longer setting of the same type) are positive too.
Pick: the qualifying candidate with the highest return / worst drawdown. If none qualifies, Part B has no rule.

Comparison: the same 14 markets all held long at equal risk (if a rule cannot beat that, it is just "markets went
up").

Pass, same for A1, A2 and the Part B pick, base case costs. All five must hold:
  1. 2023-2026: total above zero and at least 3 of the 4 years positive
  2. the eleven years outside 2022 together: t-statistic of the daily results at least 2
  3. 2015-2021: total above zero
  4. return / worst drawdown higher than the all-long comparison, both in 2023-2026 and over the eleven years
  5. at least half of the markets add a positive amount over the eleven years (it is not one market)
"""
import json
import sys

import numpy as np
import pandas as pd

from common import ROOT

TRAIN = 2022
TEST = (2023, 2024, 2025, 2026)
OLDER = (2015, 2016, 2017, 2018, 2019, 2020, 2021)
OUT = ROOT / "results" / "trend_multi"
COST_BP = {"eurusd": 1.5, "gbpusd": 1.5, "usdjpy": 1.5, "audusd": 1.5, "usdcad": 1.5, "usdchf": 1.5,
           "xauusd": 2.0, "xagusd": 15.0, "brentcmdusd": 8.0, "lightcmdusd": 8.0,
           "usa500idxusd": 3.0, "usatechidxusd": 3.0, "deuidxeur": 3.0, "jpnidxjpy": 3.0}
MARKETS = list(COST_BP)
DIRS = (ROOT / "download", ROOT / "ticks" / "dukascopy" / "download", ROOT / "ticks" / "dukascopy")
VOL_DAYS, TARGET, CAP = 60, 0.01, 5.0
FINANCING = (0.02, 0.04)
LADDER = {"momentum": (10, 20, 40, 60, 120, 250), "average": (10, 20, 50, 100, 200)}
FIXED = {"A1 12-month momentum": ("momentum", (250,)), "A2 1/3/12-month momentum blend": ("momentum", (21, 63, 250))}


def find(market: str):
    for d in DIRS:
        hits = sorted(d.glob(f"{market}-h1-*.csv")) if d.exists() else []
        if hits:
            return hits[-1]
    return None


def load_prices() -> pd.DataFrame:
    """One price a day per market (about 20:00 UTC), weekdays, columns = markets present."""
    cols = {}
    for m in MARKETS:
        f = find(m)
        if f is None:
            continue
        df = pd.read_csv(f, usecols=["timestamp", "close"])
        t = pd.to_datetime(df.timestamp, unit="ms", utc=True)
        ok = (t.dt.hour >= 13) & (t.dt.hour <= 19) & (t.dt.dayofweek < 5)
        s = pd.Series(df.close.to_numpy()[ok.to_numpy()], index=t[ok].dt.tz_localize(None).dt.normalize())
        cols[m] = s.groupby(level=0).last()
    return pd.DataFrame(cols).sort_index()


def signal(p: pd.DataFrame, kind: str, ns) -> pd.DataFrame:
    px = p.ffill()
    if kind == "momentum":
        s = sum(np.sign(px - px.shift(n)) for n in ns) / len(ns)
    else:
        s = sum(np.sign(px - px.rolling(n).mean()) for n in ns) / len(ns)
    return s


def portfolio(p: pd.DataFrame, sig: pd.DataFrame, financing: float = 0.0):
    """Daily portfolio result (fraction of equity) and each market's contribution to it."""
    px = p.ffill()
    ret = px.pct_change().where(p.notna(), 0.0)                 # no price that day: nothing earned
    vol = ret.rolling(VOL_DAYS).std()
    w = (sig * (TARGET / vol)).clip(-CAP, CAP)                   # decided at day t's price
    held = w.shift(1)
    cost = (w - w.shift(1)).abs().shift(1) * pd.Series(COST_BP) / 2 / 1e4
    fin = held.abs() * financing / 365 * 1.4
    per = held * ret - cost.fillna(0) - fin
    n = held.notna().sum(axis=1).replace(0, np.nan)
    contrib = per.div(n, axis=0)
    return contrib.sum(axis=1, min_count=1).dropna(), contrib


def summary(r: pd.Series) -> dict:
    if len(r) < 20:
        return {"days": len(r)}
    pct = r * 100
    eq = pct.cumsum(); dd = float((eq - eq.cummax()).min())
    t = pct.mean() / (pct.std(ddof=1) / np.sqrt(len(pct)))
    return {"days": len(r), "total %": round(float(pct.sum()), 1), "worst dd %": round(dd, 1),
            "ret/dd": round(float(pct.sum()) / -dd, 2) if dd < 0 else float("inf"), "t": round(float(t), 2),
            "yearly vol %": round(float(pct.std(ddof=1) * np.sqrt(252)), 1)}


def yrs(r, years):
    return r[r.index.year.isin(years)]


def label(kind, ns):
    return f"{kind} {'/'.join(str(n) for n in ns)}"


def check():
    for m in MARKETS:
        f = find(m)
        if f is None:
            print(f"{m:15} MISSING"); continue
        ts = pd.read_csv(f, usecols=["timestamp"]).timestamp
        t = pd.to_datetime(ts, unit="ms", utc=True)
        print(f"{m:15} {len(ts):7,} hourly candles, {t.iloc[0].date()} to {t.iloc[-1].date()}  ({f.name})")


def train():
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    p = load_prices()
    print(f"{p.shape[1]} of {len(MARKETS)} markets, {len(p)} days, {p.index[0].date()} to {p.index[-1].date()}\n")
    base = yrs(portfolio(p, pd.DataFrame(1.0, index=p.index, columns=p.columns))[0], [TRAIN])
    print(f"{TRAIN}, all 14 long at equal risk: {summary(base)}\n")
    print(f"== Part A on {TRAIN} (described only) ==")
    print(pd.DataFrame({k: summary(yrs(portfolio(p, signal(p, *v))[0], [TRAIN])) for k, v in FIXED.items()}).T.to_string(), "\n")
    res, half = {}, {}
    for kind, lad in LADDER.items():
        for n in lad:
            r = yrs(portfolio(p, signal(p, kind, (n,)))[0], [TRAIN])
            res[(kind, n)] = summary(r)
            half[(kind, n)] = (float(r[r.index.month <= 6].sum()), float(r[r.index.month > 6].sum()))

    def qualifies(k):
        kind, n = k; lad = LADDER[kind]; i = lad.index(n)
        nb = [(kind, lad[j]) for j in (i - 1, i + 1) if 0 <= j < len(lad)]
        return res[k]["total %"] > 0 and half[k][0] > 0 and half[k][1] > 0 and all(res[x]["total %"] > 0 for x in nb)

    t = pd.DataFrame(res).T
    t.index = [label(k[0], (k[1],)) for k in res]
    t["H1 %"] = [round(100 * half[k][0], 1) for k in res]; t["H2 %"] = [round(100 * half[k][1], 1) for k in res]
    t["qualifies"] = [qualifies(k) for k in res]
    print(f"== Part B: 11 candidates on {TRAIN} ==\n", t.sort_values("ret/dd", ascending=False).to_string(), "\n")
    q = [k for k in res if qualifies(k)]
    pick = max(q, key=lambda k: res[k]["ret/dd"]) if q else None
    print(f"{len(q)} qualify; Part B pick:", label(pick[0], (pick[1],)) if pick else "none")
    (OUT / "selection.json").write_text(json.dumps(
        {"train_year": TRAIN, "markets": list(p.columns), "qualifying": [label(k[0], (k[1],)) for k in q],
         "part_b_pick": None if pick is None else {"kind": pick[0], "n": pick[1]}}, indent=2))


def test():
    pd.set_option("display.width", 250)
    p = load_prices()
    sel = json.loads((OUT / "selection.json").read_text())
    rules = dict(FIXED)
    if sel["part_b_pick"]:
        b = sel["part_b_pick"]
        rules[f"B {label(b['kind'], (b['n'],))} (picked on {TRAIN})"] = (b["kind"], (b["n"],))
    years = sorted(set(p.index.year) - {2014})
    allout = TEST + OLDER
    hold = portfolio(p, pd.DataFrame(1.0, index=p.index, columns=p.columns))[0]
    by_year = lambda r: pd.Series({y: round(float(yrs(r, [y]).sum()) * 100, 1) for y in years})
    print("== comparison: all markets long at equal risk, % by year ==\n", by_year(hold).to_frame("%").T.to_string())
    h_te, h_al = summary(yrs(hold, TEST)), summary(yrs(hold, allout))
    print(f"  2023-2026: {h_te}\n  eleven years outside {TRAIN}: {h_al}\n")
    out = {}
    for name, spec in rules.items():
        r, contrib = portfolio(p, signal(p, *spec))
        te, ol, al = summary(yrs(r, TEST)), summary(yrs(r, OLDER)), summary(yrs(r, allout))
        pos = int((by_year(r)[list(TEST)] > 0).sum())
        mk = (contrib[contrib.index.year.isin(allout)].sum() * 100).round(1).sort_values(ascending=False)
        c1 = te["total %"] > 0 and pos >= 3
        c2 = al["t"] >= 2
        c3 = ol["total %"] > 0
        c4 = te["ret/dd"] > h_te["ret/dd"] and al["ret/dd"] > h_al["ret/dd"]
        c5 = int((mk > 0).sum()) >= (len(mk) + 1) // 2
        ok = c1 and c2 and c3 and c4 and c5
        print(f"== {name} ==\n", by_year(r).to_frame("%").T.to_string())
        print(f"  test 2023-2026: {te}; {pos} of 4 years positive")
        print(f"  older 2015-2021: {ol}")
        print(f"  eleven years outside {TRAIN}: {al}")
        print(f"  by market over those eleven years, %: {mk.to_dict()}")
        for f in FINANCING:
            x = portfolio(p, signal(p, *spec), f)[0]
            print(f"  with financing {f:.0%} a year: test {summary(yrs(x, TEST))['total %']}%, eleven years "
                  f"{summary(yrs(x, allout))['total %']}%, t {summary(yrs(x, allout))['t']}")
        print(f"  -> {'PASS' if ok else 'FAIL'} (1 test positive, 3 of 4 years: {c1}; 2 t >= 2: {c2}; 3 older positive: {c3}; "
              f"4 beats all-long on return/drawdown in both spans: {c4}; 5 half the markets positive: {c5})\n")
        out[name] = {"pass": bool(ok), "checks": [bool(c) for c in (c1, c2, c3, c4, c5)], "test": te, "older": ol, "eleven_years": al}
    (OUT / "test.json").write_text(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    {"check": check, "train": train, "test": test}[sys.argv[1]]()
