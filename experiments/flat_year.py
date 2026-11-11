"""Build on a flat year (2022: gold -0%), check on 2023-2025. Rules: one $20 trade a day, intraday.

    .venv/bin/python flat_year.py dev | check | neighbours

Selection rule (fixed before running): best net at Standard $0.20 on 2022 with >= 100 trades, whose longs
AND shorts are both profitable before costs (zero-cost run) -- so it can't just be riding one direction.
"""
import json
import sys

import pandas as pd

from common import ROOT
from intraday_20 import ORB, TP, run, table

IDEAS = {
    "pullback 15m, floor 2xATR": ("trend_pullback.py", {**TP, "tf": "15min", "lookback": 8, "min_stop_atr": 2.0}),
    "pullback 15m, floor 1.5xATR": ("trend_pullback.py", {**TP, "tf": "15min", "lookback": 8, "min_stop_atr": 1.5}),
    "pullback 1h, lookback 8": ("trend_pullback.py", {**TP, "tf": "1h", "lookback": 8, "min_stop_atr": 1.0}),
    "pullback 1h, lookback 12": ("trend_pullback.py", {**TP, "tf": "1h", "lookback": 12, "min_stop_atr": 1.0}),
    "reversal 15m, no trend": ("trend_pullback.py", {**TP, "tf": "15min", "lookback": 8, "min_stop_atr": 2.0, "trend": "none"}),
    "reversal 1h, no trend": ("trend_pullback.py", {**TP, "tf": "1h", "lookback": 8, "min_stop_atr": 1.0, "trend": "none"}),
    "London breakout, 60m range": ("opening_range.py", {**ORB, "range_start": "07:00", "range_minutes": 60}),
    "London breakout, 30m range": ("opening_range.py", {**ORB, "range_start": "07:00", "range_minutes": 30}),
    "New York breakout, 60m range": ("opening_range.py", {**ORB, "range_start": "13:30", "range_minutes": 60}),
    "New York breakout, 30m range": ("opening_range.py", {**ORB, "range_start": "13:30", "range_minutes": 30}),
}
DEV, CHECK = 2022, (2023, 2024, 2025)
OUT = ROOT / "results" / "flat_year"


def show(df):
    table(df)
    print("\n== longs vs shorts ==")
    print(df[["year", "variant", "costs", "long_trades", "trades", "long_net", "short_net"]]
          .sort_values(["costs", "year", "variant"]).to_string(index=False))


def dev():
    df = run(IDEAS, DEV)
    df.to_csv(OUT / f"dev_{DEV}.csv", index=False)
    show(df)
    zero = df[df.costs == "zero cost"].set_index("variant")
    std = df[df.costs == "Standard $0.20"].set_index("variant")
    ok = std[(std.trades >= 100) & (zero.long_net > 0) & (zero.short_net > 0)].sort_values("net", ascending=False)
    print("\nvariants passing the both-directions screen:", list(ok.index) or "none")
    if ok.empty:
        return
    pick = ok.index[0]
    (OUT / "selected.json").write_text(json.dumps({"name": pick, "file": IDEAS[pick][0], "params": IDEAS[pick][1]}, indent=2))
    print(f"selected by the pre-declared rule: {pick}")


def selected():
    sel = json.loads((OUT / "selected.json").read_text())
    return sel["name"], sel["file"], sel["params"]


def check():
    name, f, params = selected()
    df = pd.concat([run({name: (f, params)}, y) for y in CHECK])
    df.to_csv(OUT / "check.csv", index=False)
    show(df)


def neighbours():
    name, f, params = selected()
    if f == "opening_range.py":
        rm = params["range_minutes"]
        tweaks = {f"range {rm // 2} min": {"range_minutes": rm // 2}, f"range {rm + 30} min": {"range_minutes": rm + 30},
                  "trail 0.75R": {"trail_dist_r": 0.75}, "trail 0.35R": {"trail_dist_r": 0.35}}
    else:
        lb, fl = params["lookback"], params["min_stop_atr"]
        tweaks = {f"lookback {lb - 2}": {"lookback": lb - 2}, f"lookback {lb + 4}": {"lookback": lb + 4},
                  f"floor {fl - 0.5:g}xATR": {"min_stop_atr": fl - 0.5}, f"floor {fl + 0.5:g}xATR": {"min_stop_atr": fl + 0.5}}
    ideas = {f"{name} | {k}": (f, {**params, **v}) for k, v in tweaks.items()}
    df = pd.concat([run(ideas, y) for y in (DEV, *CHECK)])
    df.to_csv(OUT / "neighbours.csv", index=False)
    std = df[df.costs == "Standard $0.20"]
    print(std.pivot(index="variant", columns="year", values="net").assign(total=lambda x: x.sum(axis=1)).round(2).to_string())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"dev": dev, "check": check, "neighbours": neighbours}[sys.argv[1]]()
