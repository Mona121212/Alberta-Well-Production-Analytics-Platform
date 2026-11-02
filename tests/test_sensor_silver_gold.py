"""Sensor Silver and Gold, stage 3 (DEC-034): the rules that 04_sensor_silver and 05_sensor_gold apply in Spark, tested
here on the same generator and quality code, and static checks of the notebooks. Run: python -m pytest tests"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from sensor.config import hour_grid_utc, load_config
from sensor.downtime import detect_downtime
from sensor.generate import generate_backfill
from sensor.hierarchy import build_hierarchy
from sensor.quality import classify
from sensor.stream import build_schedule

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()
LAYERS = CFG["layers"]
GRID = hour_grid_utc(CFG)
HIERARCHY = build_hierarchy(CFG)
START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
NOTEBOOKS = {name: json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
             for name in ("04_sensor_silver", "05_sensor_gold")}
RECONCILIATION_TESTS = ["bronze_equals_silver_received_plus_rejects", "gold_hourly_good_count_and_sum_match_silver",
                        "downtime_events_match_simulation_truth", "flagged_wells_match_configured_anomalies"]


def source(name):
    return "".join("".join(c["source"]) for c in NOTEBOOKS[name]["cells"])


@pytest.fixture(scope="module")
def backfill():
    readings, truth = generate_backfill(CFG)
    return classify(readings, HIERARCHY, GRID, CFG), truth


@pytest.fixture(scope="module")
def stream_classified():
    schedule, _ = build_schedule(CFG, START, 15, "run-1")
    events = pd.DataFrame([e for batch in schedule for e in batch]).drop_duplicates(["tag_name", "ts_utc"])
    events["ts_utc"] = pd.to_datetime(events["ts_utc"], utc=True)
    ticks = pd.DatetimeIndex(sorted(events["ts_utc"].unique()))
    return classify(events, HIERARCHY, ticks, CFG)


# Downtime events ------------------------------------------------------------------------------------------------

def test_detected_events_match_the_simulation_truth(backfill):
    classified, truth = backfill
    detected = detect_downtime(classified, CFG)
    found = {(r.well_key, r.start_utc, r.duration_hours) for r in detected.itertuples()}
    expected = {(e["well_key"], pd.Timestamp(e["start_utc"]), e["hours"]) for e in truth["downtime_events"]}
    assert found == expected


def test_lost_volume_is_positive_and_near_the_running_rate(backfill):
    classified, _ = backfill
    detected = detect_downtime(classified, CFG)
    wells = {w["well_key"]: w for w in CFG["wells"]}
    for r in detected.itertuples():
        well = wells[r.well_key]
        hourly = well["petrinex_volume"] / well["petrinex_hours"]
        assert r.lost_volume == pytest.approx(hourly * r.duration_hours, rel=0.35), r


def small_config(well):
    return {**CFG, "wells": [well]}


def tiny_readings(well, run, flow):
    """Classified readings for one well, with Good quality unless a flow value is None."""
    ts = pd.date_range("2025-11-01T06:00Z", periods=len(run), freq="h")
    rows = []
    for code, values in (("RUN", run), (f"FLOW_{well['product']}", flow)):
        name = f"{well['facility_id']}.{well['well_key']}.{code}"
        rows += [{"tag_name": name, "ts_utc": t, "value": v, "quality": "Good" if v is not None else "Bad"}
                 for t, v in zip(ts, values)]
    return pd.DataFrame(rows)


def test_a_one_hour_shut_in_is_not_an_event():
    well = CFG["wells"][0]
    data = tiny_readings(well, [1.0, 0.0, 1.0], [24.0, 0.0, 24.0])
    assert detect_downtime(data, small_config(well)).empty


def test_lost_volume_uses_the_lookback_rate():
    well = CFG["wells"][0]
    data = tiny_readings(well, [1.0, 1.0, 0.0, 0.0, 1.0], [24.0, 48.0, 0.0, 0.0, 480.0])
    event = detect_downtime(data, small_config(well)).iloc[0]
    assert event["duration_hours"] == 2
    assert event["lost_volume"] == pytest.approx(36.0 * 2 / 24)


def test_event_at_the_start_of_the_month_uses_the_month_mean():
    well = CFG["wells"][0]
    data = tiny_readings(well, [0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 24.0, 72.0])
    event = detect_downtime(data, small_config(well)).iloc[0]
    assert event["lost_volume"] == pytest.approx(48.0 * 2 / 24)


# Quality: the backfill holds the bad readings, the stream has none (DEC-034) ------------------------------------

def test_backfill_quality_counts_follow_from_the_injections(backfill):
    classified, truth = backfill
    kinds = Counter(i["kind"] for i in truth["injections"])
    frozen = sum(i["hours"] + 1 for i in truth["injections"] if i["kind"] == "frozen")
    reasons = Counter(classified["quality_reason"])
    assert reasons["missing"] == kinds["missing"] == 240
    assert reasons["outside_physical_range"] == kinds["out_of_range"] == 80
    assert reasons["frozen"] == frozen == 360
    assert reasons["outside_normal_range"] == 0


def test_stream_readings_are_all_good(stream_classified):
    assert set(stream_classified["quality"]) == {"Good"}
    assert len(stream_classified) == 3000


# Silver row arithmetic -------------------------------------------------------------------------------------------

def test_silver_rows_are_received_minus_duplicates_plus_missing(backfill):
    classified, _ = backfill
    _, expected = build_schedule(CFG, START, 15, "run-1")
    backfill_rows = int(classified["value"].notna().sum())
    bronze = backfill_rows + expected["events"]
    missing = int((classified["quality_reason"] == "missing").sum())
    silver = len(classified) + expected["unique_readings"]
    assert silver == bronze - expected["duplicates"] + missing


# Notebooks and config -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_has_no_hard_coded_layer_tables_or_non_english_text(name):
    code = "".join("".join(c["source"]) for c in NOTEBOOKS[name]["cells"] if c["cell_type"] == "code")
    for key, table in LAYERS.items():
        if key.endswith("_table"):
            assert f'"{table}"' not in code, table
    assert not re.search(r"[一-鿿]", source(name))


def test_gold_runs_exactly_the_four_reconciliation_tests():
    code = source("05_sensor_gold")
    assert re.findall(r'\("([a-z_]+)", (?:bronze_rows|0)', code) == RECONCILIATION_TESTS


def test_gold_writes_results_before_it_stops():
    code = source("05_sensor_gold")
    assert code.index('saveAsTable("reconciliation_results")') < code.index("raise ValueError(f\"Reconciliation failed")


def test_gold_refuses_a_silver_run_that_did_not_finish():
    code = source("05_sensor_gold")
    assert "pipeline_run_log" in code and 'F.col("status") == "SUCCESS"' in code


def test_silver_casts_stream_timestamps():
    code = source("04_sensor_silver")
    assert 'F.col("ts_utc").cast("timestamp")' in code and 'F.col("sent_utc").cast("timestamp")' in code


def test_notebooks_use_the_default_lakehouse():
    for nb in NOTEBOOKS.values():
        assert nb["metadata"]["dependencies"]["lakehouse"]["default_lakehouse_name"] == "alberta_wells"
