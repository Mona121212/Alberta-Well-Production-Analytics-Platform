"""Rule tests for the remaining QA checks, the injected-bad-row demo and rule/test coverage.
Run: python -m pytest tests/gis"""
import pathlib

import geopandas as gpd
import pandas as pd
import pytest
import shapely
import yaml
from pyproj import Transformer

from gis import qa_checks as checks
from gis.common import repo_path
from gis.qa import INJECTED_LICENCE, evaluate, inject_bad_row

CRS = "EPSG:3400"


class Context:
    def __init__(self, layers=None, references=None, excel=None, boundary=None):
        self.crs = CRS
        self.record_keys = {"wells": "licence_number", "traj": "uwi", "strings": "uwi"}
        self._layers, self._references = layers or {}, references or {}
        self.excel_sample, self.boundary = excel, boundary
        self.side_outputs = {}

    def layer(self, name):
        return self._layers[name]

    def reference(self, name):
        return self._references[name]

    @staticmethod
    def repo_path(relative):
        return repo_path(relative)


def test_trajectory_endpoint_start():
    surface = gpd.GeoDataFrame({"licence_number": ["L1", "L2"], "uwi": ["s1", "s2"]},
                               geometry=gpd.points_from_xy([0, 1000], [0, 0]), crs=CRS)
    traj = gpd.GeoDataFrame({"uwi": ["t1", "t2"], "licence_number": ["L1", "L2"]},
                            geometry=[shapely.LineString([(0, 0, 900), (500, 0, -500)]),
                                      shapely.LineString([(1200, 0, 900), (1500, 0, -500)])], crs=CRS)
    params = {"endpoint": "start", "target_layer": "wells", "join_key": "licence_number", "tolerance_m": 50}
    result = checks.trajectory_endpoint(Context({"wells": surface, "traj": traj}), "traj", params)
    assert result.rows_checked == 2
    assert list(result.failures["record_key"]) == ["t2"]          # starts 200 m from its surface hole
    assert result.failures["metric"].iloc[0] == pytest.approx(200)
    assert result.failures.geometry.iloc[0].geom_type == "LineString"


def test_pattern_mismatch_and_status_conflict():
    strings = gpd.GeoDataFrame({
        "uwi": ["a", "b", "c", "d"], "licence_number": ["1", "2", "3", "4"],
        "status_mode": ["2", "FLOWING", "PUMPING", "ABANDONED ZONE"],
        "licence_status": ["Issued", "Abandoned", "Issued", "Abandoned"],
        "licence_status_group": ["Active", "Abandoned", "Active", "Abandoned"]},
        geometry=gpd.points_from_xy([0, 1, 2, 3], [0, 0, 0, 0]), crs=CRS)
    ctx = Context({"strings": strings})
    text = checks.pattern_mismatch(ctx, "strings", {"column": "status_mode", "pattern": r"^\d+$"})
    assert sorted(text.failures["record_key"]) == ["b", "c", "d"]
    conflict = checks.status_conflict(ctx, "strings", {
        "mode_column": "status_mode", "producing_modes": ["FLOWING", "PUMPING"],
        "licence_group_column": "licence_status_group", "closed_groups": ["Abandoned", "Reclaimed"]})
    assert list(conflict.failures["record_key"]) == ["b"]         # flowing on an abandoned licence


def test_source_compare_one_row_per_record_and_value_pairs():
    gdb = pd.DataFrame({"uwi": ["a", "b", "c"], "licence_number": ["1", "2", "3"],
                        "status_mode": ["2", "3", "0"], "licensee": ["X", "Y", "Z"]})
    excel = pd.DataFrame({"uwi": ["a", "b", "z"], "licence_number": ["1", "2", "9"],
                          "status_mode": ["ABANDONED", "3", "0"], "licensee": ["X", "Q", "Z"]})
    ctx = Context(references={"strings": gdb}, excel=excel)
    result = checks.source_compare(ctx, "strings", {"key": "uwi", "columns": ["status_mode", "licensee"]})
    assert result.rows_checked == 2                               # "z" is not in the GDB
    details = result.failures.set_index("record_key")["detail"]
    assert details["a"] == "status_mode: gdb='2' excel='ABANDONED'"
    assert details["b"] == "licensee: gdb='Y' excel='Q'"
    pairs = ctx.side_outputs["gdb_vs_excel_value_pairs"]
    assert set(map(tuple, pairs[["column", "gdb_value", "excel_value"]].to_numpy())) == {
        ("status_mode", "2", "ABANDONED"), ("licensee", "Y", "Q")}


def test_missing_and_accepted_values():
    wells = gpd.GeoDataFrame({"licence_number": ["ok", "no_status", None],
                              "licence_status": ["Issued", None, "Martian"]},
                             geometry=[shapely.Point(0, 0), shapely.Point(1, 1), None], crs=CRS)
    ctx = Context({"wells": wells})
    missing = checks.missing_values(ctx, "wells", {"columns": ["licence_number", "geometry", "licence_status"]})
    assert missing.failures["detail"].tolist() == ["missing: licence_status", "missing: licence_number, geometry"]
    accepted = checks.accepted_values(ctx, "wells", {"column": "licence_status",
                                                     "values_file": "config/gis_licence_status_map.csv",
                                                     "values_column": "licence_status"})
    assert accepted.failures["detail"].tolist() == [
        "licence_status = 'Martian' not in config/gis_licence_status_map.csv"]


def test_injected_bad_row_trips_a_fail_rule():
    x, y = Transformer.from_crs("EPSG:4269", CRS, always_xy=True).transform(-113.6, 53.4)
    wells = gpd.GeoDataFrame({"licence_number": ["ok"], "latitude_nad83": [53.4], "longitude_nad83": [-113.6]},
                             geometry=gpd.points_from_xy([x], [y]), crs=CRS)
    boundary = shapely.box(x - 1e5, y - 1e5, x + 1e5, y + 1e5)
    ctx = Context({"wells": inject_bad_row(wells, east_edge_x=boundary.bounds[2])}, boundary=boundary)
    params = {"lat_column": "latitude_nad83", "lon_column": "longitude_nad83", "latlong_crs": "EPSG:4269",
              "tolerance_m": 1.0}
    result = checks.latlong_mismatch(ctx, "wells", params)
    assert list(result.failures["record_key"]) == [INJECTED_LICENCE]
    assert evaluate({"severity": "FAIL", "max_failures": 0}, result) == "FAIL"
    outside = checks.outside_boundary(ctx, "wells", {"tolerance_m": 100})
    assert list(outside.failures["record_key"]) == [INJECTED_LICENCE]


def test_every_check_is_tested_and_every_rule_has_a_check():
    with open(repo_path("config/gis_qa.yaml"), encoding="utf-8") as fh:
        rules = yaml.safe_load(fh)["rules"]
    assert {r["check"] for r in rules} <= set(checks.CHECKS)
    assert len({r["id"] for r in rules}) == len(rules)
    source = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path(repo_path("tests/gis")).glob("test_*.py"))
    untested = [name for name in checks.CHECKS if f"checks.{name}(" not in source]
    assert untested == [], f"QA checks without a test: {untested}"
