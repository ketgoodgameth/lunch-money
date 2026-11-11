"""Engine behaviour and the key guarantee: a recorded live session reproduces exactly as a backtest."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xautest import BrokerConfig, Engine, SimBroker, Strategy
from xautest.feeds import CSVTickFeed, RealtimeReplay, TickRecorder
from xautest.feeds.synthetic import generate
from xautest.models import Tick, dt_to_ms
from xautest.report import summarize, write_report

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from ema_cross import EmaCross  # noqa: E402

UTC = timezone.utc


@pytest.fixture(scope="module")
def synth_csv(tmp_path_factory):
    df = generate(datetime(2025, 3, 3, tzinfo=UTC), datetime(2025, 3, 5, tzinfo=UTC), seed=3)
    p = tmp_path_factory.mktemp("d") / "ticks.csv"
    df.to_csv(p, index=False)
    return p


class Recorder(Strategy):
    timeframe = "1min"

    def on_start(self):
        self.calls = []

    def on_bar(self, bar):
        self.calls.append(("bar", bar.ts, self.now))

    def on_tick(self, tick):
        self.calls.append(("tick", tick.ts))


def test_bar_delivered_before_tick_that_completes_it():
    base = dt_to_ms(datetime(2025, 3, 3, 10, tzinfo=UTC))
    ticks = [Tick(base, 1, 1.2), Tick(base + 30_000, 2, 2.2), Tick(base + 61_000, 3, 3.2)]
    s = Recorder()
    Engine(s, SimBroker(), ticks).run()
    assert s.calls == [("tick", base), ("tick", base + 30_000), ("bar", base, base + 61_000), ("tick", base + 61_000)]


def test_unknown_param_rejected():
    with pytest.raises(ValueError, match="no parameter"):
        EmaCross(nope=1)


def run(feed, **engine_kw):
    broker = SimBroker(BrokerConfig(commission_per_lot=3.5, latency_ms=80, slippage=0.02))
    return Engine(EmaCross(fast=8, slow=21, start_hour=0, end_hour=24), broker, feed, **engine_kw).run()


def test_backtest_produces_trades_and_report(synth_csv, tmp_path):
    result = run(CSVTickFeed(synth_csv))
    assert len(result.trades) > 5
    s = write_report(result, tmp_path / "r", "t")
    assert (tmp_path / "r" / "equity.png").exists()
    assert s["trades"] == len(result.trades)
    assert s["final_equity"] == pytest.approx(10_000 + sum(t.net_pnl for t in result.trades), abs=0.01)


def test_live_recording_replays_identically_as_backtest(synth_csv, tmp_path):
    rec = TickRecorder(tmp_path / "rec")
    live = run(RealtimeReplay(CSVTickFeed(synth_csv), speed=1e9), live=True, recorder=rec)
    back = run(CSVTickFeed(rec.path))
    assert live.ticks_processed == back.ticks_processed
    assert [t.as_dict() for t in live.trades] == [t.as_dict() for t in back.trades]
    assert summarize(live)["net_profit"] == summarize(back)["net_profit"]


def test_checkpoint_called_and_ctrl_c_stops_cleanly(synth_csv):
    snaps = []

    def feed():
        for i, t in enumerate(CSVTickFeed(synth_csv)):
            if i == 50_000:
                raise KeyboardInterrupt
            yield t

    result = run(feed(), live=True, on_checkpoint=snaps.append, checkpoint_every_s=0)
    assert result.stopped_reason == "stopped by user" and snaps
    assert result.ticks_processed == 50_000
