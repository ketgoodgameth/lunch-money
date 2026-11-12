"""The current version is saved in one file; the Python strategy and the MT5 EA defaults must agree with it."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategies"))
from reaction_zone import ReactionZone  # noqa: E402

CAND = json.loads((ROOT / "results" / "candidates" / "reaction_zone_growth.json").read_text())


def test_every_strategy_setting_is_written_out():
    assert set(CAND["params"]) == set(ReactionZone.params)
    ReactionZone(**CAND["params"])                       # raises on an unknown setting


def test_it_is_the_growth_version():
    p, r = CAND["params"], CAND["rules"]
    assert (p["tp_usd"], p["stop_dist"], p["flat_hour"], p["min_wick"], p["exec_mode"]) == (10.0, 20.0, 20, 0.3, "m5")
    assert p["sessions"] == "07:00-10:00,12:30-16:00" and not p["use_divergence"] and not p["h1_macd_filter"]
    assert (r["max_trades"], r["stop_after_wins"], r["use_strategy_lots"]) == (2, 1, True)
    assert CAND["broker"]["daily_account"] is None and CAND["broker"]["fixed_spread"] == 0.20


def test_ea_defaults_match_the_saved_version():
    src = (ROOT / "mt5" / "ReactionZoneEA.mq5").read_text()
    inputs = dict(re.findall(r"^input\s+\w+\s+(Inp\w+)\s*=\s*([^;]+);", src, flags=re.M))
    for name, want in CAND["ea_inputs"].items():
        if name == "file":
            continue
        got = inputs[name].strip().strip('"')
        if isinstance(want, bool):
            assert got == str(want).lower(), name
        elif isinstance(want, str):
            assert got == want, name
        else:
            assert float(got) == float(want), name
    p = CAND["params"]
    ea = CAND["ea_inputs"]
    assert (ea["InpTakeProfitUSD"], ea["InpStopDist"], ea["InpMinWick"], ea["InpSessions"], ea["InpMaxSpread"]) == \
        (p["tp_usd"], p["stop_dist"], p["min_wick"], p["sessions"], p["max_spread"])
    assert (ea["InpSwingLookback"], ea["InpSetupValidMin"], ea["InpMaxFvgAge"], ea["InpFlatHourUTC"]) == \
        (p["swing_lookback"], p["setup_valid_min"], p["max_fvg_age"], p["flat_hour"])
