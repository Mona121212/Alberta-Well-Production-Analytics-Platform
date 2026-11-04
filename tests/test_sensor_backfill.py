"""PI-style simulated sensors, stage 1 (DEC-032): config, 721-hour grid, hierarchy, backfill generator, quality flags
and the reconciliation estimate. All readings are simulated; the anomaly wells are set on purpose in config/sensor.json.
Run: python -m pytest tests"""
import copy
from collections import Counter

import numpy as np
import pandas as pd
import pytest

from sensor.config import hour_grid_utc, load_config, tag_name, well_tag_codes
from sensor.generate import generate_backfill
from sensor.hierarchy import build_hierarchy
from sensor.quality import classify
from sensor.reconcile import reconcile

CFG = load_config()
GRID = hour_grid_utc(CFG)
HIERARCHY = build_hierarchy(CFG)
ANOMALIES = {a["well_key"]: a["volume_bias_pct"] for a in CFG["anomaly_wells"]["wells"]}
FAULT = CFG["sensor_fault"]
AGING = {(a["well_key"], a["tag_code"]): a for a in CFG["injections"]["aging_tags"]}
WELLS = {w["well_key"]: w for w in CFG["wells"]}


@pytest.fixture(scope="module")
def backfill():
    readings, truth = generate_backfill(CFG)
    classified = classify(readings, HIERARCHY, GRID, CFG)
    return readings, truth, classified


# Config ---------------------------------------------------------------------------------------------------------

def test_config_wells_and_facilities():
    wells = CFG["wells"]
    assert 30 <= len(wells) <= 50
    assert len({w["well_key"] for w in wells}) == len(wells)
    assert len({w["facility_id"] for w in wells}) >= 6
    assert {w["product"] for w in wells} == {"OIL", "GAS"}
    assert all(w["petrinex_volume"] > 0 and 0 < w["petrinex_hours"] <= 744 for w in wells)


def test_config_anomaly_wells_are_selected_wells():
    assert 2 <= len(ANOMALIES) <= 3
    assert set(ANOMALIES) <= {w["well_key"] for w in CFG["wells"]}
    assert all(abs(b) > CFG["reconciliation"]["threshold_pct"] for b in ANOMALIES.values())
    assert CFG["generator"]["normal_volume_bias_max_pct"] < CFG["reconciliation"]["threshold_pct"]


def test_config_tags_have_units_and_ranges():
    for t in CFG["tags"]:
        assert t["unit"] and t["attribute"]
        assert t["physical_range"][0] <= t["normal_range"][0] <= t["normal_range"][1] <= t["physical_range"][1]
    for w in CFG["wells"]:
        assert len(well_tag_codes(CFG, w)) == 5


# 721 hours ------------------------------------------------------------------------------------------------------

def test_month_has_721_hours_in_utc():
    assert len(GRID) == CFG["month"]["expected_hours"] == 721
    assert GRID[0] == pd.Timestamp("2025-11-01T06:00Z")          # local midnight, MDT (UTC-6)
    assert GRID[-1] == pd.Timestamp("2025-12-01T06:00Z")         # local 23:00 on Nov 30, MST (UTC-7)
    assert str(GRID.tz) == "UTC"
    assert (GRID[1:] - GRID[:-1] == pd.Timedelta(hours=1)).all()


def test_local_clock_repeats_one_hour_on_nov_2():
    local = GRID.tz_convert(CFG["month"]["timezone"])
    assert local[0].strftime("%Y-%m-%d %H:%M") == "2025-11-01 00:00"
    assert local[-1].strftime("%Y-%m-%d %H:%M") == "2025-11-30 23:00"
    wall = Counter(local.strftime("%Y-%m-%d %H:%M"))
    assert [k for k, v in wall.items() if v > 1] == ["2025-11-02 01:00"]


# Hierarchy ------------------------------------------------------------------------------------------------------

