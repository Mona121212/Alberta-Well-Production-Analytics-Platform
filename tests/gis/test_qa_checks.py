"""Rule tests with constructed rows: each check must flag the bad record and pass the good one.

Run: python -m pytest tests/gis
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
from pyproj import Transformer

from gis import qa_checks as checks
from gis.qa import evaluate

CRS = "EPSG:3400"
TO_3400 = Transformer.from_crs("EPSG:4269", CRS, always_xy=True)

DLS_PARAMS = {
    "label_column": "surface_location_dls",
    "label_pattern": r"^(\d{2})-(\d{2})-(\d{3})-(\d{2})W(\d)$",
    "base_latitude": 49.0,
    "meridian_longitudes": {4: -110.0, 5: -114.0, 6: -118.0},
    "township_height_deg": 0.0873,
    "range_width_m": 9790,
    "tolerance_cells": 0.25,
    "tolerance_sections": 0.5,
}


class FakeContext:
    def __init__(self, layers, references=None, boundary=None):
        self.crs = CRS
        self.record_keys = {"wells": "licence_number", "holes": "uwi"}
        self._layers, self._references = layers, references or {}
        self.boundary = boundary
        self.side_outputs = {}

    def layer(self, name):
        return self._layers[name]

    def reference(self, name):
        return self._references[name]


def wells_at(lonlat, **columns):
    x, y = TO_3400.transform(*zip(*lonlat))
    return gpd.GeoDataFrame(columns, geometry=gpd.points_from_xy(x, y), crs=CRS)


def test_section_numbering_round_trip():
    sections = np.arange(1, 37)
    row, col = checks.section_row_col(sections)
    assert list(checks.section_number(row, col)) == list(sections)
    # Section 1 is the south-east corner, 6 south-west, 7 above 6, 36 north-east.
    assert (row[0], col[0]) == (0, 0)
    assert (row[5], col[5]) == (0, 5)
    assert (row[6], col[6]) == (1, 5)
    assert (row[35], col[35]) == (5, 0)


def test_outside_boundary_uses_tolerance():
    boundary = shapely.box(0, 0, 1000, 1000)
    wells = gpd.GeoDataFrame({"licence_number": ["in", "near", "far"]},
                             geometry=gpd.points_from_xy([500, 1050, 1500], [500, 500, 500]), crs=CRS)
    result = checks.outside_boundary(FakeContext({"wells": wells}, boundary=boundary), "wells", {"tolerance_m": 100})
    assert result.rows_checked == 3
    assert list(result.failures["record_key"]) == ["far"]
    assert result.failures["metric"].iloc[0] == pytest.approx(500)


def test_latlong_mismatch_reports_metres():
    wells = wells_at([(-113.6, 53.4), (-113.6, 53.4)], licence_number=["ok", "shifted"],
                     latitude_nad83=[53.4, 53.4], longitude_nad83=[-113.6, -113.6])
    wells.loc[1, "geometry"] = shapely.Point(wells.geometry.x[1] + 10, wells.geometry.y[1])
    params = {"lat_column": "latitude_nad83", "lon_column": "longitude_nad83", "latlong_crs": "EPSG:4269",
              "tolerance_m": 1.0}
    result = checks.latlong_mismatch(FakeContext({"wells": wells}), "wells", params)
    assert list(result.failures["record_key"]) == ["shifted"]
    assert result.failures["metric"].iloc[0] == pytest.approx(10, abs=0.01)


def test_dls_township_range_flags_wrong_township():
    # Licence 0000001 is at 08-09-051-25W4 (53.386619, -113.617905).
    wells = wells_at([(-113.617905, 53.386619)] * 3, licence_number=["right", "wrong_twp", "bad_label"],
                     surface_location_dls=["08-09-051-25W4", "08-09-055-25W4", "8-9-51-25W4"])
    result = checks.dls_township_range(FakeContext({"wells": wells}), "wells", DLS_PARAMS)
    assert sorted(result.failures["record_key"]) == ["bad_label", "wrong_twp"]
    assert result.failures.set_index("record_key").loc["wrong_twp", "metric"] > 3


def test_dls_section_flags_wrong_section():
    wells = wells_at([(-113.617905, 53.386619)] * 2, licence_number=["right", "wrong_sec"],
                     surface_location_dls=["08-09-051-25W4", "08-27-051-25W4"])
    result = checks.dls_section(FakeContext({"wells": wells}), "wells", DLS_PARAMS)
    assert list(result.failures["record_key"]) == ["wrong_sec"]


def test_missing_reference_and_exclude():
    wells = wells_at([(-113.6, 53.4)] * 3, licence_number=["has_hole", "orphan", "cancelled"],
                     licence_status_group=["Active", "Active", "Cancelled"])
    refs = {"holes": pd.DataFrame({"licence_number": ["has_hole"]})}
    params = {"key": "licence_number", "reference_layers": ["holes"],
              "exclude": {"column": "licence_status_group", "values": ["Cancelled"]}}
    result = checks.missing_reference(FakeContext({"wells": wells}, refs), "wells", params)
    assert result.rows_checked == 2
    assert list(result.failures["record_key"]) == ["orphan"]


def test_duplicate_key_uses_full_reference():
    holes = wells_at([(-113.6, 53.4)] * 2, uwi=["A", "B"], licence_number=["1", "2"])
    refs = {"holes": pd.DataFrame({"uwi": ["A", "B", "B"]})}  # B is duplicated outside the sample
    result = checks.duplicate_key(FakeContext({"holes": holes}, refs), "holes", {"key": "uwi", "reference_layer": "holes"})
    assert list(result.failures["record_key"]) == ["B"]


def test_zero_length_2d_vs_3d():
    vertical = shapely.LineString([(0, 0, 800), (0, 0, -400)])
    point_like = shapely.LineString([(5, 5, 800), (5, 5, 800)])
    deviated = shapely.LineString([(0, 0, 800), (300, 0, -400)])
    traj = gpd.GeoDataFrame({"uwi": ["vertical", "point_like", "deviated"], "licence_number": ["1", "2", "3"],
                             "geometry_source": "Calculated"}, geometry=[vertical, point_like, deviated], crs=CRS)
    ctx = FakeContext({"holes": traj})
    assert list(checks.zero_length(ctx, "holes", {"dimension": "3d"}).failures["record_key"]) == ["point_like"]
    assert list(checks.zero_length(ctx, "holes", {"dimension": "2d"}).failures["record_key"]) == ["vertical", "point_like"]


def test_fail_severity_respects_allowlist():
    two_failures = checks.CheckResult(gpd.GeoDataFrame({"record_key": ["a", "b"], "licence_number": ["a", "b"]}),
                                      rows_checked=10)
    no_failures = checks.CheckResult(gpd.GeoDataFrame({"record_key": [], "licence_number": []}), rows_checked=10)
    both = [{"licence_number": "a", "reason": "r"}, {"licence_number": "b", "reason": "r"}]
    assert evaluate({"severity": "FAIL"}, two_failures) == "FAIL"
    assert evaluate({"severity": "FAIL", "allowlist": both}, two_failures) == "WARN"
    assert evaluate({"severity": "FAIL", "allowlist": both[:1]}, two_failures) == "FAIL"
    assert evaluate({"severity": "WARN"}, two_failures) == "WARN"
    assert evaluate({"severity": "FAIL"}, no_failures) == "PASS"


def test_date_out_of_range():
    wells = gpd.GeoDataFrame({"licence_number": ["ok", "future", "ancient"],
                              "spud_date": pd.to_datetime(["2001-01-01", "2001-01-01", "1850-06-01"]),
                              "licence_status_date": pd.to_datetime(["2005-01-01", "2099-01-01", "2005-01-01"])},
                             geometry=gpd.points_from_xy([0, 1, 2], [0, 0, 0]), crs=CRS)
    params = {"columns": ["spud_date", "licence_status_date"], "min_date": "1880-01-01", "max_date": "today"}
    result = checks.date_out_of_range(FakeContext({"wells": wells}), "wells", params)
    assert sorted(result.failures["record_key"]) == ["ancient", "future"]
    assert "licence_status_date = 2099-01-01" in result.failures.set_index("record_key").loc["future", "detail"]
