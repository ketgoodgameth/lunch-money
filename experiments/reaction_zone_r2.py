"""Reaction-zone round 2: relax the conditions that blocked almost every setup in round 1.

    .venv/bin/python reaction_zone_r2.py dev | check | neighbours

Fixed: sweeps + FVG zones, London + New York sessions, 0.01 lots, stop after first win, max 2 trades,
$20/day account, flat 20:00 UTC. Selection rule (fixed before running): best net at $0.20 on 2022 with
>= 80 trades, longs and shorts both profitable before costs.
"""
import json
import sys

import pandas as pd

from common import ROOT
from flat_year import show
from intraday_20 import run

F = "reaction_zone.py"
RULES = {"__rules__": {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_wins": 1}}
NO_RSI = {"rsi_overbought": 0.0, "rsi_oversold": 100.0, "use_divergence": False}
BASE = {**RULES, **NO_RSI, "zones": "sweep+fvg", "sessions": "07:00-10:00,12:30-16:00", "flat_hour": 20,
        "tp_usd": 10.0, "sl_mode": "none", "h1_macd_filter": False, "exec_mode": "m1"}
IDEAS = {
    "1 no RSI, 1H MACD on": (F, {**BASE, "h1_macd_filter": True}),
    "2 no RSI, 1H MACD off": (F, BASE),
    "3 RSI 60/40 or divergence": (F, {**BASE, "rsi_overbought": 60.0, "rsi_oversold": 40.0, "use_divergence": True}),
    "4 = 2 + stop beyond wick": (F, {**BASE, "sl_mode": "wick"}),
    "5 = 2 + TP $20": (F, {**BASE, "tp_usd": 20.0}),
    "6 = 2 + enter on 5m confirmation": (F, {**BASE, "exec_mode": "m5"}),
}
OUT = ROOT / "results" / "reaction_zone_r2"


def dev():
    df = run(IDEAS, 2022)
    df.to_csv(OUT / "dev_2022.csv", index=False)
    show(df)
    zero = df[df.costs == "zero cost"].set_index("variant")
    std = df[df.costs == "Standard $0.20"].set_index("variant")
    ok = std[(std.trades >= 80) & (zero.long_net > 0) & (zero.short_net > 0)].sort_values("net", ascending=False)
    print("\npassing the screen:", list(ok.index) or "none")
    if not ok.empty:
        pick = ok.index[0]
        (OUT / "selected.json").write_text(json.dumps({"name": pick, "file": F, "params": IDEAS[pick][1]}, indent=2))
        print("selected:", pick)


def selected():
    path = OUT / "selected.json"
    return json.loads(path.read_text()) if path.exists() else None


def check():
    sel = selected()
    if not sel:
        print("nothing selected; no check"); return
    df = pd.concat([run({sel["name"]: (F, sel["params"])}, y) for y in (2023, 2024, 2025, 2026)])
    df.to_csv(OUT / "check.csv", index=False)
    show(df)


def neighbours():
    sel = selected()
    if not sel:
        print("nothing selected; no neighbours"); return
    p = sel["params"]
    tweaks = {"swing lookback 12": {"swing_lookback": 12}, "swing lookback 32": {"swing_lookback": 32},
              f"TP ${p['tp_usd'] * 0.75:g}": {"tp_usd": p["tp_usd"] * 0.75},
              f"TP ${p['tp_usd'] * 1.25:g}": {"tp_usd": p["tp_usd"] * 1.25},
              "wick >= 30%": {"min_wick": 0.30}, "wick >= 50%": {"min_wick": 0.50}}
    ideas = {f"{sel['name']} | {k}": (F, {**p, **v}) for k, v in tweaks.items()}
    df = pd.concat([run(ideas, y) for y in (2022, 2023, 2024, 2025, 2026)])
    df.to_csv(OUT / "neighbours.csv", index=False)
    std = df[df.costs == "Standard $0.20"]
    print(std.pivot(index="variant", columns="year", values="net").assign(total=lambda x: x.sum(axis=1)).round(2).to_string())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for step in sys.argv[1:]:
        print(f"\n######## {step} ########", flush=True)
        {"dev": dev, "check": check, "neighbours": neighbours}[step]()
