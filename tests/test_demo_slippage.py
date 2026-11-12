import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from demo_slippage import parse  # noqa: E402

LOG = """server_time,event,detail
2026.11.13 09:20:02,entry,BUY 0.01 lots at 4000.15, TP 4010.12, spread 0.20
2026.11.13 09:20:02,fill,entry BUY wanted 4000.120 got 4000.150 slip 0.030 send_ms 85 spread 0.200
2026.11.13 11:02:10,fill,exit tp wanted 4010.120 got 4010.120 slip 0.000 send_ms 0 spread 0.210
2026.11.14 20:00:00,fill,exit close wanted 3990.500 got 3990.380 slip 0.120 send_ms 140 spread 0.250
"""


def test_parse_reads_only_fill_rows():
    f = parse(LOG)
    assert list(f.leg) == ["entry", "exit: tp", "exit: close"]
    assert f.slip.tolist() == pytest.approx([0.03, 0.0, 0.12])
    assert f.send_ms.tolist() == [85, 0, 140] and f.side[0] == "BUY"


def test_parse_reads_stop_loss_fills():
    f = parse("2026.11.16 13:40:11,fill,exit sl wanted 3980.120 got 3979.950 slip 0.170 send_ms 0 spread 0.300\n")
    assert list(f.leg) == ["exit: sl"] and f.slip[0] == pytest.approx(0.17)
