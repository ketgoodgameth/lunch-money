"""Time-of-day drift in gold: hold one fixed window a day. Trained on 2022, tested on newer years.

    .venv/bin/python tod_drift.py train    # 2022 only: the two fixed-in-advance windows + the window search -> selection.json
    .venv/bin/python tod_drift.py test     # the windows from `train` on 2023-2026, then 2015-2021

Everything below was fixed on 2026-11-13 before any hourly data was looked at.

Trade: enter at the start of the window, exit at its end, 1 oz (0.01 lots), no stop. Price = last mid quote at
that time (skipped if there is no quote in the 5 minutes before: closed market). Cost: $0.20 per trade (the
spread). Results in $ per oz and in basis points of the entry price (bp, so years at $1,200 and $4,000 compare).

Part A, no fitting. The windows come from published work on the London gold fixings, not from this data:
  A1  short from the morning fix to the afternoon fix: 10:30 -> 15:00 London time
  A2  long from the afternoon fix to the next morning fix: 15:00 -> 10:30 London time, Monday-Thursday entries
      (no weekend holds). Held overnight, so swap matters: shown at $0, $0.10, $0.20 and $0.40 per oz per night
      (x1.5: four entries a week carry six nights of swap because Wednesday is charged three times).

Part B, trained on 2022 (the user's method). Candidates: every window on whole UTC hours inside 00:00-21:00,
2 to 12 hours long, long or short (330 in all). A candidate qualifies on 2022 if, after the $0.20 cost,
  its average is positive, both half-years are positive, at least 8 of the 12 months are positive, and its four
  neighbours (start or end moved by one hour, same side) are positive too.
Pick: the qualifying window with the highest t-statistic. If none qualifies, Part B has no strategy.

Test, same for A1, A2 and the Part B pick. On 2023-2026: total after costs above zero, at least 3 of the 4 years
positive, and t-statistic of the daily results at least 2. A long window must also beat the average long window
of the same length in at least 3 of the 4 years (otherwise it is just "gold went up"); a short window has the
market against it, so no such test. 2015-2021 is a second check (reported, not part of the pass rule).
"""
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from common import ROOT, YEAR_FILES, cache_for, year_file

TRAIN = 2022
TEST = (2023, 2024, 2025, 2026)
OLDER = (2015, 2016, 2017, 2018, 2019, 2020, 2021)
OUT = ROOT / "results" / "tod_drift"
COST = 0.20
LONDON = ZoneInfo("Europe/London")
MAX_AGE = 5 * 60_000
SWAPS = (0.0, 0.10, 0.20, 0.40)
H = 3_600_000


class Year:
    def __init__(self, y: int):
        c = cache_for(year_file(y))
        self.y = y
        self.ts = np.load(c / "ts.npy", mmap_mode="r")
        self.bid = np.load(c / "bid.npy", mmap_mode="r")
        self.ask = np.load(c / "ask.npy", mmap_mode="r")
        self.days = [date(y, 1, 1) + timedelta(days=i) for i in range((date(y + 1, 1, 1) - date(y, 1, 1)).days)]

    def mid(self, q: np.ndarray) -> np.ndarray:
        """Last mid quote at each time in q (ms), NaN if older than 5 minutes."""
        i = np.searchsorted(self.ts, q, "right") - 1
        ok = (i >= 0)
        i = np.clip(i, 0, len(self.ts) - 1)
        ok &= (q - self.ts[i]) <= MAX_AGE
        m = (self.bid[i] + self.ask[i]) * 0.5
        return np.where(ok, m, np.nan)

    def utc_grid(self) -> pd.DataFrame:
        """Mid price at every whole UTC hour 0..21 of every weekday: rows = days, columns = hours."""
        d0 = np.array([int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000) for d in self.days])
        g = np.stack([self.mid(d0 + h * H) for h in range(22)], axis=1)
        df = pd.DataFrame(g, index=pd.to_datetime([d.isoformat() for d in self.days]), columns=range(22))
        return df[df.index.dayofweek < 5]

    def long_from_london_hour(self, hh: int, hold_ms: int) -> pd.Series:
        """Long 1 oz from hh:00 London for hold_ms, Monday-Thursday entries, after the spread: the comparison
        windows for A2 (same length, same days, other start times)."""
        idx = pd.to_datetime([d.isoformat() for d in self.days])
        q = np.array([int(datetime.combine(d, time(hh, 0), LONDON).timestamp() * 1000) for d in self.days])
        p = self.mid(q + hold_ms) - self.mid(q) - COST
        return pd.Series(np.where(idx.dayofweek < 4, p, np.nan), index=idx).dropna()

    def london(self, hh: int, mm: int, shift_days: int = 0) -> np.ndarray:
        q = np.array([int(datetime.combine(d + timedelta(days=shift_days), time(hh, mm), LONDON).timestamp() * 1000)
                      for d in self.days])
        return self.mid(q)


