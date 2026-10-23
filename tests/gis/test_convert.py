"""Standardization tests: licence status mapping, date parsing, text cleaning. Run: python -m pytest tests/gis"""
import geopandas as gpd
import pandas as pd

from gis import convert
from gis.common import repo_path

STATUS_MAP = pd.read_csv(repo_path("config/gis_licence_status_map.csv"), dtype=str)


def frame(**cols):
    n = len(next(iter(cols.values())))
    return gpd.GeoDataFrame(cols, geometry=gpd.points_from_xy(range(n), [0] * n), crs="EPSG:3400")


def test_status_mapping_covers_every_raw_value_and_flags_new_ones():
    raw = STATUS_MAP["raw_value"].tolist() + ["Brand New Status"]
    out = convert.map_licence_status(frame(licence_status_raw=raw), STATUS_MAP, "test")
    mapped = out.set_index("licence_status_raw")
    assert mapped.loc["Re-entered", "licence_status"] == mapped.loc["Re-Entered", "licence_status"] == "Re-Entered"
    assert mapped.loc["RecExempt", "licence_status_group"] == "Reclaimed"
    assert mapped.loc["Suspension", "licence_status"] == "Suspended"
    assert mapped.loc["Brand New Status", "licence_status"] == convert.UNMAPPED_STATUS
    cols = list(out.columns)
    at = cols.index("licence_status_raw")
    assert cols[at + 1:at + 3] == ["licence_status", "licence_status_group"]


def test_status_map_has_unique_raw_values():
    assert not STATUS_MAP["raw_value"].duplicated().any()


def test_parse_dates_per_column_format():
    gdf = frame(a=["07/05/1949", "bad", None], b=["19490528", "20260101", ""],
                c=pd.to_datetime(["1998-12-31 12:00", "2025-09-30 21:51", None], utc=True))
    out = convert.parse_dates(convert.clean_text(gdf), {"a": "%m/%d/%Y", "b": "%Y%m%d", "c": "datetime"}, "test")
    assert str(out["a"].iloc[0]) == "1949-07-05" and pd.isna(out["a"].iloc[1])
    assert str(out["b"].iloc[0]) == "1949-05-28" and pd.isna(out["b"].iloc[2])
    assert str(out["c"].iloc[1]) == "2025-09-30"


def test_clean_text_trims_and_nulls_empty_strings():
    out = convert.clean_text(frame(licence_number=[" 0065081", "0000001  ", "   "]))
    assert out["licence_number"].tolist()[:2] == ["0065081", "0000001"]
    assert pd.isna(out["licence_number"].iloc[2])
