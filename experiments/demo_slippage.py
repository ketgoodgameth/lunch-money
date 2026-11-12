"""Slippage and delay measured by the EA on a demo (or real) account, against what the backtest assumes.

    .venv/bin/python demo_slippage.py [PATH_TO_LOG]

Default log: the newest ReactionZoneEA_<login>.csv in the MT5 data folder (MQL5/Files). The EA (v1.10+) writes a
"fill" row for every entry and exit:
    entry BUY wanted 4000.120 got 4000.150 slip 0.030 send_ms 85 spread 0.200
    exit close|tp|sl|broker_stopout wanted ... got ... slip ... send_ms ... spread ...
"wanted" is the price on screen when the order was sent (the TP / stop price for a TP / stop-loss exit, v1.20);
slip > 0 means a worse fill,
in $/oz (= $ per trade at 0.01 lots). send_ms is how long the order took.
The backtest assumes 100 ms and no slippage; results/execution_costs/summary.csv has the same strategy re-run with
$0.05 / $0.10 / $0.30 slippage on market entries and market exits, which this script uses to translate the measured
slippage into backtest profit.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MT5_FILES = (Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files"
             / "MetaTrader 5/MQL5/Files")
FILL = re.compile(r"(entry|exit) (\S+) wanted ([\d.]+) got ([\d.]+) slip (-?[\d.]+) send_ms (-?[\d.]+) spread (-?[\d.]+)")


def parse(text: str) -> pd.DataFrame:
    rows = []
    for line in text.splitlines():
        parts = line.split(",", 2)
        if len(parts) < 3 or parts[1].strip('"') != "fill":
            continue
        m = FILL.search(parts[2])
        if not m:
            continue
        leg, what, want, got, slip, ms, spread = m.groups()
        rows.append({"time": parts[0].strip('"'), "leg": "entry" if leg == "entry" else f"exit: {what}",
                     "side": what if leg == "entry" else "", "wanted": float(want), "got": float(got),
                     "slip": float(slip), "send_ms": float(ms), "spread": float(spread)})
    return pd.DataFrame(rows)


def backtest_net(slip: float, version: str = "TP $10, intraday") -> float:
    """Backtest 2019-2026 net for this average slippage, read off the slippage scenarios (straight lines between)."""
    s = pd.read_csv(ROOT / "results" / "execution_costs" / "summary.csv")
    s = s[s.version == version].set_index("scenario").net
    pts = [(0.0, s["baseline: 100 ms, no slippage"]), (0.05, s["VPS, $0.05 slippage"]),
           (0.10, s["VPS, $0.10 slippage"]), (0.30, s["bad fills, $0.30 slippage"])]
    return float(np.interp(max(slip, 0.0), [p[0] for p in pts], [p[1] for p in pts]))


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        logs = sorted(MT5_FILES.glob("ReactionZoneEA_*.csv"), key=lambda p: p.stat().st_mtime)
        if not logs:
            raise SystemExit(f"no EA log in {MT5_FILES} yet: attach the EA to a demo chart and let it trade")
        path = logs[-1]
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    f = parse(text)
    if f.empty:
        raise SystemExit(f"{path.name}: no fill rows yet (needs EA v1.10 and at least one trade)")
    print(f"{path.name}: {len(f)} fills, {f.time.min()} to {f.time.max()}\n")
    g = f.groupby("leg")
    t = pd.DataFrame({"fills": g.size(), "avg slip $/oz": g.slip.mean().round(3), "median": g.slip.median().round(3),
                      "worst": g.slip.max().round(3), "share worse than wanted": (100 * g.slip.apply(lambda x: (x > 0.0005).mean())).round(0),
                      "median delay ms": g.send_ms.median().round(0), "slowest ms": g.send_ms.max().round(0),
                      "avg spread": g.spread.mean().round(3)})
    print(t.to_string(), "\n")
    market = f[f.leg != "exit: tp"]
    avg = market.slip.mean()
    print(f"market fills (entries and closes by the EA): average slippage ${avg:.3f}/oz over {len(market)} fills; "
          f"entries' spread ${f[f.leg == 'entry'].spread.mean():.3f} (backtest: $0.20)")
    print(f"backtest 2019-2026 at that slippage: about ${backtest_net(avg):+,.0f} (no slippage: ${backtest_net(0):+,.0f})")
    if len(market) < 30:
        print(f"only {len(market)} market fills so far: treat this as a first look, not a measurement (aim for 30+)")


if __name__ == "__main__":
    main()