def test_hierarchy_levels_and_paths():
    counts = HIERARCHY["node_type"].value_counts().to_dict()
    n_wells = len(CFG["wells"])
    assert counts == {"facility": len({w["facility_id"] for w in CFG["wells"]}), "well": n_wells, "tag": 5 * n_wells}
    assert HIERARCHY["af_path"].is_unique
    tags = HIERARCHY[HIERARCHY["node_type"] == "tag"]
    assert tags["tag_name"].is_unique
    assert set(tags["parent_path"]) == set(HIERARCHY.loc[HIERARCHY["node_type"] == "well", "af_path"])
    assert tags["af_path"].str.startswith(CFG["af"]["root"] + "\\").all()
    assert tags["af_path"].str.contains(r"\|").all()
    assert tags[["unit", "physical_min", "normal_max"]].notna().all().all()


# Generator ------------------------------------------------------------------------------------------------------

def test_generator_is_reproducible(backfill):
    readings, truth, _ = backfill
    again, truth_again = generate_backfill(CFG)
    pd.testing.assert_frame_equal(readings, again)
    assert truth == truth_again


def test_other_seed_gives_other_readings(backfill):
    other = copy.deepcopy(CFG)
    other["seed"] += 1
    readings, _ = generate_backfill(other)
    assert not readings["value"].equals(backfill[0]["value"])


def test_row_count_and_keys(backfill):
    readings, truth, _ = backfill
    n_missing = sum(1 for i in truth["injections"] if i["kind"] == "missing")
    assert len(readings) == 5 * len(CFG["wells"]) * 721 - n_missing
    assert not readings.duplicated(["tag_name", "ts_utc"]).any()
    assert readings[["tag_name", "ts_utc", "value"]].notna().all().all()
    assert set(readings["tag_name"]) == set(HIERARCHY["tag_name"].dropna())
    assert readings["ts_utc"].between(GRID[0], GRID[-1]).all()


def test_injection_counts_match_config(backfill):
    _, truth, _ = backfill
    inj = CFG["injections"]
    per_well = Counter((i["well_key"], i["kind"]) for i in truth["injections"])
    for w in CFG["wells"]:
        key = w["well_key"]
        fixed = [a for (k, _), a in AGING.items() if k == key]
        runs = sum(a["frozen_runs"] for a in fixed) + (key == FAULT["well_key"])
        missing = sum(a["missing"] for a in fixed) + (FAULT["missing_hours"] if key == FAULT["well_key"] else 0)
        assert inj["out_of_range_per_well"][0] <= per_well[(key, "out_of_range")] <= inj["out_of_range_per_well"][1]
        assert inj["frozen_runs_per_well"][0] <= per_well[(key, "frozen")] - runs <= inj["frozen_runs_per_well"][1]
        assert inj["missing_per_well"][0] <= per_well[(key, "missing")] - missing <= inj["missing_per_well"][1]


def test_true_volume_is_petrinex_volume_plus_bias(backfill):
    _, truth, _ = backfill
    max_bias = CFG["generator"]["normal_volume_bias_max_pct"]
    for t in truth["wells"]:
        expected_bias = ANOMALIES.get(t["well_key"])
        if expected_bias is None:
            assert abs(t["volume_bias_pct"]) <= max_bias
        else:
            assert t["volume_bias_pct"] == expected_bias
        assert t["true_volume"] == pytest.approx(t["petrinex_volume"] * (1 + t["volume_bias_pct"] / 100), rel=1e-6)


