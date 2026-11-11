"""Monthly orders chart (profitable vs stopped out) from export_rr_trades.py output.

    .venv/bin/python build_orders_chart.py    ->  results/charts/backtest_orders.html
"""
import json

import pandas as pd

from common import ROOT

if __name__ == "__main__":
    src = ROOT / "results" / "rr_framework" / "trades"
    data = {}
    for rr in (3, 5):
        df = pd.read_csv(src / f"trades_1to{rr}.csv")
        month = pd.to_datetime(df.entry_time, utc=True, format="ISO8601").dt.strftime("%Y-%m")
        g = df.assign(month=month, win=df.net_pnl > 0).groupby("month")
        agg = pd.DataFrame({"n": g.size(), "w": g.win.sum(), "net": g.net_pnl.sum().round(2)})
        data[f"1:{rr}"] = {m: [int(r.n), int(r.w), float(r.net)] for m, r in agg.iterrows()}
    html = (ROOT / "experiments" / "chart_template.html").read_text().replace("/*DATA*/null", json.dumps(data))
    out = ROOT / "results" / "charts"
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest_orders.html").write_text(html)
    for k, d in data.items():
        print(k, sum(v[0] for v in d.values()), "orders,", sum(v[1] for v in d.values()), "profitable, net",
              round(sum(v[2] for v in d.values()), 2))
