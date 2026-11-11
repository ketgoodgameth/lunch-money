from datetime import datetime, timezone

import numpy as np
import pytest

from xautest.bars import BarBuilder, parse_timeframe
from xautest.feeds import CSVTickFeed, DukascopyFeed
from xautest.feeds.dukascopy import RECORD, daily_path, decode_bi5, encode_bi5, hourly_path
from xautest.feeds.live import OandaStreamFeed, parse_rfc3339_ms
from xautest.models import Tick, dt_to_ms

UTC = timezone.utc


def records(ms, bid, ask):
    r = np.zeros(len(ms), RECORD)
    r["ms"], r["bid"], r["ask"] = ms, bid, ask
    r["bid_vol"], r["ask_vol"] = 0.5, 0.25
    return r


def test_bi5_roundtrip():
    r = records([0, 10, 999], [2650123, 2650100, 2650200], [2650323, 2650300, 2650400])
    out = decode_bi5(encode_bi5(r))
    assert out.tolist() == r.tolist()
    assert len(decode_bi5(b"")) == 0


def test_dukascopy_daily_and_hourly_layouts(tmp_path):
    d1, d2 = datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)
    p = daily_path(tmp_path, "XAUUSD", d1.date())
    assert p.parts[-3:] == ("2025", "00", "02_ticks.bi5")  # zero-based month
    p.parent.mkdir(parents=True)
    p.write_bytes(encode_bi5(records([1000, 2000], [2650123, 2650223], [2650323, 2650423])))
    h = hourly_path(tmp_path, "XAUUSD", d2.date(), 14)
    h.parent.mkdir(parents=True)
    h.write_bytes(encode_bi5(records([5], [2651000], [2651200])))
    hourly_path(tmp_path, "XAUUSD", d2.date(), 15).write_bytes(b"")  # empty hour

    ticks = list(DukascopyFeed(tmp_path, d1, datetime(2025, 1, 4, tzinfo=UTC)))
    assert [t.bid for t in ticks] == [2650.123, 2650.223, 2651.0]
    assert ticks[0].ts == dt_to_ms(d1) + 1000
    assert ticks[2].ts == dt_to_ms(d2) + 14 * 3600_000 + 5
    assert ticks[0].ask == pytest.approx(2650.323)


def test_dukascopy_wrong_scale_is_caught(tmp_path):
    d = datetime(2025, 1, 2, tzinfo=UTC)
    p = daily_path(tmp_path, "XAUUSD", d.date())
    p.parent.mkdir(parents=True)
    p.write_bytes(encode_bi5(records([0], [2650123], [2650323])))
    with pytest.raises(ValueError, match="price scale"):
        list(DukascopyFeed(tmp_path, d, datetime(2025, 1, 3, tzinfo=UTC), price_scale=100))


def test_dukascopy_missing_data_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(DukascopyFeed(tmp_path, datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)))


def test_csv_generic_iso_and_epoch(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("time,bid,ask\n2025-01-02T10:00:00.123Z,2650.1,2650.3\n2025-01-02T10:00:01Z,2650.2,2650.4\n")
    g = tmp_path / "b.csv"
    g.write_text("ts_ms,bid,ask,bid_vol,ask_vol\n1735812000123,2650.1,2650.3,1,2\n")
    a, = list(CSVTickFeed(g))
    assert a.ts == 1735812000123 and a.ask_vol == 2
    ticks = list(CSVTickFeed(f))
    assert ticks[0].ts == dt_to_ms(datetime(2025, 1, 2, 10, 0, 0, 123000, tzinfo=UTC))


def test_csv_histdata_converts_est_to_utc(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("20250102 170000123,2625.12,2625.45,0\n")
    (t,) = list(CSVTickFeed(f, "histdata"))
    assert t.ts == dt_to_ms(datetime(2025, 1, 2, 22, 0, 0, 123000, tzinfo=UTC))


def test_csv_mt5_forward_fills_and_offsets(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\n"
                 "2025.01.02\t12:00:00.000\t2650.10\t2650.30\t\t\t6\n"
                 "2025.01.02\t12:00:00.500\t2650.20\t\t\t\t2\n")
    ticks = list(CSVTickFeed(f, "mt5", utc_offset_hours=2))
    assert ticks[1].bid == 2650.20 and ticks[1].ask == 2650.30
    assert ticks[0].ts == dt_to_ms(datetime(2025, 1, 2, 10, tzinfo=UTC))


def test_bars_emitted_when_next_bar_starts():
    bb = BarBuilder("1min")
    assert parse_timeframe("5m") == 300_000 and parse_timeframe("1h") == 3_600_000
    base = dt_to_ms(datetime(2025, 1, 2, 10, tzinfo=UTC))
    out = [bb.update(Tick(base + ms, p, p + 0.2)) for ms, p in [(0, 10), (20_000, 12), (40_000, 9), (59_999, 11)]]
    assert out == [None] * 4
    bar = bb.update(Tick(base + 60_000, 11.5, 11.7))
    assert (bar.ts, bar.open, bar.high, bar.low, bar.close, bar.ticks) == (base, 10, 12, 9, 11, 4)


def test_oanda_time_parsing():
    assert parse_rfc3339_ms("2025-01-02T10:00:00.123456789Z") == dt_to_ms(
        datetime(2025, 1, 2, 10, 0, 0, 123000, tzinfo=UTC))
    assert parse_rfc3339_ms("2025-01-02T10:00:00Z") % 1000 == 0


def test_oanda_stream_parsing(monkeypatch):
    lines = [
        b'{"type":"HEARTBEAT","time":"2025-01-02T10:00:00.000000000Z"}',
        b'{"type":"PRICE","time":"2025-01-02T10:00:00.250000000Z","tradeable":true,'
        b'"bids":[{"price":"2650.12","liquidity":250000}],"asks":[{"price":"2650.42","liquidity":250000}]}',
        b'{"type":"PRICE","time":"2025-01-02T10:00:01Z","tradeable":false,"bids":[{"price":"1"}],"asks":[{"price":"2"}]}',
    ]

    class Resp:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_lines(self): return iter(lines)

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    feed = OandaStreamFeed(account_id="x", token="y")
    it = iter(feed)
    t = next(it)
    assert (t.bid, t.ask) == (2650.12, 2650.42)
    assert t.ts == dt_to_ms(datetime(2025, 1, 2, 10, 0, 0, 250000, tzinfo=UTC))


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig", "utf-8"])
def test_csv_mt5_any_encoding(tmp_path, encoding):
    f = tmp_path / "m.csv"
    text = ("<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\r\n"
            "2025.10.15\t09:00:00.250\t4180.11\t4180.42\t\t\t6\r\n"
            "2025.10.15\t09:00:01\t\t4180.47\t\t\t4\r\n")
    f.write_bytes(text.encode(encoding))
    ticks = list(CSVTickFeed(f, "mt5", utc_offset_hours=3))
    assert [(t.bid, t.ask) for t in ticks] == [(4180.11, 4180.42), (4180.11, 4180.47)]
    assert ticks[0].ts == dt_to_ms(datetime(2025, 10, 15, 6, 0, 0, 250000, tzinfo=UTC))


def test_csv_dukascopy_node_columns(tmp_path):
    f = tmp_path / "xauusd-tick.csv"
    f.write_text("timestamp,askPrice,bidPrice\n1735772400455,2626.592,2625.098\n")
    (t,) = list(CSVTickFeed(f))
    assert (t.ts, t.bid, t.ask) == (1735772400455, 2625.098, 2626.592)   # ask/bid order respected