def test_downtime_events_are_shut_in_and_do_not_overlap(backfill):
    readings, truth, _ = backfill
    by_tag = readings.set_index(["tag_name", "ts_utc"])["value"]
    wells = {w["well_key"]: w for w in CFG["wells"]}
    for key, events in pd.DataFrame(truth["downtime_events"]).groupby("well_key"):
        w = wells[key]
        spans = sorted((pd.Timestamp(e.start_utc), pd.Timestamp(e.end_utc)) for e in events.itertuples())
        assert all(prev_end < start for (_, prev_end), (start, _) in zip(spans, spans[1:]))
        total = int(events["hours"].sum())
        gap = 721 - w["petrinex_hours"]
        if gap >= CFG["downtime"]["min_event_hours"]:
            assert total == gap
        for start, end in spans:
            hours = pd.date_range(start, end, freq="h", inclusive="left")
            for code in ["RUN", f"FLOW_{w['product']}"]:
                vals = by_tag.loc[tag_name(CFG, w, code)].reindex(hours).dropna()
                assert (vals == 0).all(), (key, code, start)


# Quality flags --------------------------------------------------------------------------------------------------

def tiny(values, code="TP"):
    """One well, one tag, hourly values from the grid start (None = missing reading)."""
    cfg = copy.deepcopy(CFG)
    cfg["wells"] = [CFG["wells"][0] | {"product": "GAS"}]
    h = build_hierarchy(cfg)
    grid = GRID[:len(values)]
    name = tag_name(cfg, cfg["wells"][0], code)
    readings = pd.DataFrame({"tag_name": name, "ts_utc": grid, "value": values}).dropna()
    out = classify(readings, h, grid, cfg)
    return out[out["tag_name"] == name].reset_index(drop=True)


def test_quality_bad_for_missing_and_out_of_physical_range():
    out = tiny([3000.0, None, -9999.0, 25000.0, 3001.0])
    assert list(out["quality"]) == ["Good", "Bad", "Bad", "Bad", "Good"]
    assert list(out["quality_reason"]) == ["", "missing", "outside_physical_range", "outside_physical_range", ""]


def test_quality_questionable_outside_normal_range():
    out = tiny([3000.0, 12000.0, 3000.5])
    assert list(out["quality"]) == ["Good", "Questionable", "Good"]


def test_frozen_needs_min_points():
    n = CFG["quality"]["frozen_min_points"]
    frozen = tiny([3000.0] + [3100.0] * n + [3200.0])
    assert list(frozen["quality"]) == ["Good"] + ["Questionable"] * n + ["Good"]
    short = tiny([3000.0] + [3100.0] * (n - 1) + [3200.0])
    assert (short["quality"] == "Good").all()


def test_zero_flow_and_run_status_are_not_frozen():
    n = CFG["quality"]["frozen_min_points"]
    assert (tiny([0.0] * (n + 2), code="FLOW_GAS")["quality"] == "Good").all()
    assert (tiny([1.0] * (n + 2), code="RUN")["quality"] == "Good").all()


def test_missing_reading_breaks_a_frozen_run():
    n = CFG["quality"]["frozen_min_points"]
    out = tiny([3100.0] * (n - 1) + [None] + [3100.0] * (n - 1))
    assert set(out["quality"]) == {"Good", "Bad"}


def test_injected_bad_readings_are_flagged_exactly(backfill):
    _, truth, classified = backfill
    reasons = Counter(classified["quality_reason"])
    kinds = Counter(i["kind"] for i in truth["injections"])
    run_points = sum(i["hours"] + 1 for i in truth["injections"] if i["kind"] == "frozen")  # + the original value
    assert reasons["missing"] == kinds["missing"]
    assert reasons["outside_physical_range"] == kinds["out_of_range"]
    assert reasons["frozen"] == run_points
    assert reasons["outside_normal_range"] == 0
    assert len(classified) == 5 * len(CFG["wells"]) * 721            # flagged, not deleted


# Reconciliation -------------------------------------------------------------------------------------------------

def test_reconciliation_flags_exactly_the_anomaly_wells_and_the_sensor_fault(backfill):
    rec = reconcile(backfill[2], CFG)
    assert set(rec.loc[rec["needs_investigation"], "well_key"]) == set(ANOMALIES) | {FAULT["well_key"]}
    normal = rec[~rec["needs_investigation"]]
    assert normal["difference_pct"].abs().max() < CFG["reconciliation"]["threshold_pct"]
    for key, bias in ANOMALIES.items():
        assert rec.set_index("well_key").loc[key, "difference_pct"] == pytest.approx(bias, abs=1.0)


