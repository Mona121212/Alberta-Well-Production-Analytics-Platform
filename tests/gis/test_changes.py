"""Change detection tests with constructed snapshots. Run: python -m pytest tests/gis"""
import geopandas as gpd
import pandas as pd
from shapely import Point

from gis import changes as C

CCFG = C.load_changes_config()


def snapshot(n=40):
    return gpd.GeoDataFrame({
        "licence_number": [f"{i:07d}" for i in range(n)],
        "licensee": ["Acme Ltd."] * n,
        "licence_status": ["Issued", "Abandoned"] * (n // 2),
        "licence_status_group": ["Active", "Abandoned"] * (n // 2),
        "licence_status_date": pd.Timestamp("2020-01-01"),
        "spud_date": pd.Timestamp("2010-01-01"),
        "surface_location_dls": "01-01-001-01W4",
    }, geometry=gpd.points_from_xy(range(0, n * 1000, 1000), [0] * n), crs="EPSG:3400")


def test_compare_finds_each_change_type():
    old = snapshot()
    new = old.copy()
    new.loc[0, "licence_status"] = "Suspended"
    new.loc[1, "licensee"] = "Buyer Inc."
    new.loc[2, "geometry"] = Point(2000 + 300, 0)  # moved 300 m
    new.loc[3, "geometry"] = Point(3000.4, 0)      # 0.4 m: below tolerance
    new.loc[4, "surface_location_dls"] = "02-01-001-01W4"
    new = pd.concat([new.drop(index=5), new.loc[[6]].assign(licence_number="NEW0001")], ignore_index=True)
    changelog, moves = C.compare(old, gpd.GeoDataFrame(new, crs=old.crs), CCFG)
    found = changelog.groupby("change_type")["licence_number"].apply(sorted).to_dict()
    assert found == {"status_change": ["0000000"], "licensee_transfer": ["0000001"],
                     "moved_location": ["0000002"], "dls_label_change": ["0000004"],
                     "removed_licence": ["0000005"], "new_licence": ["NEW0001"]}
    row = changelog.set_index("change_type").loc["status_change"]
    assert (row["old_value"], row["new_value"]) == ("Issued", "Suspended")
    assert moves["distance_m"].tolist() == [300.0]


def test_synthetic_snapshot_round_trip():
    old = snapshot(200)
    new, expected = C.synthetic_snapshot(old, CCFG)
    changelog, _ = C.compare(old, new, CCFG)
    assert changelog["change_type"].value_counts().to_dict() == expected


def test_identical_snapshots_have_no_changes():
    old = snapshot()
    changelog, moves = C.compare(old, old.copy(), CCFG)
    assert changelog.empty and moves.empty
