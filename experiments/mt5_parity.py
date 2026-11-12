"""Does the MT5 Expert Advisor trade like the Python strategy? Same broker ticks, same month.

    .venv/bin/python mt5_parity.py python    # Python on the MT5 tick export (MetaQuotes-Demo, Oct 2025)
    .venv/bin/python mt5_parity.py tester    # read the EA log from the Strategy Tester run
    .venv/bin/python mt5_parity.py compare   # match the two trade lists
    .venv/bin/python mt5_parity.py growth    # EA v1.20 ($20 stop, risk % of balance) tester run vs the Python list

Python side: the broker's own bid/ask (no re-quote), no delay, no slippage, $20 daily account, 0.01 lots,
max 2 a day, stop after a win; version TP $10 closed at 20:00 UTC. Indicators start cold on Oct 1, while the
EA warms up from earlier history, so trades in the first day may differ.
"""
import sys

import pandas as pd

from common import ROOT, cache_for  # noqa: F401  (sets sys.path for xautest)

TICKS = ROOT / "ticks" / "mt5" / "XAUUSD_202510012300_202510312256.csv"
OUT = ROOT / "results" / "mt5_parity"
VERSION = {"tp_usd": 10.0, "flat_hour": 20}


def python_side():
    import json
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.csvfeed import CSVTickFeed
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    params = {k: v for k, v in base.items() if k != "__rules__"} | VERSION
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**params)
    s.risk = DailyRiskRules(fixed_lots=0.01, max_daily_loss=20.0, max_trades=2, stop_after_wins=1)
    broker = SimBroker(BrokerConfig(initial_balance=10_000, latency_ms=0, leverage=float("inf"), daily_account=20.0))
    res = Engine(s, broker, CSVTickFeed(TICKS, "mt5", None, server_tz="Europe/Athens")).run()
    df = trades_frame(res)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "python_trades.csv", index=False)
    print(df[["side", "entry_time", "entry_price", "exit_time", "exit_price", "net_pnl", "exit_type"]].to_string())
    print(f"\n{len(df)} trades, {100 * (df.net_pnl > 0).mean():.0f}% won, net {df.net_pnl.sum():+.2f}; stats {s.stats}")


def tester_side(out_name: str = "tester_trades.csv"):
    """Read the EA's own log from the Strategy Tester agent folder (newest one) into tester_trades.csv."""
    from pathlib import Path
    mt5 = Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5"
    logs = sorted((mt5 / "Tester").glob("Agent-*/MQL5/Files/ReactionZoneEA_*.csv"), key=lambda p: p.stat().st_mtime)
    if not logs:
        sys.exit("no tester log found under MetaTrader 5/Tester/Agent-*/MQL5/Files")
    lines = logs[-1].read_text(errors="replace").splitlines()[1:]      # details contain commas: split twice only
    ev = pd.DataFrame([(ln.split(",", 2) + ["", ""])[:3] for ln in lines if ln.strip()],
                      columns=["server_time", "event", "detail"])
    clock = ev[ev.event == "server clock"].detail.iloc[0] if (ev.event == "server clock").any() else ""
    tz = "UTC" if "UTC+0.0" in clock else "Europe/Athens"          # Exness: UTC; MetaQuotes-Demo: Athens
    ev["utc"] = (pd.to_datetime(ev.server_time, format="%Y.%m.%d %H:%M:%S")
                 .dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC"))
    rows, cur = [], None
    for _, e in ev.iterrows():
        if e.event == "entry":
            cur = {"entry_utc": e.utc, "side": e.detail.split()[0], "entry_detail": e.detail}
        elif e.event == "exit" and cur is not None:
            cur.update(exit_utc=e.utc, exit_detail=e.detail, net=float(e.detail.rsplit("net", 1)[1]))
            rows.append(cur); cur = None
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / out_name, index=False)
    print(f"read {logs[-1]}\n{ev.event.value_counts().to_dict()}")
    if out_name == "tester_trades.csv":
        print(df.to_string() if len(df) else "no trades")
    return df, ev


def compare():
    ref = sys.argv[2] if len(sys.argv) > 2 else "mt5"
    if ref == "dukascopy":     # the main backtest (Dukascopy prices, $0.20 spread), same month and version
        py = pd.read_csv(ROOT / "results" / "high_winrate" / "trades.csv")
        py = py[(py.variant == "TP $10, intraday") & py.entry_time.str.startswith("2025-10")].reset_index(drop=True)
    else:
        py = pd.read_csv(OUT / "python_trades.csv")
    py["entry"] = pd.to_datetime(py.entry_time, utc=True, format="ISO8601")
    mt = pd.read_csv(OUT / "tester_trades.csv")
    mt["entry"] = pd.to_datetime(mt.entry_utc, utc=True)
    rows, used = [], set()
    for _, p in py.iterrows():
        d = (mt.entry - p.entry).abs()
        j = d.idxmin() if len(mt) else None
        ok = j is not None and d[j] <= pd.Timedelta(minutes=1) and mt.side[j] == p.side and j not in used
        if ok:
            used.add(j)
        rows.append({"python_entry": p.entry, "side": p.side, "python_net": p.net_pnl, "python_exit": p.exit_type,
                     "tester_entry": mt.entry[j] if ok else None, "tester_net": mt.net[j] if ok else None})
    for j in set(mt.index) - used:
        rows.append({"python_entry": None, "side": mt.side[j], "python_net": None, "python_exit": None,
                     "tester_entry": mt.entry[j], "tester_net": mt.net[j]})
    r = pd.DataFrame(rows).sort_values(by=["python_entry", "tester_entry"], na_position="last")
    pd.set_option("display.width", 200)
    print(r.to_string(index=False))
    both = r.dropna(subset=["python_entry", "tester_entry"])
    print(f"\nmatched {len(both)} of {len(py)} Python trades and {len(mt)} tester trades; "
          f"net Python {py.net_pnl.sum():+.2f}, tester {mt.net.sum():+.2f}")
    r.to_csv(OUT / "comparison.csv", index=False)


