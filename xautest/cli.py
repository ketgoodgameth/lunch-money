"""Command line: backtest, live, fetch, synth, inspect.  Run ``python -m xautest -h``."""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import inspect
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .broker import BrokerConfig, SimBroker
from .engine import Engine
from .report import format_summary, write_report
from .strategy import Strategy

log = logging.getLogger("xautest")


# ---------------------------------------------------------------- argument helpers
def parse_time(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_leverage(s: str) -> float:
    """'100', '1:500', 'unlimited' / 'inf' / '1:unlimited' -> leverage as a float (inf = unlimited)."""
    v = s.strip().lower().removeprefix("1:")
    if v in ("unlimited", "inf", "infinite", "infinity"):
        return float("inf")
    try:
        lev = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad leverage {s!r}; use e.g. 100, 1:500 or unlimited")
    if lev <= 0:
        raise argparse.ArgumentTypeError("leverage must be > 0")
    return lev


def parse_params(items: list[str]) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--param expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        try:
            out[k] = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            out[k] = v
    return out


def load_strategy(spec: str) -> type[Strategy]:
    """'strategies/ema_cross.py:EmaCross', 'pkg.module:Class', or a file with a single Strategy subclass."""
    target, _, cls_name = spec.partition(":")
    if target.endswith(".py") or Path(target).exists():
        path = Path(target).resolve()
        if not path.exists():
            raise SystemExit(f"strategy file not found: {target}")
        sys.path.insert(0, str(path.parent))
        mod_spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    if cls_name:
        return getattr(module, cls_name)
    found = [c for _, c in inspect.getmembers(module, inspect.isclass)
             if issubclass(c, Strategy) and c is not Strategy and c.__module__ == module.__name__]
    if len(found) != 1:
        raise SystemExit(f"{spec}: name the class explicitly (found {[c.__name__ for c in found]})")
    return found[0]


def add_data_args(p: argparse.ArgumentParser, required_dates: bool = True) -> None:
    p.add_argument("--source", choices=["dukascopy", "csv"], default="dukascopy",
                   help="historical tick source (default: dukascopy .bi5 files)")
    p.add_argument("--data", default="data/dukascopy",
                   help="dukascopy: root folder; csv: file, folder or glob")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", type=parse_time, required=required_dates, help="UTC, e.g. 2025-03-01 or 2025-03-01T08:00")
    p.add_argument("--end", type=parse_time, required=required_dates, help="UTC, exclusive")
    p.add_argument("--csv-format", choices=["generic", "histdata", "mt5"], default="generic")
    p.add_argument("--server-tz", default=None,
                   help="clock used by CSV timestamps: NY+7 (most MT5 brokers), an IANA zone like Europe/Athens, "
                        "or a fixed offset like +2 (histdata default -5). `inspect` detects it.")
    p.add_argument("--utc-offset", dest="server_tz", help=argparse.SUPPRESS)  # older name
    p.add_argument("--price-scale", type=float, default=None, help="override Dukascopy price divisor (XAUUSD: 1000)")


def add_broker_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("simulated broker")
    d = BrokerConfig()
    g.add_argument("--balance", type=float, default=d.initial_balance)
    g.add_argument("--commission", type=float, default=d.commission_per_lot, help="USD per lot per side")
    g.add_argument("--slippage", type=float, default=d.slippage, help="USD/oz on market & stop fills")
    g.add_argument("--extra-spread", type=float, default=d.extra_spread, help="USD/oz added to raw spread")
    g.add_argument("--latency-ms", type=int, default=d.latency_ms)
    g.add_argument("--leverage", type=parse_leverage, default=d.leverage, help="e.g. 100, 1:500, unlimited")
    g.add_argument("--contract-size", type=float, default=d.contract_size, help="oz per lot")
    g.add_argument("--swap-long", type=float, default=d.swap_long, help="USD per lot per night")
    g.add_argument("--swap-short", type=float, default=d.swap_short, help="USD per lot per night")
    g.add_argument("--zero-spread", action="store_true", help="hypothetical: fill both sides at the mid price")
    g.add_argument("--daily-account", type=float, default=None,
                   help="USD at risk per day (e.g. 20): stop-out at $0, negative balance protection, profits withdrawn")
    g.add_argument("--spread", type=float, default=None,
                   help="re-quote every tick at this spread (USD/oz) around its mid, e.g. your broker's typical spread")
    r = p.add_argument_group("daily risk rules (active when --risk-per-trade is set; lots come from the stop distance)")
    r.add_argument("--risk-per-trade", type=float, default=None, help="USD lost if a trade's stop is hit")
    r.add_argument("--max-daily-loss", type=float, default=20.0, help="stop for the day at this closed loss")
    r.add_argument("--max-trades-day", type=int, default=2, help="0 = no limit")
    r.add_argument("--stop-after-wins", type=int, default=1,
                   help="stop after this many wins in a row (default 1 = first win; 0 = never)")
    r.add_argument("--stop-after-losses", type=int, default=0, help="stop after this many losses in a row (0 = never)")
    r.add_argument("--day-start-hour", type=float, default=0.0, help="UTC hour the trading day starts")
    r.add_argument("--weekly-loss-limit", type=float, default=0.0, help="stop for the week at this loss (0 = off)")
    r.add_argument("--monthly-loss-limit", type=float, default=0.0, help="stop for the month at this loss (0 = off)")
    r.add_argument("--skip-stop-below", type=float, default=0.0,
                   help="skip trades whose stop is closer than this (USD/oz), e.g. spread / 0.03")


def add_strategy_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strategy", required=True, help="path.py:ClassName or module:ClassName")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="strategy parameter (repeatable)")
    p.add_argument("--out", default=None, help="results folder (default results/<strategy>_<timestamp>)")
    p.add_argument("--no-plot", action="store_true")


def broker_from_args(a) -> SimBroker:
    return SimBroker(BrokerConfig(
        initial_balance=a.balance, commission_per_lot=a.commission, slippage=a.slippage,
        extra_spread=a.extra_spread, latency_ms=a.latency_ms, leverage=a.leverage,
        contract_size=a.contract_size, swap_long=a.swap_long, swap_short=a.swap_short, zero_spread=a.zero_spread, fixed_spread=a.spread,
        daily_account=a.daily_account, day_start_hour=getattr(a, "day_start_hour", 0.0)))


def rules_from_args(a):
    if a.risk_per_trade is None:
        return None
    from .risk import DailyRiskRules
    return DailyRiskRules(risk_per_trade=a.risk_per_trade, max_daily_loss=a.max_daily_loss,
                          max_trades=a.max_trades_day, day_start_hour=a.day_start_hour,
                          stop_after_wins=a.stop_after_wins, stop_after_losses=a.stop_after_losses,
                          min_stop=a.skip_stop_below, weekly_loss_limit=a.weekly_loss_limit,
                          monthly_loss_limit=a.monthly_loss_limit)


def report_days(rules, out: Path) -> None:
    """Per-day outcome under the risk rules: written to daily.csv and summarised on screen."""
    import pandas as pd
    from .risk import DAY_MS
    rows = [{"day": pd.Timestamp(d * DAY_MS + int(rules.day_start_hour * 3_600_000), unit="ms").date(),
             "trades": st.trades, "pnl": round(st.pnl, 2), "ended_by": st.stopped or "no further signal"}
            for d, st in sorted(rules.days.items()) if st.trades]
    df = pd.DataFrame(rows, columns=["day", "trades", "pnl", "ended_by"])
    df.to_csv(out / "daily.csv", index=False)
    if df.empty:
        print("\nrisk rules: no trades taken", rules.stats)
        return
    print(f"\n== days ({len(df)} traded) ==")
    for name, m in [("stopped after win(s)", df.ended_by == "win"),
                    ("hit the daily loss limit", df.ended_by == "loss limit"),
                    ("stopped after losses in a row", df.ended_by == "losses"),
                    ("reached max trades", df.ended_by == "max trades"),
                    ("ran out of signals", df.ended_by == "no further signal")]:
        if m.any():
            print(f"  {name:<28} {int(m.sum()):3d} days  total {df.pnl[m].sum():9.2f}")
    print(f"  average day {df.pnl.mean():.2f}, best {df.pnl.max():.2f}, worst {df.pnl.min():.2f}, "
          f"winning days {(df.pnl > 0).mean():.0%}")
    print(f"  orders: {rules.stats}")


def historical_feed(a):
    from .feeds import CSVTickFeed, DukascopyFeed
    if a.source == "dukascopy":
        if a.start is None or a.end is None:
            raise SystemExit("--start and --end are required for --source dukascopy")
        return DukascopyFeed(a.data, a.start, a.end, a.symbol, a.price_scale)
    return CSVTickFeed(a.data, a.csv_format, None, a.start, a.end, server_tz=a.server_tz)


def out_dir(a, cls, mode: str) -> Path:
    if a.out:
        return Path(a.out)
    return Path("results") / f"{cls.__name__}_{mode}_{datetime.now():%Y%m%d_%H%M%S}"


def meta_for(a, cls, mode: str, broker: SimBroker) -> dict:
    return {"mode": mode, "strategy": a.strategy, "params": vars_of(cls, a),
            "broker": {k: ("unlimited" if v == float("inf") else v) for k, v in broker.cfg.__dict__.items()}, "source": getattr(a, "source", None), "data": getattr(a, "data", None),
            "start": str(getattr(a, "start", None)), "end": str(getattr(a, "end", None))}


def vars_of(cls, a) -> dict:
    return {**cls.params, **parse_params(a.param)}


# ---------------------------------------------------------------- commands
def cmd_backtest(a) -> None:
    cls = load_strategy(a.strategy)
    strategy = cls(**parse_params(a.param))
    strategy.risk = rules_from_args(a)
    broker = broker_from_args(a)
    t0 = time.perf_counter()
    result = Engine(strategy, broker, historical_feed(a)).run()
    dt = time.perf_counter() - t0
    out = out_dir(a, cls, "backtest")
    s = write_report(result, out, f"{cls.__name__} backtest", meta_for(a, cls, "backtest", broker),
                     plot=not a.no_plot)
    print(format_summary(s, f"{cls.__name__} backtest"))
    rate = result.ticks_processed / dt if dt > 0 else 0
    if strategy.risk is not None:
        report_days(strategy.risk, out)
    print(f"\n{result.ticks_processed:,} ticks in {dt:.1f}s ({rate:,.0f} ticks/s). Results: {out}")


def cmd_live(a) -> None:
    from .feeds import OandaStreamFeed, RealtimeReplay, TickRecorder

    cls = load_strategy(a.strategy)
    strategy = cls(**parse_params(a.param))
    strategy.risk = rules_from_args(a)
    broker = broker_from_args(a)
    if a.feed == "oanda":
        feed = OandaStreamFeed(a.instrument, env=a.oanda_env)
        print(f"Forward test on live OANDA {a.oanda_env} prices for {a.instrument}. "
              f"Orders are SIMULATED; nothing is sent to the broker. Ctrl+C to stop.")
    else:
        feed = RealtimeReplay(historical_feed(a), a.speed)
        print(f"Rehearsal: replaying historical ticks at {a.speed:g}x real time. Ctrl+C to stop.")
    recorder = None if a.no_record else TickRecorder(a.record, a.symbol)
    if recorder:
        print(f"Recording ticks to {recorder.path}")
    out = out_dir(a, cls, "live")
    meta = meta_for(a, cls, "live", broker)
    meta["feed"] = a.feed

    def checkpoint(snapshot):
        s = write_report(snapshot, out, f"{cls.__name__} live", meta, plot=False)
        t = broker.tick
        log.info("%s bid %.3f ask %.3f | equity %.2f | open %d | trades %d | ticks %d",
                 t.time.strftime("%H:%M:%S") if t else "-", t.bid if t else 0, t.ask if t else 0,
                 broker.equity(), len(broker.positions), s["trades"], snapshot.ticks_processed)

    engine = Engine(strategy, broker, feed, live=True, recorder=recorder, close_on_end=not a.keep_open,
                    on_checkpoint=checkpoint, checkpoint_every_s=a.status_every)
    result = engine.run()
    s = write_report(result, out, f"{cls.__name__} live", meta, plot=not a.no_plot)
    print(format_summary(s, f"{cls.__name__} live"))
    print(f"\nResults: {out}")
    if recorder:
        print(f"Re-run this session as a backtest:\n  python -m xautest backtest --strategy {a.strategy} "
              f"--source csv --data {recorder.path} --latency-ms {a.latency_ms} ...same broker options...")


def cmd_fetch(a) -> None:
    from .feeds import download_s3
    print(f"Downloading {a.symbol} daily tick files from s3://{a.bucket} (requester pays: your AWS account "
          f"is billed for requests and transfer) into {a.dest}")
    stats = download_s3(a.dest, a.start, a.end, a.symbol, a.bucket, a.region, a.workers)
    print(f"downloaded {stats['downloaded']}, already cached {stats['cached']}, missing {stats['missing']}, "
          f"{stats['bytes'] / 1e6:.1f} MB")


def cmd_synth(a) -> None:
    from .feeds.synthetic import generate
    df = generate(a.start, a.end, a.price, a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df):,} SYNTHETIC ticks to {a.out} (for testing the pipeline only, not market data)")


