"""Monthly orders chart (profitable vs losing) for the saved versions.

    .venv/bin/python build_orders_chart.py    ->  results/charts/backtest_orders.html
Sources: results/high_winrate/trades.csv (high_winrate.py) and results/rr_framework/trades/ (export_rr_trades.py).
"""
import json

import pandas as pd

from common import ROOT

HW = "Small target, no stop loss, $20/day account, max 2 orders a day, stop after a win."
VERSIONS = {   # button label: (source, variant, description, rule)
    "$7.5 held": ("hw", "TP $7.5, held", "High win rate: +$7.5 target, held until the target or the $20 account stop-out.",
                  "Wins about +$7.7, losses about −$20, so it needs a win rate above about 72% to profit. " + HW),
    "$7.5 intraday": ("hw", "TP $7.5, intraday", "High win rate: +$7.5 target, anything open is closed at 20:00 UTC.",
                      "Wins about +$6.7, losses about −$10, so it needs a win rate above about 60%. " + HW),
    "$10 intraday": ("hw", "TP $10, intraday", "High win rate: +$10 target, anything open is closed at 20:00 UTC.",
                     "Wins about +$8.2, losses about −$10, so it needs a win rate above about 55%. " + HW),
    "1:3": ("rr", 3, "Risk:reward 1:3: the $10 account is the stop, +$30 target, held until target or stop-out.",
            "Each order risks $10 for +$30; break-even win rate 25%."),
    "1:5": ("rr", 5, "Risk:reward 1:5: the $10 account is the stop, +$50 target, held until target or stop-out.",
            "Each order risks $10 for +$50; break-even win rate 16.7%."),
}


def monthly(df):
    month = pd.to_datetime(df.entry_time, utc=True, format="ISO8601").dt.strftime("%Y-%m")
    g = df.assign(month=month, win=df.net_pnl > 0).groupby("month")
    agg = pd.DataFrame({"n": g.size(), "w": g.win.sum(), "net": g.net_pnl.sum().round(2)})
    return {m: [int(r.n), int(r.w), float(r.net)] for m, r in agg.iterrows()}


if __name__ == "__main__":
    hw = pd.read_csv(ROOT / "results" / "high_winrate" / "trades.csv")
    data, info = {}, {}
    for label, (src, key, desc, rule) in VERSIONS.items():
        df = hw[hw.variant == key] if src == "hw" else \
            pd.read_csv(ROOT / "results" / "rr_framework" / "trades" / f"trades_1to{key}.csv")
        w, l = df.net_pnl[df.net_pnl > 0].mean(), df.net_pnl[df.net_pnl <= 0].mean()
        data[label], info[label] = monthly(df), [desc, rule, round(100 * -l / (w - l), 1)]   # break-even win rate
    html = (ROOT / "experiments" / "chart_template.html").read_text() \
        .replace("/*DATA*/null", json.dumps(data)).replace("/*INFO*/null", json.dumps(info))
    out = ROOT / "results" / "charts"
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest_orders.html").write_text(html)
    for k, d in data.items():
        n, w = sum(v[0] for v in d.values()), sum(v[1] for v in d.values())
        print(f"{k}: {n} orders, {w} profitable ({100 * w / n:.1f}%), net {sum(v[2] for v in d.values()):.2f}")
