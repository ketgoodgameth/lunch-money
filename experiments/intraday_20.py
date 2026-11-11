"""New rules: ONE trade a day risking $20 (daily loss still capped at -$20), intraday only (flat 20:00 UTC).

    .venv/bin/python intraday_20.py dev           # 4 pre-declared ideas on 2025, picks one
    .venv/bin/python intraday_20.py check         # the pick on 2022, 2023, 2024
    .venv/bin/python intraday_20.py neighbours    # the pick with nearby settings on all years

Selection rule (fixed before running): best net at Standard $0.20 on 2025 with >= 100 trades.
"""
import json
import sys

import pandas as pd

from common import COSTS, ROOT, run_grid

RULES20 = {"risk_per_trade": 20.0, "max_daily_loss": 20.0, "max_trades": 1, "stop_after_wins": 1}
TRAIL = {"trail_start_r": 1.0, "trail_dist_r": 0.5}
DAY = {"__rules__": RULES20}
TP = {**TRAIL, **DAY, "buffer_atr": 0.2, "max_stop": 20.0, "entry_hours": "7-18", "flat_hour": 20}
ORB = {**TRAIL, **DAY, "exit": "trail", "range_minutes": 60, "max_stop": 20.0, "entry_end_hour": 18, "flat_hour": 20}
IDEAS = {
    "pullback 1h candles": ("trend_pullback.py", {**TP, "tf": "1h", "lookback": 8, "min_stop_atr": 1.0}),
    "pullback 15m candles": ("trend_pullback.py", {**TP, "tf": "15min", "lookback": 8, "min_stop_atr": 2.0}),
    "London open breakout": ("opening_range.py", {**ORB, "range_start": "07:00"}),
    "New York open breakout": ("opening_range.py", {**ORB, "range_start": "13:30"}),
}
COST2 = {k: COSTS[k] for k in ("zero cost", "Standard $0.20")}
OUT = ROOT / "results" / "intraday_20"


def run(ideas: dict, year: int) -> pd.DataFrame:
    by_file = {}
    for name, (f, params) in ideas.items():
        by_file.setdefault(f, {})[name] = params
    return pd.concat([run_grid(f, v, year, costs=COST2) for f, v in by_file.items()])


def table(df):
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    cols = ["year", "variant", "costs", "trades", "net", "Q1", "Q2", "Q3", "Q4", "q_positive", "win_rate", "avg_R", "tstat", "max_dd"]
    print(df[[c for c in cols if c in df.columns]].sort_values(["costs", "year", "net"], ascending=[True, True, False]).to_string(index=False))


def dev():
    df = run(IDEAS, 2025)
    df.to_csv(OUT / "dev_2025.csv", index=False)
    table(df)
    std = df[(df.costs == "Standard $0.20") & (df.trades >= 100)].sort_values("net", ascending=False)
    if std.empty:
        print("no idea reached 100 trades")
        return
    pick = std.iloc[0].variant
    (OUT / "selected.json").write_text(json.dumps({"name": pick, "file": IDEAS[pick][0], "params": IDEAS[pick][1]}, indent=2))
    print(f"\nselected by the pre-declared rule: {pick}")


def selected():
    sel = json.loads((OUT / "selected.json").read_text())
    return sel["name"], sel["file"], sel["params"]


def check():
    name, f, params = selected()
    df = pd.concat([run({name: (f, params)}, y) for y in (2022, 2023, 2024)])
    df.to_csv(OUT / "check.csv", index=False)
    table(df)


def neighbours():
    name, f, params = selected()
    if f == "opening_range.py":
        tweaks = {"range 30 min": {"range_minutes": 30}, "range 90 min": {"range_minutes": 90},
                  "trail 0.75R": {"trail_dist_r": 0.75}, "trail 0.35R": {"trail_dist_r": 0.35}}
    else:
        tweaks = {"lookback 6": {"lookback": 6}, "lookback 12": {"lookback": 12},
                  "floor -0.5 ATR": {"min_stop_atr": params["min_stop_atr"] - 0.5},
                  "floor +0.5 ATR": {"min_stop_atr": params["min_stop_atr"] + 0.5}}
    ideas = {f"{name} | {k}": (f, {**params, **v}) for k, v in tweaks.items()}
    df = pd.concat([run(ideas, y) for y in (2022, 2023, 2024, 2025)])
    df.to_csv(OUT / "neighbours.csv", index=False)
    std = df[df.costs == "Standard $0.20"]
    print(std.pivot(index="variant", columns="year", values="net").assign(total=lambda x: x.sum(axis=1)).round(2).to_string())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"dev": dev, "check": check, "neighbours": neighbours}[sys.argv[1]]()
