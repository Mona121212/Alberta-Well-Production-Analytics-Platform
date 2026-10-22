"""Spatial analysis tests with constructed geometry. Run: python -m pytest tests/gis"""
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from gis import analysis as A

ACFG = A.load_analysis_config()


def test_hex_index_point_falls_inside_its_hexagon():
    rng = np.random.default_rng(7)
    x, y = rng.uniform(-50_000, 50_000, 2000), rng.uniform(-50_000, 50_000, 2000)
    size = 5000 / np.sqrt(3)
    q, r = A.hex_index(x, y, size)
    polys = A.hex_polygons(q, r, size)
    assert shapely.covers(polys, shapely.points(x, y)).all()
    assert np.allclose(shapely.area(polys), 1.5 * np.sqrt(3) * size ** 2)


def wells_frame(xy, **cols):
    base = {"licence_number": [f"L{i}" for i in range(len(xy))], "licensee": "A", "well_class": "active_reporting",
            "licence_status_group": "Active", "spud_date": pd.Timestamp("2010-01-01"),
            "surface_location_dls": "01-01-001-01W4", "township": "001-01W4"}
    base.update({c: 1.0 for c in ACFG["volume_columns"]})
    base.update(cols)
    return gpd.GeoDataFrame(base, geometry=gpd.points_from_xy(*zip(*xy)), crs="EPSG:3400")


def test_township_polygon_hull_or_buffer():
    wells = wells_frame([(0, 0), (5000, 5000), (1000, 0), (0, 1000)],   # groups not sorted on purpose
                        township=["T1", "T2", "T1", "T1"])
    town = A.townships(wells, ACFG).set_index("township")
    assert town.loc["T1", "geometry"].geom_type == "Polygon"
    assert town.loc["T1", "polygon_method"] == "convex hull of wells"
    assert town.loc["T2", "polygon_method"].startswith("buffer")
    assert town.loc["T1", "wells"] == 3 and town.loc["T1", "oil_m3_12m"] == 3.0


def test_pads_distribution_and_sizes():
    wells = wells_frame([(0, 0), (20, 0), (40, 0), (5000, 0)])
    layer, dist, largest = A.pads(wells, 50, ACFG)
    assert sorted(layer["wells"]) == [1, 3]
    assert dist.set_index("wells_per_pad").loc["1", "pads"] == 1
    assert largest.iloc[0]["wells"] == 3


def test_lateral_classes():
    wells = wells_frame([(0, 0), (0, 0), (0, 0)], licence_number=["V", "D", "H"])
    bottom = gpd.GeoDataFrame({"uwi": ["v", "d", "h"], "licence_number": ["V", "D", "H"],
                               "spud_date": pd.Timestamp("2015-01-01"), "licensee": "A"},
                              geometry=gpd.points_from_xy([10, 200, 2000], [0, 0, 0]), crs="EPSG:3400")
    traj = gpd.GeoDataFrame({"uwi": ["h"]}, geometry=[shapely.LineString([(0, 0), (2000, 0)])], crs="EPSG:3400")
    layer, trend = A.laterals(wells, bottom, traj, ACFG)
    assert layer.set_index("uwi")["lateral_class"].to_dict() == {"v": "vertical", "d": "deviated", "h": "horizontal"}
    assert layer.set_index("uwi").loc["h", "trajectory_2d_m"] == 2000
    assert trend.iloc[0]["wellbores"] == 3