def test_likely_cause_separates_the_sensor_fault_from_production_mismatches(backfill):
    rec = reconcile(backfill[2], CFG).set_index("well_key")
    causes = CFG["reconciliation"]["likely_cause"]
    assert set(rec.index[rec["likely_cause"] == causes["sensor"]]) == {FAULT["well_key"]}
    assert set(rec.index[rec["likely_cause"] == causes["production"]]) == set(ANOMALIES)
    assert rec.loc[~rec["needs_investigation"], "likely_cause"].isna().all()


def test_sensor_fault_well_has_a_normal_true_volume_and_a_biased_estimate(backfill):
    _, truth, classified = backfill
    true_bias = next(w["volume_bias_pct"] for w in truth["wells"] if w["well_key"] == FAULT["well_key"])
    assert abs(true_bias) <= CFG["generator"]["normal_volume_bias_max_pct"]
    assert FAULT["well_key"] not in ANOMALIES and FAULT["well_key"] in WELLS
    rec = reconcile(classified, CFG).set_index("well_key").loc[FAULT["well_key"]]
    threshold = CFG["reconciliation"]
    assert abs(rec["difference_pct"]) > threshold["threshold_pct"]
    assert rec["flow_good_ratio_pct"] < threshold["sensor_good_ratio_threshold_pct"]


def test_aging_flow_tags_are_not_flagged_because_filling_covers_them(backfill):
    rec = reconcile(backfill[2], CFG).set_index("well_key")
    aging_flow = [k for k, c in AGING if c.startswith("FLOW_")]
    assert aging_flow
    assert not rec.loc[aging_flow, "needs_investigation"].any()


def test_health_differs_between_wells_and_tags_and_aging_tags_are_worst(backfill):
    classified = backfill[2].assign(good=lambda d: d["quality"].eq("Good"))
    by_tag = classified.groupby("tag_name")["good"].mean()
    names = {tag_name(CFG, WELLS[k], c) for k, c in AGING} | {tag_name(CFG, WELLS[FAULT["well_key"]], f"FLOW_{WELLS[FAULT['well_key']]['product']}")}
    assert set(by_tag.sort_values().index[:len(names)]) == names
    assert by_tag[list(names)].max() < by_tag.drop(list(names)).min()
    wells = classified.merge(HIERARCHY[["tag_name", "well_key"]], on="tag_name").groupby("well_key")["good"].mean()
    assert wells.nunique() > 10


def test_estimate_uses_good_flow_zero_when_shut_in_and_daily_mean_otherwise():
    cfg = copy.deepcopy(CFG)
    well = CFG["wells"][0] | {"product": "GAS", "petrinex_volume": 1.0}
    cfg["wells"] = [well]
    h = build_hierarchy(cfg)
    grid = GRID[:6]                                     # same local day
    flow = [48.0, 48.0, 0.0, -9999.0, None, 24.0]       # Good, Good, shut in, Bad, missing, Good
    run = [1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
    readings = pd.concat([
        pd.DataFrame({"tag_name": tag_name(cfg, well, "FLOW_GAS"), "ts_utc": grid, "value": flow}),
        pd.DataFrame({"tag_name": tag_name(cfg, well, "RUN"), "ts_utc": grid, "value": run})]).dropna()
    rec = reconcile(classify(readings, h, grid, cfg), cfg)
    day_mean = np.mean([48.0, 48.0, 24.0])
    assert rec.loc[0, "sensor_volume"] == pytest.approx((48 + 48 + 0 + day_mean + day_mean + 24) / 24)
    assert rec.loc[0, "hours_filled"] == 2