def stats(pnl: pd.Series, entry: pd.Series) -> dict:
    """pnl: $ per oz after costs, indexed by day; entry: entry price."""
    pnl = pnl.dropna()
    if len(pnl) < 20:
        return {"n": len(pnl)}
    bp = (pnl / entry.loc[pnl.index]) * 1e4
    m = pnl.groupby(pnl.index.month).sum()
    h = pnl.groupby(pnl.index.month <= 6).sum()
    return {"n": len(pnl), "total $": round(pnl.sum(), 1), "avg $": round(pnl.mean(), 3), "avg bp": round(bp.mean(), 2),
            "t": round(bp.mean() / (bp.std(ddof=1) / np.sqrt(len(bp))), 2), "win%": round(100 * (pnl > 0).mean(), 1),
            "months+": int((m > 0).sum()), "halves+": int((h > 0).sum())}


def window_pnl(g: pd.DataFrame, s: int, e: int, side: int) -> tuple[pd.Series, pd.Series]:
    return side * (g[e] - g[s]) - COST, g[s]


def candidates():
    for s in range(0, 20):
        for e in range(s + 2, min(s + 12, 21) + 1):
            for side in (1, -1):
                yield s, e, side


def fixed_windows(yr: Year) -> dict:
    """A1 and A2 as (pnl after the spread, entry price, nights held)."""
    idx = pd.to_datetime([d.isoformat() for d in yr.days])
    am, pm, am_next = yr.london(10, 30), yr.london(15, 0), yr.london(10, 30, 1)
    wd = idx.dayofweek
    a1 = pd.Series(np.where(wd < 5, -(pm - am) - COST, np.nan), index=idx)
    a2 = pd.Series(np.where(wd < 4, (am_next - pm) - COST, np.nan), index=idx)
    return {"A1 short 10:30-15:00 London": (a1, pd.Series(am, index=idx)),
            "A2 long 15:00-10:30 London (overnight)": (a2, pd.Series(pm, index=idx))}


def name_of(s, e, side):
    return f"B {'long' if side > 0 else 'short'} {s:02d}:00-{e:02d}:00 UTC"


def train():
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 400)
    yr = Year(TRAIN)
    print(f"== Part A on {TRAIN} (described only; nothing is chosen here) ==")
    rows = {k: stats(p, en) for k, (p, en) in fixed_windows(yr).items()}
    print(pd.DataFrame(rows).T.to_string(), "\n")
    g = yr.utc_grid()
    hourly = (g.diff(axis=1).iloc[:, 1:] / g.iloc[:, :-1].values * 1e4).mean().round(2)
    print(f"average move by UTC hour in {TRAIN}, bp (column h = move from h-1:00 to h:00)\n", hourly.to_frame("bp").T.to_string(), "\n")
    res = {}
    for s, e, side in candidates():
        p, en = window_pnl(g, s, e, side)
        res[(s, e, side)] = stats(p, en)
    t = pd.DataFrame(res).T
    t.index.names = ["start", "end", "side"]

    def positive(s, e, side):
        r = res.get((s, e, side))
        return r is not None and r.get("avg $", -1) > 0

    def neighbours_ok(s, e, side):
        nb = [(s - 1, e), (s + 1, e), (s, e - 1), (s, e + 1)]
        nb = [(a, b) for a, b in nb if (a, b, side) in res]
        return len(nb) > 0 and all(positive(a, b, side) for a, b in nb)

    t["qualifies"] = [(r["avg $"] > 0 and r["halves+"] == 2 and r["months+"] >= 8 and neighbours_ok(*k))
                      for k, r in t.iterrows()]
    print(f"== Part B: {len(t)} candidate windows on {TRAIN}; the 15 best by t-statistic ==")
    print(t.sort_values("t", ascending=False).head(15).to_string(), "\n")
    q = t[t.qualifies]
    print(f"{len(q)} windows qualify" + (":" if len(q) else ""))
    if len(q):
        print(q.sort_values("t", ascending=False).head(10).to_string())
    pick = q.t.astype(float).idxmax() if len(q) else None
    sel = {"train_year": TRAIN, "part_b_pick": None if pick is None else {"start": int(pick[0]), "end": int(pick[1]), "side": int(pick[2])},
           "qualifying": int(len(q)), "candidates": int(len(t))}
    print("\nPart B pick:", name_of(*pick) if pick else "none (no window qualifies)")
    (OUT / "selection.json").write_text(json.dumps(sel, indent=2))
    t.to_csv(OUT / "train_windows.csv")


