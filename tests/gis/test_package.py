"""Client package tests: dictionary coverage, style XML, package build on constructed data.
Run: python -m pytest tests/gis"""
import sqlite3
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
import pytest
import shapely

from gis import package as P
from gis import styles

PCFG = P.load_package_config()
STYLE_CFG = styles.load_style_config()
DICTIONARY = P.load_dictionary()


def test_every_packaged_field_has_a_description():
    fields = PCFG["wells_fields"] + PCFG["pads_fields"] + PCFG["qa_fields"] + ["x_epsg3400", "y_epsg3400",
                                                                                  "selection_type", "selection_value",
                                                                                  "area_km2", "province_uid",
                                                                                  "province_name", "land_area_km2"]
    assert sorted(set(fields) - set(DICTIONARY)) == []


@pytest.mark.parametrize("layer, geometry", [("wells", "point"), ("pads", "point"),
                                             ("package_extent", "polygon"), ("alberta_boundary", "polygon")])
def test_qml_is_valid_xml_with_renderer(layer, geometry):
    qml = styles.qml_for_layer(layer, geometry, STYLE_CFG)
    root = ET.fromstring(qml.split("\n", 1)[1])
    renderer = root.find("renderer-v2")
    assert renderer is not None
    if STYLE_CFG["layers"][layer]["type"] == "categorized":
        palette = STYLE_CFG["palettes"][STYLE_CFG["layers"][layer]["palette"]]
        assert len(renderer.find("categories")) == len(palette) + 1  # plus "other"


class FakeSources:
    def __init__(self):
        n = 6
        self.wells = gpd.GeoDataFrame({f: None for f in PCFG["wells_fields"]} | {
            "licence_number": [f"000000{i}" for i in range(n)],
            "licensee": ["Acme Ltd."] * 4 + ["Other Inc."] * 2,
            "township": ["001-01W4"] * 3 + ["002-01W4"] * 3,
            "well_class": ["active_reporting", "abandoned", "reclaimed", "suspended", "active_reporting", "abandoned"],
            "inactive_candidate_6m": False, "inactive_candidate_12m": False, "qa_pass": True,
            "pad_id": ["PAD-0000000", "PAD-0000000", "PAD-0000002", "PAD-0000003", "PAD-0000004", "PAD-0000005"],
        }, geometry=gpd.points_from_xy([0, 20, 900, 5000, 9000, 9500], [0] * n), crs="EPSG:3400")
        self.pads = gpd.GeoDataFrame({f: 1 for f in PCFG["pads_fields"] if f != "wells_in_package"} | {
            "pad_id": [f"PAD-000000{i}" for i in (0, 2, 3, 4, 5)], "wells": [2, 1, 1, 1, 1]},
            geometry=gpd.points_from_xy([10, 900, 5000, 9000, 9500], [0] * 5), crs="EPSG:3400")
        self.failures = pd.DataFrame({c: ["x"] for c in PCFG["qa_fields"]} | {
            "licence_number": ["0000001"], "metric": [1.0], "rule_id": ["GQA-05"], "severity": ["WARN"]})
        self.boundary = gpd.GeoDataFrame({"province_uid": ["48"], "province_name": ["Alberta"],
                                          "land_area_km2": [1.0]},
                                         geometry=[shapely.box(-1e5, -1e5, 1e5, 1e5)], crs="EPSG:3400")
        self.petrinex_months = ("2025-07", "2026-06")
        self.st37_latest_status = pd.Timestamp("2025-09-30").date()


def test_build_licensee_package(tmp_path):
    out = P.build(FakeSources(), PCFG, STYLE_CFG, DICTIONARY, root=tmp_path, licensee="acme ltd.")  # any case
    assert sorted(p.name for p in out.iterdir()) == sorted([
        "README.md", "data.gpkg", "data_dictionary.md", "wells.csv", "wells.qml", "pads.qml",
        "package_extent.qml", "alberta_boundary.qml"])
    wells = gpd.read_file(out / "data.gpkg", layer="wells")
    assert len(wells) == 4 and set(wells["licensee"]) == {"Acme Ltd."}
    pads = gpd.read_file(out / "data.gpkg", layer="pads")
    assert pads.set_index("pad_id").loc["PAD-0000000", "wells_in_package"] == 2
    with sqlite3.connect(out / "data.gpkg") as con:
        styled = {r[0] for r in con.execute("SELECT f_table_name FROM layer_styles WHERE useAsDefault = 1")}
        registered = con.execute("SELECT count(*) FROM gpkg_contents WHERE table_name = 'layer_styles'").fetchone()[0]
    assert styled == {"wells", "pads", "package_extent", "alberta_boundary"} and registered == 1
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "EPSG:3400" in readme and "2026-06" in readme and "GQA-05" in readme


def test_township_selection_and_unknown_licensee(tmp_path):
    src = FakeSources()
    chosen, kind, value, _ = P.select(src, townships="002-01w4")
    assert kind == "townships" and len(chosen) == 3
    with pytest.raises(SystemExit, match="Close matches"):
        P.select(src, licensee="Acme Limited")
