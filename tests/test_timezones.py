from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from xautest.feeds import CSVTickFeed
from xautest.models import dt_to_ms
from xautest.timezones import detect_server_tz, parse_server_tz

UTC = timezone.utc


def local_ms(*args):
    """A wall-clock time in the file, expressed as if it were UTC (how the CSV reader parses it)."""
    return dt_to_ms(datetime(*args, tzinfo=UTC))


def test_ny_plus_7_follows_us_daylight_saving():
    conv = parse_server_tz("NY+7")
    # 2025-03-07 (before US DST): server 00:30 = UTC 22:30 previous day (UTC+2)
    # 2025-03-12 (after US DST, before EU DST): server 00:30 = UTC 21:30 previous day (UTC+3)
    out, ok = conv(np.array([local_ms(2025, 3, 7, 0, 30), local_ms(2025, 3, 12, 0, 30)]))
    assert ok.all()
    assert out.tolist() == [dt_to_ms(datetime(2025, 3, 6, 22, 30, tzinfo=UTC)),
                            dt_to_ms(datetime(2025, 3, 11, 21, 30, tzinfo=UTC))]


def test_athens_and_fixed_offsets():
    out, _ = parse_server_tz("Europe/Athens")(np.array([local_ms(2025, 3, 12, 0, 30)]))
    assert out[0] == dt_to_ms(datetime(2025, 3, 11, 22, 30, tzinfo=UTC))  # EU still on winter time
    out, _ = parse_server_tz("+3")(np.array([local_ms(2025, 3, 12, 0, 30)]))
    assert out[0] == dt_to_ms(datetime(2025, 3, 11, 21, 30, tzinfo=UTC))
    with pytest.raises(ValueError):
        parse_server_tz("Mars/Olympus")


def gold_minutes_utc(start, end):
    """One tick a minute while gold trades: closed 17:00-18:00 New York daily and Fri 17:00 - Sun 18:00."""
    t = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    ny = t.tz_convert("America/New_York")
    closed = (ny.hour == 17) | (ny.day_of_week == 5) | ((ny.day_of_week == 4) & (ny.hour >= 17)) | \
             ((ny.day_of_week == 6) & (ny.hour < 18))
    return t[~closed]


def as_server_clock(utc_index, tz):
    if tz == "NY+7":
        wall = utc_index.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)
    else:
        wall = utc_index.tz_convert(tz).tz_localize(None)
    return ((wall - pd.Timestamp(0)) // pd.Timedelta(milliseconds=1)).to_numpy(np.int64)


@pytest.mark.parametrize("tz", ["NY+7", "Europe/Athens"])
def test_detection_tells_ny7_from_athens_across_march(tz):
    utc = gold_minutes_utc("2025-03-01", "2025-04-10")
    det = detect_server_tz(as_server_clock(utc, tz))
    assert det.best == [tz] and det.score == 1.0
    if tz == "NY+7":  # each trading day starts at 00:00 server time
        assert (det.break_local, det.resume_local) == ("23:59", "01:00")


def test_detection_within_one_dst_regime_lists_alternatives():
    utc = gold_minutes_utc("2025-01-06", "2025-01-25")
    det = detect_server_tz(as_server_clock(utc, "NY+7"))
    assert det.best[0] == "NY+7" and "+2" in det.best


def test_mt5_file_with_ny7_clock_converts_to_utc(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("<DATE>\t<TIME>\t<BID>\t<ASK>\n"
                 "2025.07.01\t10:00:00.000\t3300.10\t3300.30\n")
    (t,) = list(CSVTickFeed(f, "mt5", server_tz="NY+7"))
    assert t.ts == dt_to_ms(datetime(2025, 7, 1, 7, 0, tzinfo=UTC))  # summer: UTC+3
