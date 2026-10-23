"""Liability tests with constructed rows. Run: python -m pytest tests/gis"""
import geopandas as gpd
import pandas as pd
import pytest

from gis import liability as L

LCFG = L.load_liability_config()
AS_OF = pd.Period("2026-06", freq="M")


def test_uwi_to_well_key_matches_silver_rule():
    uwi = pd.Series(["00/06-06-001-01W4/0", "F1/06-06-001-01W4/2", "AA/16-36-126-14W6/10", "bad"])
    keys = L.uwi_to_well_key(uwi, LCFG["uwi_pattern"])
    assert keys[:3].tolist() == ["100060600101W400", "1F1060600101W402", "1AA163612614W610"]
    assert pd.isna(keys[3])


def petrinex_rows(rows):
    df = pd.DataFrame(rows, columns=["well_key", "ProductionMonth", "ActivityID", "ProductID", "Volume", "Hours"])
    df["month"] = pd.PeriodIndex(df["ProductionMonth"], freq="M")
    df["volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df["hours"] = pd.to_numeric(df["Hours"], errors="coerce")
    return df


def test_well_activity_applies_gold_rules():
    pet = petrinex_rows([
        ("100060600101W400", "2026-06", "PROD", "OIL", "10", "100"),     # producing
        ("100070600101W400", "2026-06", "PROD", "OIL", "10", "9999"),    # hours above month max: rejected
        ("100080600101W400", "2026-06", "INJ", "WATER", "50", "720"),    # injector: activity, not production
        ("100090600101W400", "2026-06", "SHUTIN", "OIL", "0", "0"),      # reported, no volume
        ("100100600101W400", "2026-06", "PROD", "OIL", "***", "100"),    # masked volume
    ])
    act = L.well_activity(pet, LCFG, AS_OF - 11).set_index("well_key")
    assert act.loc["100060600101W400", "last_prod_month"] == AS_OF
    assert pd.isna(act.loc["100070600101W400", "last_prod_month"])
    assert pd.isna(act.loc["100080600101W400", "last_prod_month"])
    assert act.loc["100080600101W400", "last_activity_month"] == AS_OF
    assert pd.isna(act.loc["100090600101W400", "last_activity_month"])
    assert act.loc["100090600101W400", "last_report_month"] == AS_OF
    assert pd.isna(act.loc["100100600101W400", "last_activity_month"])


def test_match_reasons():
    activity = pd.DataFrame({
        "well_key": ["100060600101W400", "100060600101W402", "100990600101W400", "100070600101W400"],
        "last_prod_month": [AS_OF] * 4, "key_valid": True, "oil_m3_12m": 1.0})
    key_map = pd.DataFrame({"well_key": ["100060600101W400", "100070600101W400"], "uwi": ["a", "b"],
                            "licence_number": ["L1", "L2"]})
    producing, summary = L.match_petrinex(activity, key_map, {"L1"}, AS_OF - 11, ["oil_m3_12m"])
    status = producing.set_index("well_key")["match_status"]
    assert status["100060600101W400"].startswith("matched")
    assert status["100060600101W402"] == "unmatched: location found, different event sequence"
    assert status["100990600101W400"] == "unmatched: not in ST37 production strings or bottom holes"
    assert status["100070600101W400"] == "unmatched: UWI found, licence not in surface holes"
    assert summary["wells"].sum() == 4


@pytest.mark.parametrize("group, spud, last_activity, expected", [
    ("Active", "2000-01-01", "2026-05", "active_reporting"),
    ("Active", "2000-01-01", "2025-10", "active_no_activity_6m"),
    ("Active", "2000-01-01", None, "active_no_activity_12m"),
    ("Active", "2026-04-01", None, "active_recently_spudded"),
    ("Active", "2025-09-01", None, "active_no_activity_6m"),   # spudded inside the 12-month window
    ("Active", None, None, "active_not_spudded"),
    ("Inactive", "2000-01-01", None, "suspended"),
    ("Abandoned", "1980-01-01", None, "abandoned"),
])
def test_classify(group, spud, last_activity, expected):
    wells = gpd.GeoDataFrame({
        "licence_number": ["L1"], "licence_status_group": [group], "spud_date": [pd.Timestamp(spud) if spud else None],
        "licence_status_date": [pd.Timestamp("2010-01-01")], "surface_location_dls": ["08-09-051-25W4"]},
        geometry=gpd.points_from_xy([0], [0]), crs="EPSG:3400")
    lic = pd.DataFrame({"licence_number": ["L1"], "petrinex_well_keys": [1],
                        "last_report_month": pd.array([AS_OF], dtype="period[M]"),
                        "last_prod_month": pd.array([None], dtype="period[M]"),
                        "last_activity_month": pd.array([pd.Period(last_activity, "M") if last_activity else None],
                                                        dtype="period[M]")})
    result = L.classify(wells, lic, LCFG, AS_OF, pad_distance=50)
    assert result["well_class"].iloc[0] == expected
    assert result["township"].iloc[0] == "051-25W4"


def test_pad_review_class_only_for_large_never_reported_pads():
    n = 12
    xs = [i * 5 for i in range(n)] + [10_000] + [50_000 + i * 5 for i in range(n)]
    wells = gpd.GeoDataFrame({
        "licence_number": [f"B{i:02d}" for i in range(n)] + ["S1"] + [f"C{i:02d}" for i in range(n)],
        "licence_status_group": "Active",
        "well_class": "active_no_activity_12m",
        "in_petrinex": [False] * (2 * n) + [True],
    }, geometry=gpd.points_from_xy(xs, [0] * len(xs)), crs="EPSG:3400")
    review = LCFG["pad_review"]
    out = L.apply_pad_review(wells, 50, review).set_index("licence_number")
    assert (out.loc[[f"B{i:02d}" for i in range(n)], "well_class"] == review["well_class"]).all()
    assert out.loc["S1", "well_class"] == "active_no_activity_12m"      # single-well pad
    assert (out.loc[[f"C{i:02d}" for i in range(n - 1)], "well_class"] == "active_no_activity_12m").all()  # pad reported
    assert out.loc["B00", "pad_licences"] == n


def test_cluster_points_chains_within_distance():
    import shapely
    from gis.spatial import cluster_points
    labels = cluster_points(shapely.points([(0, 0), (40, 0), (80, 0), (500, 0)]), 50)
    assert labels[0] == labels[1] == labels[2] != labels[3]


def test_township_key_from_dls_label():
    labels = pd.Series(["08-09-051-25W4", "16-36-110-13W6", "8-9-51-25W4", None])
    assert L.township_key(labels).tolist()[:2] == ["051-25W4", "110-13W6"]
    assert L.township_key(labels).isna().tolist()[2:] == [True, True]