def growth():
    """EA v1.20 in the tester (2026, $1,000, $20 stop, risk 3%) against the Python $20-stop list for 2026
    (Dukascopy, $0.20 spread; results/portfolio_growth). Checks the stop, the sizing and the trade match."""
    import re
    mt, ev = tester_side("tester_v120_2026.csv")
    start = ev[ev.event == "start"].detail.iloc[-1]
    print("EA start line:", start)
    mt["entry"] = pd.to_datetime(mt.entry_utc, utc=True)
    mt["lots"] = mt.entry_detail.str.extract(r"([\d.]+) lots").astype(float)
    mt["price"] = mt.entry_detail.str.extract(r"lots at ([\d.]+)").astype(float)
    mt["sl"] = mt.entry_detail.str.extract(r"SL ([\d.]+)").astype(float)
    mt["balance"] = mt.entry_detail.str.extract(r"balance ([\d.]+)").astype(float)
    mt["kind"] = mt.exit_detail.str.extract(r"^(.*?) at ")
    mt["per_oz"] = mt.net / (mt.lots * 100)
    stop = (mt.sl - mt.price).abs()
    want = (mt.balance * 0.03 / (20 * 100) / 0.01 + 1e-9).astype(int).clip(lower=1) * 0.01
    print(f"\n{len(mt)} tester trades, exits {mt.kind.value_counts().to_dict()}")
    print(f"stop distance from the fill: min {stop.min():.2f}, median {stop.median():.2f}, max {stop.max():.2f} (want 20 +- slippage/spread)")
    sl = mt[mt.kind == "stop loss"]
    print(f"stop-loss exits: {len(sl)}, result per oz min {sl.per_oz.min():.2f}, median {sl.per_oz.median():.2f}, max {sl.per_oz.max():.2f}; "
          f"worst trade of all {mt.per_oz.min():.2f} $/oz")
    print(f"lots follow 3% of balance in {(abs(mt.lots - want) < 1e-9).sum()} of {len(mt)} trades; sizes used {mt.lots.value_counts().sort_index().to_dict()}")
    print(f"balance $1,000 -> ${mt.balance.iloc[-1] + mt.net.iloc[-1]:,.2f}; net {mt.net.sum():+.2f}; at 0.01 lots it would be {mt.per_oz.sum():+.2f}; "
          f"win rate {100 * (mt.net > 0).mean():.0f}%")
    py = pd.read_csv(ROOT / "results" / "portfolio_growth" / "trades.csv.gz")
    py = py[(py.stop == "stop $20") & (py.year == 2026)].reset_index(drop=True)
    py["entry"] = pd.to_datetime(py.entry_time, utc=True, format="ISO8601")
    used, pairs = set(), []
    for i, p in py.iterrows():
        d = (mt.entry - p.entry).abs()
        j = d.idxmin()
        if d[j] <= pd.Timedelta(minutes=1) and mt.side[j] == p.side and j not in used:
            used.add(j); pairs.append((i, j))
    a = py.loc[[i for i, _ in pairs]].net_pnl.to_numpy(); b = mt.loc[[j for _, j in pairs]].per_oz.to_numpy()
    same = ((a > 0) == (b > 0)).sum()
    print(f"\nPython 2026 list (Dukascopy): {len(py)} trades, {py.net_pnl.sum():+.2f} $/oz, win rate {100 * (py.net_pnl > 0).mean():.0f}%")
    print(f"matched to the minute and side: {len(pairs)}; same win/loss in {same}; Python {a.sum():+.2f} vs tester {b.sum():+.2f} $/oz on those")
    only_py = py.drop(index=[i for i, _ in pairs]); only_mt = mt.drop(index=[j for _, j in pairs])
    print(f"Python only: {len(only_py)} trades {only_py.net_pnl.sum():+.2f}; tester only: {len(only_mt)} trades {only_mt.per_oz.sum():+.2f} $/oz")
    print("fill rows:", ev[ev.event == "fill"].detail.str.extract(r"^(entry|exit \S+)")[0].value_counts().to_dict())


if __name__ == "__main__":
    {"python": python_side, "tester": tester_side, "compare": compare, "growth": growth}[sys.argv[1]]()