def test():
    pd.set_option("display.width", 240); pd.set_option("display.max_rows", 400)
    sel = json.loads((OUT / "selection.json").read_text())
    pick = sel["part_b_pick"]
    daily, entries, bench, bench_a2 = {}, {}, {}, {}
    for y in sorted(YEAR_FILES):
        yr = Year(y)
        for k, (p, en) in fixed_windows(yr).items():
            daily.setdefault(k, []).append(p); entries.setdefault(k, []).append(en)
        g = yr.utc_grid()
        if pick:
            k = name_of(pick["start"], pick["end"], pick["side"])
            p, en = window_pnl(g, pick["start"], pick["end"], pick["side"])
            daily.setdefault(k, []).append(p); entries.setdefault(k, []).append(en)
            L = pick["end"] - pick["start"]
            same = [window_pnl(g, s, s + L, pick["side"])[0].sum() for s in range(0, 22 - L)]
            bench[(k, y)] = float(np.mean(same))
        others = [yr.long_from_london_hour(hh, int(19.5 * H)) for hh in range(24)]
        bench_a2[y] = float(np.mean([o.mean() for o in others if len(o) >= 100]))
    out = {}
    for k in daily:
        p = pd.concat(daily[k]).dropna(); en = pd.concat(entries[k])
        by = p.groupby(p.index.year)
        tab = pd.DataFrame({"n": by.size(), "total $": by.sum().round(1), "avg $": by.mean().round(3),
                            "avg bp": (p / en.loc[p.index] * 1e4).groupby(p.index.year).mean().round(2),
                            "win%": (100 * by.apply(lambda x: (x > 0).mean())).round(1)})
        if pick and k.startswith("B"):
            tab["same-length average window $"] = pd.Series({y: round(v, 1) for (kk, y), v in bench.items() if kk == k})
        if k.startswith("A2"):
            tab["avg $ of 19.5 h longs from any hour"] = pd.Series({y: round(v, 3) for y, v in bench_a2.items()})
        print(f"== {k} ==  ({TRAIN} = training year)\n", tab.T.to_string())
        for label, yrs in (("TEST 2023-2026", TEST), ("older 2015-2021", OLDER)):
            x = p[p.index.year.isin(yrs)]
            bp = x / en.loc[x.index] * 1e4
            t = bp.mean() / (bp.std(ddof=1) / np.sqrt(len(bp)))
            pos = int((x.groupby(x.index.year).sum() > 0).sum())
            line = f"  {label}: total ${x.sum():+.1f}, {pos} of {len(yrs)} years positive, avg {bp.mean():+.2f} bp, t {t:+.2f}, n {len(x)}"
            if label.startswith("TEST"):
                ok = x.sum() > 0 and pos >= 3 and t >= 2
                if pick and k.startswith("B") and pick["side"] > 0:
                    beat = sum(x[x.index.year == y].sum() > bench[(k, y)] for y in TEST)
                    ok = ok and beat >= 3
                    line += f", beats the same-length average in {beat} of 4 years"
                if k.startswith("A2"):
                    beat = sum(x[x.index.year == y].mean() > bench_a2[y] for y in TEST)
                    edge = np.mean([x[x.index.year == y].mean() - bench_a2[y] for y in TEST])
                    ok = ok and beat >= 3
                    line += f", beats the same-length average in {beat} of 4 years (by ${edge:+.3f} a trade)"
                line += f"  -> {'PASS' if ok else 'FAIL'}"
                out[k] = {"pass": bool(ok), "total": round(float(x.sum()), 1), "years_positive": pos, "t": round(float(t), 2)}
            print(line)
        if k.startswith("A2"):
            for sw in SWAPS[1:]:
                x = p[p.index.year.isin(TEST)] - 1.5 * sw
                print(f"  TEST with swap ${sw:.2f}/oz per night: total ${x.sum():+.1f}, "
                      f"{int((x.groupby(x.index.year).sum() > 0).sum())} of 4 years positive")
        print()
    (OUT / "test.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    {"train": train, "test": test}[sys.argv[1]]()
