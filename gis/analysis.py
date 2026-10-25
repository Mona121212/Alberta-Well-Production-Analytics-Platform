"""Spatial analysis on the standardized and liability layers.

1. Townships: DLS township key, convex-hull polygon of its wells, counts by status, 12-month volumes
2. 5 km hexagon grid: well, active, inactive, abandoned-not-reclaimed counts and 12-month volumes
3. Multi-well pads: surface holes within pads.distance_m, pad size distribution, largest pads
4. Laterals: surface-to-bottom-hole offset and trajectory 2D length per wellbore, trend by spud year

Outputs in gis/output/<mode>/analysis/: analysis.gpkg (townships, hexagons, pads, wellbore_laterals)
CSV summaries and pad_membership.parquet (licence -> pad, used by client packages).

Usage:
    python -m gis.analysis [--mode sample|full]
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
import yaml

from gis.common import REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir, reset_gpkg
from gis.spatial import assign_pads

log = get_logger("gis.analysis")
DEFAULT_ANALYSIS_CONFIG = REPO_ROOT / "config" / "gis_analysis.yaml"
SQRT3 = np.sqrt(3.0)


class ReconciliationFailed(Exception):
    """Raised when analysis layers do not reconcile to the drilled-well count or volumes."""


def load_inputs(cfg):
    out = output_dir(cfg)
    gpkg = out / cfg["exports"]["geopackage"]
    wells = pyogrio.read_dataframe(out / "liability" / "liability.gpkg", layer="wells_liability", use_arrow=True)
    bottom = pyogrio.read_dataframe(gpkg, layer=cfg["layers"]["bottom_holes"]["output_layer"], use_arrow=True,
                                    columns=["uwi", "licence_number", "spud_date", "licensee"])
    traj = pyogrio.read_dataframe(gpkg, layer=cfg["layers"]["well_trajectories"]["output_layer"], use_arrow=True,
                                  columns=["uwi"])
    boundary = pyogrio.read_dataframe(gpkg, layer=cfg["boundary"]["output_layer"]).geometry.union_all()
    return wells, bottom, traj, boundary


def drilled(wells, acfg):
    f = acfg["drilled_filter"]
    keep = ~wells["licence_status_group"].isin(f["exclude_status_groups"])
    if f["require_spud_date"]:
        keep &= wells["spud_date"].notna()
    return wells[keep].copy()


def class_flags(wells, acfg):
    return pd.DataFrame({name: wells["well_class"].isin(classes) for name, classes in acfg["class_counts"].items()})


def summarize(wells, key, acfg):
    """Counts by class and volumes per group key."""
    flags = class_flags(wells, acfg).astype(int)
    table = pd.concat([wells[[key]], flags, wells[acfg["volume_columns"]].fillna(0)], axis=1)
    summary = table.groupby(key).sum()
    summary[acfg["volume_columns"]] = summary[acfg["volume_columns"]].round(1)
    summary.insert(0, "wells", wells.groupby(key).size())
    return summary


def grouped_hulls(points, key):
    """Convex hull of the points in each group, vectorized (one multipoint per group)."""
    codes, groups = pd.factorize(points[key], sort=True)
    order = np.argsort(codes, kind="stable")  # shapely needs indices sorted
    coords = shapely.get_coordinates(points.geometry.to_numpy()[order])
    multipoints = shapely.multipoints(coords, indices=codes[order])
    return gpd.GeoSeries(shapely.convex_hull(multipoints), index=groups, crs=points.crs)


# --- 1. Townships -------------------------------------------------------------------------

def townships(wells, acfg):
    w = wells.dropna(subset=["township"])
    summary = summarize(w, "township", acfg)
    status = w.pivot_table(index="township", columns="licence_status_group", values="licence_number",
                           aggfunc="count", fill_value=0)
    status.columns = [f"status_{c.lower()}" for c in status.columns]
    summary = summary.join(status)

    tcfg = acfg["township"]
    hulls = grouped_hulls(w, "township")
    small = (summary["wells"] < tcfg["min_wells_for_hull"]) | (hulls.geom_type != "Polygon")
    hulls[small] = hulls[small].buffer(tcfg["point_buffer_m"])
    summary["polygon_method"] = np.where(small, f"buffer {tcfg['point_buffer_m']} m", "convex hull of wells")
    return gpd.GeoDataFrame(summary.reset_index(), geometry=hulls.reindex(summary.index).to_numpy(), crs=wells.crs)


# --- 2. Hexagon grid ----------------------------------------------------------------------

def hex_index(x, y, size):
    """Pointy-top axial (q, r) of the hexagon containing each point; size = centre-to-vertex."""
    q = (SQRT3 / 3 * x - y / 3) / size
    r = (2 / 3 * y) / size
    s = -q - r
    rq, rr, rs = np.round(q), np.round(r), np.round(s)
    dq, dr, ds = np.abs(rq - q), np.abs(rr - r), np.abs(rs - s)
    fix_q = (dq > dr) & (dq > ds)
    fix_r = ~fix_q & (dr > ds)
    rq = np.where(fix_q, -rr - rs, rq)
    rr = np.where(fix_r, -rq - rs, rr)
    return rq.astype(np.int64), rr.astype(np.int64)


def hex_polygons(q, r, size):
    cx = size * (SQRT3 * q + SQRT3 / 2 * r)
    cy = size * 1.5 * r
    angles = np.radians(30 + 60 * np.arange(6))
    xs = cx[:, None] + size * np.cos(angles)[None, :]
    ys = cy[:, None] + size * np.sin(angles)[None, :]
    rings = np.stack([xs, ys], axis=-1)
    return shapely.polygons(rings)


def hexagons(wells, boundary, crs, acfg):
    size = acfg["hexagons"]["width_m"] / SQRT3
    # Every cell whose centre is near the province, then keep cells touching the boundary.
    minx, miny, maxx, maxy = boundary.bounds
    corner_q, corner_r = hex_index(np.array([minx, maxx, minx, maxx]), np.array([miny, miny, maxy, maxy]), size)
    qq, rr = np.meshgrid(np.arange(corner_q.min() - 2, corner_q.max() + 3),
                         np.arange(corner_r.min() - 2, corner_r.max() + 3))
    grid = pd.DataFrame({"q": qq.ravel(), "r": rr.ravel()})
    polys = hex_polygons(grid["q"].to_numpy(), grid["r"].to_numpy(), size)
    shapely.prepare(boundary)
    touches = shapely.intersects(boundary, polys)
    grid, polys = grid[touches], polys[touches]

    w = wells.copy()
    w["q"], w["r"] = hex_index(w.geometry.x.to_numpy(), w.geometry.y.to_numpy(), size)
    w["hex_id"] = w["q"].astype(str) + "_" + w["r"].astype(str)
    summary = summarize(w, "hex_id", acfg)
    grid["hex_id"] = grid["q"].astype(str) + "_" + grid["r"].astype(str)
    out = grid.merge(summary, left_on="hex_id", right_index=True, how="left")
    count_cols = ["wells"] + list(acfg["class_counts"]) + acfg["volume_columns"]
    out[count_cols] = out[count_cols].fillna(0)
    out["area_km2"] = round(1.5 * SQRT3 * size ** 2 / 1e6, 3)
    out["wells_per_km2"] = (out["wells"] / out["area_km2"]).round(3)
    missing = len(w) - int(out["wells"].sum())
    return gpd.GeoDataFrame(out, geometry=polys, crs=crs), missing


# --- 3. Pads ------------------------------------------------------------------------------

def pads(wells, distance, acfg):
    w = wells.copy()
    w["pad_id"], w["pad_wells"] = assign_pads(w, distance)
    w["x"], w["y"] = w.geometry.x, w.geometry.y
    w["is_active"] = w["well_class"].isin(acfg["class_counts"]["active"])
    # Arrow date/string columns make groupby min/max/first fall back to a Python loop per group
    # (382,090 pads); numpy-backed columns keep it vectorized.
    w["spud_date"] = pd.to_datetime(w["spud_date"])
    w["surface_location_dls"] = w["surface_location_dls"].astype(object)
    w["licensee"] = w["licensee"].astype(object)
    grouped = w.groupby("pad_id")
    licensee_counts = w.groupby(["pad_id", "licensee"]).size().sort_values(ascending=False)
    main_licensee = licensee_counts.reset_index().drop_duplicates("pad_id").set_index("pad_id")["licensee"]
    table = pd.DataFrame({
        "wells": grouped.size(),
        "licensees": grouped["licensee"].nunique(),
        "main_licensee": main_licensee,
        "surface_location_dls": grouped["surface_location_dls"].first(),
        "first_spud": grouped["spud_date"].min().dt.strftime("%Y-%m-%d"),
        "last_spud": grouped["spud_date"].max().dt.strftime("%Y-%m-%d"),
        "active": grouped["is_active"].sum().astype(int),
        "x": grouped["x"].mean(),
        "y": grouped["y"].mean(),
    })
    # Spread = twice the largest distance from the pad centre (close to the minimum bounding circle).
    dx = w["x"] - w["pad_id"].map(table["x"])
    dy = w["y"] - w["pad_id"].map(table["y"])
    table["spread_m"] = (2 * np.hypot(dx, dy).groupby(w["pad_id"]).max()).round(1)
    layer = gpd.GeoDataFrame(table.drop(columns=["x", "y"]).reset_index(),
                             geometry=gpd.points_from_xy(table["x"], table["y"]), crs=wells.crs)

    size_class = pad_size_class(table["wells"], acfg["pads"]["size_bins"])
    layer["size_class"] = size_class.astype(str).to_numpy()
    distribution = table.groupby(size_class, observed=False).agg(pads=("wells", "size"), wells=("wells", "sum"))
    distribution["pct_pads"] = (100 * distribution["pads"] / distribution["pads"].sum()).round(2)
    distribution["pct_wells"] = (100 * distribution["wells"] / distribution["wells"].sum()).round(2)
    largest = layer.drop(columns="geometry").sort_values("wells", ascending=False).head(acfg["pads"]["top_n"])
    membership = pd.DataFrame(w[["licence_number", "pad_id", "pad_wells"]])
    return layer, distribution.rename_axis("wells_per_pad").reset_index(), largest, membership


def pad_size_class(wells_per_pad, bins):
    labels = [f"{a}" if b - a == 1 else f"{a}-{b - 1}" if b < 100000 else f"{a}+" for a, b in zip(bins[:-1], bins[1:])]
    return pd.cut(wells_per_pad, bins=bins, right=False, labels=labels)


# --- 4. Laterals --------------------------------------------------------------------------

def laterals(wells, bottom, traj, acfg):
    lcfg = acfg["laterals"]
    surface = wells.drop_duplicates("licence_number").set_index("licence_number").geometry
    bh = bottom[bottom.geometry.notna()].copy()
    bh["surface_geom"] = bh["licence_number"].map(surface)
    bh = bh[bh["surface_geom"].notna()]
    sh_pts = bh["surface_geom"].to_numpy()
    bh_pts = bh.geometry.to_numpy()
    bh["offset_m"] = shapely.distance(sh_pts, bh_pts).round(1)
    traj_len = pd.Series(shapely.length(traj.geometry.to_numpy()), index=traj["uwi"]).groupby(level=0).max()
    bh["trajectory_2d_m"] = bh["uwi"].map(traj_len).round(1)
    bh["lateral_class"] = np.select(
        [bh["offset_m"] < lcfg["vertical_max_m"], bh["offset_m"] >= lcfg["horizontal_min_m"]],
        ["vertical", "horizontal"], default="deviated")
    bh["spud_year"] = pd.to_datetime(bh["spud_date"]).dt.year
    lines = shapely.linestrings(np.stack([
        np.column_stack([shapely.get_x(sh_pts), shapely.get_y(sh_pts)]),
        np.column_stack([shapely.get_x(bh_pts), shapely.get_y(bh_pts)])], axis=1))
    layer = gpd.GeoDataFrame(bh.drop(columns=["geometry", "surface_geom"]), geometry=lines, crs=bottom.crs)
    layer["spud_date"] = layer["spud_date"].astype("string")

    recent = layer[layer["spud_year"] >= lcfg["first_spud_year"]]
    trend = recent.pivot_table(index="spud_year", columns="lateral_class", values="uwi", aggfunc="count", fill_value=0)
    trend = trend.reindex(columns=["vertical", "deviated", "horizontal"], fill_value=0)
    trend["wellbores"] = trend.sum(axis=1)
    trend["pct_horizontal"] = (100 * trend["horizontal"] / trend["wellbores"]).round(1)
    by_year = recent.groupby("spud_year")
    trend["median_offset_m"] = by_year["offset_m"].median().round(0)
    trend["median_offset_horizontal_m"] = recent[recent["lateral_class"] == "horizontal"].groupby(
        "spud_year")["offset_m"].median().round(0)
    trend["median_trajectory_2d_m"] = by_year["trajectory_2d_m"].median().round(0)
    return layer, trend.reset_index()


# --- Run ----------------------------------------------------------------------------------

def run(cfg, acfg):
    out = output_dir(cfg) / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    wells_all, bottom, traj, boundary = load_inputs(cfg)
    wells = drilled(wells_all, acfg)
    log.info("Drilled wells: %s of %s licences", f"{len(wells):,}", f"{len(wells_all):,}")

    town = townships(wells, acfg)
    hexes, unassigned = hexagons(wells, boundary, wells.crs, acfg)
    pad_layer, pad_dist, pad_top, pad_members = pads(wells, cfg["pads"]["distance_m"], acfg)
    lat_layer, lat_trend = laterals(wells, bottom, traj, acfg)

    checks = pd.DataFrame([
        ("township_wells_equal_drilled_wells", len(wells.dropna(subset=["township"])), int(town["wells"].sum())),
        ("hexagon_wells_equal_drilled_wells", len(wells), int(hexes["wells"].sum())),
        ("pad_wells_equal_drilled_wells", len(wells), int(pad_layer["wells"].sum())),
        ("hexagon_oil_equals_wells_oil", round(wells["oil_m3_12m"].sum()), round(hexes["oil_m3_12m"].sum())),
    ], columns=["test_name", "expected", "actual"])
    checks["status"] = np.where(checks["expected"] == checks["actual"], "PASS", "FAIL")

    gpkg = out / "analysis.gpkg"
    reset_gpkg(gpkg, log)
    for layer, gdf in [("townships", town), ("hexagons_5km", hexes), ("pads", pad_layer),
                       ("wellbore_laterals", lat_layer)]:
        pyogrio.write_dataframe(gdf, gpkg, layer=layer, driver="GPKG", use_arrow=True)
    town.drop(columns="geometry").to_csv(out / "township_summary.csv", index=False)
    hexes[hexes["wells"] > 0].drop(columns="geometry").to_csv(out / "hexagon_summary.csv", index=False)
    pad_dist.to_csv(out / "pad_size_distribution.csv", index=False)
    pad_top.to_csv(out / "largest_pads.csv", index=False)
    pad_members.to_parquet(out / "pad_membership.parquet", index=False)
    lat_trend.to_csv(out / "lateral_trend_by_spud_year.csv", index=False)
    checks.to_csv(out / "analysis_reconciliation.csv", index=False)

    multi = pad_layer["wells"] > 1
    log.info("Townships %s | hexagons %s (%s with wells) | pads %s (%s multi-well, %s wells on them) | wellbores %s",
             f"{len(town):,}", f"{len(hexes):,}", f"{(hexes['wells'] > 0).sum():,}", f"{len(pad_layer):,}",
             f"{multi.sum():,}", f"{int(pad_layer.loc[multi, 'wells'].sum()):,}", f"{len(lat_layer):,}")
    log.info("Lateral classes: %s", lat_layer["lateral_class"].value_counts().to_dict())
    for row in checks.itertuples():
        log.info("reconciliation %-40s expected %s actual %s %s", row.test_name, row.expected, row.actual, row.status)
    if (checks["status"] == "FAIL").any():
        raise ReconciliationFailed(", ".join(checks.loc[checks.status == "FAIL", "test_name"]))
    return checks


def load_analysis_config(path=DEFAULT_ANALYSIS_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--analysis-config", default=str(DEFAULT_ANALYSIS_CONFIG))
    args = parser.parse_args()
    run(load_config(args.config, args.mode), load_analysis_config(args.analysis_config))


if __name__ == "__main__":
    main()