def cmd_inspect(a) -> None:
    import numpy as np
    n, spreads, gaps, last = 0, [], [], None
    first = None
    for t in historical_feed(a):
        if first is None:
            first = t
        spreads.append(t.ask - t.bid)
        if last is not None:
            gaps.append(t.ts - last.ts)
        last = t
        n += 1
    if not n:
        raise SystemExit("no ticks")
    sp, g = np.array(spreads), np.array(gaps or [0])
    big = int((g > 5 * 60_000).sum())
    print(f"ticks          {n:,}\nfirst          {first}\nlast           {last}")
    print(f"spread USD/oz  median {np.median(sp):.3f}  p95 {np.percentile(sp, 95):.3f}  max {sp.max():.3f}  "
          f"negative {(sp < 0).sum()}")
    print(f"tick gap ms    median {np.median(g):.0f}  p99 {np.percentile(g, 99):.0f}  gaps>5min {big}")
    if a.source == "csv":
        report_server_tz(a)


def report_server_tz(a) -> None:
    """Detect the file's clock from gold's daily break (17:00 New York) and compare with --server-tz."""
    import numpy as np
    from .feeds import CSVTickFeed
    from .timezones import detect_server_tz
    raw = np.fromiter((t.ts for t in CSVTickFeed(a.data, a.csv_format, None, server_tz=0)), np.int64)
    det = detect_server_tz(raw)
    if det is None:
        print("server time  could not detect (fewer than 2 daily breaks; export a longer period)")
        return
    print(f"daily break    last tick ~{det.break_local}, resumes ~{det.resume_local} in file time "
          f"({det.breaks} breaks found)")
    pick = det.best[0]
    alts = f" (equally consistent: {', '.join(det.best[1:4])}; export data spanning a DST change to tell them apart)" \
        if len(det.best) > 1 else ""
    print(f"server time    detected {pick}, {det.score:.0%} of breaks at 17:00 New York{alts}")
    given = a.server_tz
    if given is None:
        print(f"               -> add  --server-tz {pick}  to backtest/inspect commands")
    elif str(given) not in det.best:
        print(f"               WARNING: --server-tz {given} does not match the data; expected {pick}")


