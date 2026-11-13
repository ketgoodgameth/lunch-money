"""Daily trend following on gold: a rule for when to be long, short or flat. Trained on 2022, tested on newer years.

    .venv/bin/python trend_daily.py train    # 2022 only -> results/trend_daily/selection.json
    .venv/bin/python trend_daily.py test     # the rules from `train` on 2023-2026, then 2015-2021

Everything below was fixed on 2026-11-13 before any daily trend result was looked at.

Data: one price a day, the mid quote at 20:00 UTC (weekdays; days without a quote in the 5 minutes before are
skipped). The rule is evaluated on that price and the position is changed at that price. Position: +1, 0 or -1
ounce (0.01 lots). Costs: $0.10 per ounce per side (the $0.20 spread per round trip; a flip from long to short
pays both sides). Swap: none in the base case; longs are also shown paying $0.10, $0.20 and $0.40 per ounce per
night (x1.4 for weekends), shorts get nothing. Results: $ per ounce, and % of price so that years compare.
Indicators use earlier years to warm up; a year's result counts only days inside that year.

Part A, no fitting: two rules taken from published work, not from this data.
  A1  12-month momentum, long/short: long if the price is above its level 250 trading days ago, else short
  A2  200-day average, long/flat: long while the price is above its 200-day average, else flat

Part B, trained on 2022 (the user's method). 28 candidates:
  momentum      price vs its level L days ago, L = 10, 20, 40, 60, 120, 250
  average       price vs its N-day average, N = 10, 20, 50, 100, 200
  breakout      long on a new N-day closing high, short on a new N-day closing low, out when the close passes
                the N/2-day opposite extreme, N = 10, 20, 55
  each as long/short and as long/flat.
A candidate qualifies on 2022 if after costs it is positive for the year and in both half-years, and its
neighbours (same type and version, next shorter and next longer setting) are positive too.
Pick: the qualifying candidate with the highest return / worst drawdown. If none qualifies, Part B has no rule.

Test, same for A1, A2 and the Part B pick (base case costs):
  on 2023-2026: total above zero, at least 3 of 4 years positive, AND return / worst drawdown higher than simply
  holding one ounce (otherwise it is just "gold went up");
  on all eleven years outside 2022 together: t-statistic of the daily % results at least 2;
  on 2015-2021: total above zero.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from common import ROOT, YEAR_FILES, cache_for, year_file

TRAIN = 2022
TEST = (2023, 2024, 2025, 2026)
OLDER = (2015, 2016, 2017, 2018, 2019, 2020, 2021)
OUT = ROOT / "results" / "trend_daily"
SIDE_COST = 0.10
SWAPS = (0.10, 0.20, 0.40)
MAX_AGE = 5 * 60_000
HOUR = 20


def daily_prices() -> pd.Series:
    """Mid quote at 20:00 UTC on every weekday of every year on disk."""
    out = {}
    for y in sorted(YEAR_FILES):
        c = cache_for(year_file(y))
        ts = np.load(c / "ts.npy", mmap_mode="r"); bid = np.load(c / "bid.npy", mmap_mode="r"); ask = np.load(c / "ask.npy", mmap_mode="r")
        d = date(y, 1, 1)
        while d.year == y:
            if d.weekday() < 5:
                q = int(datetime(d.year, d.month, d.day, HOUR, tzinfo=timezone.utc).timestamp() * 1000)
                i = int(np.searchsorted(ts, q, "right")) - 1
                if i >= 0 and q - int(ts[i]) <= MAX_AGE:
                    out[pd.Timestamp(d)] = (float(bid[i]) + float(ask[i])) * 0.5
            d += timedelta(days=1)
    return pd.Series(out).sort_index()


def positions(p: pd.Series, kind: str, n: int, short: bool) -> pd.Series:
    """Position held from each day's 20:00 until the next day's, decided on prices up to that day."""
    if kind == "momentum":
        s = np.sign(p - p.shift(n))
    elif kind == "average":
        s = np.sign(p - p.rolling(n).mean())
    else:                                   # breakout with an N/2-day exit
        hi, lo = p.shift(1).rolling(n).max(), p.shift(1).rolling(n).min()
        xhi, xlo = p.shift(1).rolling(n // 2).max(), p.shift(1).rolling(n // 2).min()
        pos, cur = [], 0.0
        for px, h, l, xh, xl in zip(p, hi, lo, xhi, xlo):
            if np.isnan(h):
                pos.append(np.nan); continue
            if px >= h:
                cur = 1.0
            elif px <= l:
                cur = -1.0
            elif cur > 0 and px <= xl:
                cur = 0.0
            elif cur < 0 and px >= xh:
                cur = 0.0
            pos.append(cur)
        s = pd.Series(pos, index=p.index)
    return s if short else s.clip(lower=0)


def results(p: pd.Series, pos: pd.Series, swap: float = 0.0) -> pd.DataFrame:
    """Per day: $ per ounce and % of price, after costs, for the position held into that day."""
    held = pos.shift(1)
    change = (pos - pos.shift(1)).abs().shift(1)                 # the trade made at the previous 20:00
    usd = held * p.diff() - SIDE_COST * change.fillna(0) - swap * 1.4 * (held > 0)
    return pd.DataFrame({"usd": usd, "pct": usd / p.shift(1) * 100, "held": held}).dropna()


def summary(r: pd.DataFrame) -> dict:
    if len(r) < 20:
        return {"days": len(r)}
    eq = r.pct.cumsum()
    dd = float((eq - eq.cummax()).min())
    t = r.pct.mean() / (r.pct.std(ddof=1) / np.sqrt(len(r))) if r.pct.std() > 0 else 0.0
    return {"days": len(r), "total $": round(r.usd.sum(), 1), "total %": round(r.pct.sum(), 1), "worst dd %": round(dd, 1),
            "ret/dd": round(r.pct.sum() / -dd, 2) if dd < 0 else float("inf"), "t": round(float(t), 2),
            "in market %": round(100 * (r.held != 0).mean()), "trades": int((r.held.diff().abs() > 0).sum())}


def candidates():
    for short in (True, False):
        for n in (10, 20, 40, 60, 120, 250):
            yield "momentum", n, short
        for n in (10, 20, 50, 100, 200):
            yield "average", n, short
        for n in (10, 20, 55):
            yield "breakout", n, short


LADDER = {"momentum": (10, 20, 40, 60, 120, 250), "average": (10, 20, 50, 100, 200), "breakout": (10, 20, 55)}
FIXED = {"A1 12-month momentum, long/short": ("momentum", 250, True), "A2 200-day average, long/flat": ("average", 200, False)}


def label(kind, n, short):
    return f"{kind} {n}, {'long/short' if short else 'long/flat'}"


def year_of(r: pd.DataFrame, years) -> pd.DataFrame:
    return r[r.index.year.isin(years)]


def train():
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    p = daily_prices()
    p.to_csv(OUT / "daily_2000utc.csv", header=["mid"])
    print(f"{len(p)} daily prices, {p.index[0].date()} to {p.index[-1].date()}\n")
    res, half = {}, {}
    for kind, n, short in candidates():
        r = year_of(results(p, positions(p, kind, n, short)), [TRAIN])
        res[(kind, n, short)] = summary(r)
        half[(kind, n, short)] = (r[r.index.month <= 6].pct.sum(), r[r.index.month > 6].pct.sum())
    t = pd.DataFrame(res).T
    t.index = [label(*k) for k in res]

    def pos_ok(k):
        return res[k]["total %"] > 0

    def qualifies(k):
        kind, n, short = k
        lad = LADDER[kind]; i = lad.index(n)
        nb = [(kind, lad[j], short) for j in (i - 1, i + 1) if 0 <= j < len(lad)]
        return pos_ok(k) and half[k][0] > 0 and half[k][1] > 0 and all(pos_ok(x) for x in nb)

    t["H1 %"] = [round(half[k][0], 1) for k in res]; t["H2 %"] = [round(half[k][1], 1) for k in res]
    t["qualifies"] = [qualifies(k) for k in res]
    bh = summary(year_of(results(p, pd.Series(1.0, index=p.index)), [TRAIN]))
    print(f"== {TRAIN}: holding one ounce: {bh}\n")
    print(f"== Part A on {TRAIN} (described only) ==")
    print(pd.DataFrame({k: summary(year_of(results(p, positions(p, *v)), [TRAIN])) for k, v in FIXED.items()}).T.to_string(), "\n")
    print(f"== Part B: 28 candidates on {TRAIN} ==\n", t.sort_values("total %", ascending=False).to_string(), "\n")
    q = [k for k in res if qualifies(k)]
    pick = max(q, key=lambda k: res[k]["ret/dd"]) if q else None
    print(f"{len(q)} qualify; Part B pick:", label(*pick) if pick else "none")
    (OUT / "selection.json").write_text(json.dumps(
        {"train_year": TRAIN, "qualifying": [label(*k) for k in q],
         "part_b_pick": None if pick is None else {"kind": pick[0], "n": pick[1], "short": pick[2]}}, indent=2))


def test():
    pd.set_option("display.width", 240); pd.set_option("display.max_rows", 200)
    p = daily_prices()
    sel = json.loads((OUT / "selection.json").read_text())
    rules = dict(FIXED)
    if sel["part_b_pick"]:
        b = sel["part_b_pick"]
        rules["B " + label(b["kind"], b["n"], b["short"]) + f" (picked on {TRAIN})"] = (b["kind"], b["n"], b["short"])
    hold = results(p, pd.Series(1.0, index=p.index))
    years = sorted(set(p.index.year))
    print("== holding one ounce (the comparison) ==")
    print(pd.DataFrame({y: summary(year_of(hold, [y])) for y in years}).loc[["total $", "total %", "worst dd %"]].to_string())
    for lab, ys in (("2023-2026", TEST), ("2015-2021", OLDER)):
        print(f"  {lab}: {summary(year_of(hold, ys))}")
    print()
    out = {}
    for name, spec in rules.items():
        r = results(p, positions(p, *spec))
        print(f"== {name} ==")
        print(pd.DataFrame({y: summary(year_of(r, [y])) for y in years}).loc[["total $", "total %", "worst dd %", "in market %", "trades"]].to_string())
        te, ol, al = year_of(r, TEST), year_of(r, OLDER), year_of(r, TEST + OLDER)
        s_te, s_ol, s_al, s_bh = summary(te), summary(ol), summary(al), summary(year_of(hold, TEST))
        pos = int((te.groupby(te.index.year).pct.sum() > 0).sum())
        c1 = s_te["total %"] > 0 and pos >= 3
        c2 = s_te["ret/dd"] > s_bh["ret/dd"]
        c3 = s_al["t"] >= 2
        c4 = s_ol["total %"] > 0
        ok = c1 and c2 and c3 and c4
        print(f"  test 2023-2026: {s_te}; {pos} of 4 years positive")
        print(f"  holding 2023-2026: return/drawdown {s_bh['ret/dd']} -> strategy {'beats' if c2 else 'does not beat'} it ({s_te['ret/dd']})")
        print(f"  older 2015-2021: {s_ol}")
        print(f"  all 11 years outside {TRAIN}: t {s_al['t']}")
        for sw in SWAPS:
            x = summary(year_of(results(p, positions(p, *spec), sw), TEST))
            print(f"  test with long swap ${sw:.2f}/oz per night: total ${x['total $']}, {x['total %']}%")
        print(f"  -> {'PASS' if ok else 'FAIL'} (positive and 3 of 4 years: {c1}; beats holding on return/drawdown: {c2}; "
              f"t >= 2 over 11 years: {c3}; older years positive: {c4})\n")
        out[name] = {"pass": bool(ok), "test_total_pct": s_te["total %"], "test_ret_dd": s_te["ret/dd"], "hold_ret_dd": s_bh["ret/dd"],
                     "t_11_years": s_al["t"], "older_total_pct": s_ol["total %"]}
    (OUT / "test.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    {"train": train, "test": test}[sys.argv[1]]()
