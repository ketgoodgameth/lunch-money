"""Replay a real trade history on Dukascopy ticks with a stop-loss cap and/or a maximum hold time.

    .venv/bin/python trade_history_replay.py PATH_TO_CSV [--cost 0.5]   # --cost: charge this $/oz instead

Every XAU trade keeps its real entry (time, side, lots, price). The price path after entry is the Dukascopy mid
price move from the entry tick (the times line up to the second: move correlation 0.99 at 0 s offset), minus your
own round-trip cost, measured from the history as the median gap between your real profit and the Dukascopy mid
move (Dukascopy's own 2026 spread, ~$0.67/oz, is far wider than Exness and would wreck short trades). A trade closes at the first of:
  - the stop cap: the tick where its loss reaches -$cap (filled at that tick, so a gap can fill a bit worse),
  - the max hold: the first tick at or after entry + N minutes,
  - its real close, with its real profit (your own exit, TP or stop-out).
Caps are per position at the position's own lot size. Trades with no tick data (after the tick files end) are left
out of every version, so all versions cover the same trades.
Writes results/trade_history_replay/ (git-ignored: it is your account data).
"""
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
from common import cache_for, year_file                     # noqa: E402
from xautest.feeds.arrays import load_cache                 # noqa: E402

OUT = ROOT / "results" / "trade_history_replay"
CAPS = (None, 5.0, 10.0, 20.0)
HOLDS = (None, 1, 2, 3)          # minutes
CONTRACT = 100.0


def load(path):
    d = pd.read_csv(path)
    d = d[d.symbol.str.upper().str.startswith("XAU")].copy()
    d["open_ms"] = pd.to_datetime(d.opening_time_utc, utc=True).dt.as_unit("ms").astype("int64")
    d["close_ms"] = pd.to_datetime(d.closing_time_utc, utc=True).dt.as_unit("ms").astype("int64")
    d["side"] = np.where(d.type.str.lower() == "buy", 1, -1)
    return d.sort_values("open_ms").reset_index(drop=True)


def paths(d, cost=None):
    """Per trade: tick times and the trade's $ P&L on each tick: mid move from entry minus `cost` $/oz.
    With cost=None, returns (paths, measured cost)."""
    out, ticks = {}, {}
    for i, r in d.iterrows():
        y = pd.Timestamp(r.open_ms, unit="ms").year
        if y not in ticks:
            try:
                ticks[y] = load_cache(cache_for(year_file(y)))
            except FileNotFoundError:
                ticks[y] = None
        a = ticks[y]
        if a is None:
            continue
        ts = a["ts"]
        if r.close_ms > ts[-1]:
            continue
        k0 = int(np.searchsorted(ts, r.open_ms, "right")) - 1
        k1 = int(np.searchsorted(ts, r.close_ms, "right"))
        if k0 < 0 or k1 <= k0:
            continue
        mid = (np.asarray(a["bid"][k0:k1], float) + np.asarray(a["ask"][k0:k1], float)) / 2
        out[i] = (np.asarray(ts[k0:k1]), r.side * (mid - mid[0]))           # $/oz move in the trade's favour
    if cost is None:
        gaps = [d.profit[i] / (d.lots[i] * CONTRACT) - m[-1] for i, (_, m) in out.items() if pd.notna(d.profit[i])]
        cost = -float(np.median(gaps))
    return {i: (t, (m - cost) * d.lots[i] * CONTRACT) for i, (t, m) in out.items()}, cost


def replay(d, p, cap, hold):
    res = []
    for i, r in d.iterrows():
        if i not in p:
            continue
        t, pnl = p[i]
        stop = np.flatnonzero(pnl <= -cap) if cap else np.array([], int)
        late = np.flatnonzero(t >= r.open_ms + hold * 60_000) if hold else np.array([], int)
        cands = [(t[x[0]], pnl[x[0]], why) for x, why in ((stop, "stop cap"), (late, "max hold")) if len(x)]
        cands = [c for c in cands if c[0] < r.close_ms]
        if cands:
            ct, pl, why = min(cands, key=lambda c: c[0])
        else:
            ct, pl, why = r.close_ms, r.profit, "real close"
        res.append((i, r.opening_time_utc[:10], ct, round(float(pl), 2), why))
    return pd.DataFrame(res, columns=["i", "day", "close_ms", "pnl", "exit"])


def main(path):
    OUT.mkdir(parents=True, exist_ok=True)
    d = load(path)
    p, cost = paths(d) if COST is None else paths(d, COST)
    cov = d.loc[list(p)]
    print(f"{len(d)} XAU trades, {len(p)} with tick data "
          f"({cov.opening_time_utc.min()} to {cov.opening_time_utc.max()}); "
          f"left out: {len(d) - len(p)} trades, real net {d.drop(index=list(p)).profit.sum():+.2f}")
    ok = [i for i in p if pd.notna(d.profit[i])]
    err = np.array([p[i][1][-1] - d.profit[i] for i in ok]) / d.lots[ok].values / CONTRACT
    print(f"round-trip cost charged: ${cost:.3f}/oz; replayed real closes vs your real profit: median gap "
          f"${np.median(np.abs(err)):.2f}/oz, 90% within ${np.quantile(np.abs(err), .9):.2f}/oz")
    rows, allt = [], []
    for cap, hold in product(CAPS, HOLDS):
        name = f"stop {'-$' + format(cap, 'g') if cap else 'none'} | hold {str(hold) + ' min' if hold else 'none'}"
        r = replay(d, p, cap, hold)
        r.insert(0, "version", name)
        allt.append(r)
        day = r.groupby("day").pnl.sum()
        w, l = r.pnl[r.pnl > 0], r.pnl[r.pnl <= 0]
        rows.append({"version": name, "trades": len(r), "win_rate": round(100 * (r.pnl > 0).mean(), 1),
                     "net": round(r.pnl.sum(), 2), "avg_win": round(w.mean(), 2), "avg_loss": round(l.mean(), 2),
                     "worst_trade": r.pnl.min(), "worst_day": round(day.min(), 2),
                     "days_below_-20": int((day <= -20).sum()), "losing_days": f"{(day < 0).mean():.0%}",
                     "stop_cap_exits": int((r.exit == "stop cap").sum()),
                     "max_hold_exits": int((r.exit == "max hold").sum())})
    s = pd.DataFrame(rows).set_index("version")
    pd.set_option("display.width", 250)
    print(s.to_string())
    s.to_csv(OUT / f"summary{SUFFIX}.csv")
    pd.concat(allt).merge(d[["ticket", "opening_time_utc", "type", "lots", "opening_price", "profit"]],
                          left_on="i", right_index=True).drop(columns="i").to_csv(OUT / f"trades{SUFFIX}.csv", index=False)


COST = float(sys.argv[sys.argv.index("--cost") + 1]) if "--cost" in sys.argv else None   # $/oz override
SUFFIX = f"_cost{COST:g}" if COST is not None else ""

if __name__ == "__main__":
    main(sys.argv[1])