def parse_grid(items: list[str]) -> dict[str, list]:
    spec = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--grid expects name=v1,v2,..., got {item!r}")
        k, v = item.split("=", 1)
        vals = []
        for x in v.split(","):
            try:
                vals.append(ast.literal_eval(x.strip()))
            except (ValueError, SyntaxError):
                vals.append(x.strip())
        spec[k.strip()] = vals
    return spec


def cmd_optimize(a) -> None:
    import pandas as pd
    from . import optimize as opt
    from .feeds.arrays import build_cache, cache_dir_for
    from .feeds.csvfeed import resolve_paths
    from .models import dt_to_ms

    cls = load_strategy(a.strategy)
    spec = parse_grid(a.grid)
    if not spec:
        raise SystemExit("give at least one --grid name=v1,v2,...")
    unknown = set(spec) - set(cls.params)
    if unknown:
        raise SystemExit(f"{cls.__name__} has no parameter(s) {sorted(unknown)}; known: {sorted(cls.params)}")
    combos = opt.grid(spec)

    files = resolve_paths(a.data) if a.source == "csv" else []
    key = {"source": a.source, "data": str(a.data), "files": [str(f) for f in files], "fmt": a.csv_format,
           "tz": a.server_tz, "start": str(a.start), "end": str(a.end), "symbol": a.symbol, "scale": a.price_scale}
    cache = cache_dir_for(Path(".cache") / "ticks", key)
    if not (cache / "ok").exists():
        print(f"caching ticks to {cache} (first run only)...")
        build_cache(historical_feed(a), cache)

    start = dt_to_ms(a.start) if a.start else None
    end = dt_to_ms(a.end) if a.end else None
    if a.train_end:
        split = dt_to_ms(a.train_end)
        windows, names = [(start, split), (split, end)], ["train", "test"]
    else:
        windows, names = [(start, end)], ["train"]
        print("WARNING: no --train-end, so there is no out-of-sample test; results are in-sample only.")

    broker = broker_from_args(a)
    rules = rules_from_args(a)
    risk_cfg = None if rules is None else {k: getattr(rules, k) for k in (
        "risk_per_trade", "max_daily_loss", "max_trades", "day_start_hour", "stop_after_wins", "stop_after_losses", "min_stop")}
    fixed = parse_params(a.param)
    print(f"{len(combos)} combinations x {len(names)} period(s) on {a.workers or 'auto'} workers...")

    def progress(i, n):
        if i == n or i % max(1, n // 10) == 0:
            print(f"  {i}/{n}", flush=True)

    t0 = time.perf_counter()
    baseline = opt.run([{}], windows, cache, a.strategy, fixed, broker.cfg.__dict__, risk_cfg, 1)
    results = opt.run(combos, windows, cache, a.strategy, fixed, broker.cfg.__dict__, risk_cfg, a.workers, progress)
    df = opt.to_frame(results, names)
    base = opt.to_frame(baseline, names).iloc[0]

    obj = a.objective
    eligible = df[df[f"train_trades"] >= a.min_trades].copy()
    ranked = eligible.sort_values(f"train_{obj}", ascending=False)
    out = Path(a.out) if a.out else Path("results") / f"optimize_{cls.__name__}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "all_combinations.csv", index=False)

    cols = list(spec)
    show = cols + [f"train_{m}" for m in ("trades", "net", "avg_day", obj)]
    if "test" in names:
        show += [f"test_{m}" for m in ("trades", "net", "avg_day", obj, "worst_day")]
    show = list(dict.fromkeys(show))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 50)
    print(f"\n== top {a.top} by train {obj} (min {a.min_trades} train trades; {len(eligible)}/{len(df)} eligible) ==")
    print(ranked[show].head(a.top).to_string(index=False))
    bshow = {k.replace("train_", "train ").replace("test_", "test "): base[k] for k in show if k in base and k not in cols}
    print("\nbaseline (strategy defaults):", ", ".join(f"{k}={v}" for k, v in bshow.items()))

    if "test" in names:
        rho = opt.spearman(eligible[f"train_{obj}"], eligible[f"test_{obj}"])
        top = ranked.head(a.top)
        print(f"\ntrain-vs-test rank correlation over {len(eligible)} combinations: {rho:+.2f}"
              "   (near 0 or negative = the search is fitting noise)")
        print(f"top {len(top)} on test: average net {top['test_net'].mean():+.2f}, "
              f"{(top['test_net'] > 0).mean():.0%} profitable;  all combinations: average test net "
              f"{eligible['test_net'].mean():+.2f}, {(eligible['test_net'] > 0).mean():.0%} profitable")

    print("\n== average score by parameter value (look for broad plateaus, not single peaks) ==")
    for c in cols:
        agg = {f"train_{obj}": "mean"}
        if "test" in names:
            agg[f"test_{obj}"] = "mean"
        g = eligible.groupby(c).agg(agg).round(3)
        print(f"  {c}: " + "  ".join(f"{idx}: " + "/".join(f"{v:+.2f}" for v in row) for idx, row in g.iterrows()))
    print(f"\n{len(combos)} combinations in {time.perf_counter() - t0:.0f}s. All results: {out / 'all_combinations.csv'}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="xautest", description="XAU/USD tick backtesting and live forward testing")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backtest", help="run a strategy over historical ticks")
    add_strategy_args(p); add_data_args(p, required_dates=False); add_broker_args(p)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("live", help="forward-test a strategy on live ticks (simulated fills)")
    add_strategy_args(p); add_data_args(p, required_dates=False); add_broker_args(p)
    p.add_argument("--feed", choices=["oanda", "replay"], default="oanda",
                   help="oanda: live stream; replay: historical ticks at --speed x real time")
    p.add_argument("--instrument", default="XAU_USD")
    p.add_argument("--oanda-env", choices=["practice", "live"], default="practice",
                   help="which OANDA price stream to read (prices only; no orders are placed)")
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--record", default="recordings", help="folder for recorded live ticks")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--status-every", type=float, default=60.0, help="seconds between status lines / report writes")
    p.add_argument("--keep-open", action="store_true", help="don't flatten open positions on exit")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("optimize", help="search strategy parameters with a train/test split")
    add_strategy_args(p); add_data_args(p, required_dates=False); add_broker_args(p)
    p.add_argument("--grid", action="append", default=[], metavar="NAME=V1,V2,...",
                   help="parameter values to try (repeatable); all combinations are run")
    p.add_argument("--train-end", type=parse_time, default=None,
                   help="UTC date splitting train (before) from test (after)")
    p.add_argument("--objective", choices=["sharpe", "avg_day", "net", "profit_factor"], default="sharpe")
    p.add_argument("--min-trades", type=int, default=10, help="ignore combinations with fewer train trades")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--workers", type=int, default=None)
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("fetch", help="download Dukascopy daily tick files from their requester-pays S3 bucket")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", type=parse_time, required=True)
    p.add_argument("--end", type=parse_time, required=True)
    p.add_argument("--dest", default="data/dukascopy")
    from .feeds.dukascopy import DEFAULT_S3_BUCKET, DEFAULT_S3_REGION
    p.add_argument("--bucket", default=DEFAULT_S3_BUCKET)
    p.add_argument("--region", default=DEFAULT_S3_REGION)
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("synth", help="write SYNTHETIC ticks for pipeline testing")
    p.add_argument("--start", type=parse_time, required=True)
    p.add_argument("--end", type=parse_time, required=True)
    p.add_argument("--out", default="data/synthetic/XAUUSD_synthetic.csv")
    p.add_argument("--price", type=float, default=2650.0)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("inspect", help="data quality check: tick count, spreads, gaps")
    add_data_args(p, required_dates=False)
    p.set_defaults(func=cmd_inspect)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    a.func(a)


if __name__ == "__main__":
    main()
