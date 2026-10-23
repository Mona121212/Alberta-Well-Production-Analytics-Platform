"""QA check functions. Each one is referenced by name from config/gis_qa.yaml.

Signature: check(ctx, layer, params) -> CheckResult
- ctx: QAContext (standardized layers, full-province reference tables, boundary, Excel sample)
- layer: output layer name the rule runs on
- failures: GeoDataFrame with FAILURE_COLUMNS, one row per failed record
"""
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer

FAILURE_COLUMNS = ["record_key", "licence_number", "detail", "metric"]
GRS80_A = 6378137.0
GRS80_E2 = 0.00669438002290


@dataclass
class CheckResult:
    failures: gpd.GeoDataFrame
    rows_checked: int


def make_failures(rows, ctx, layer, detail, metric=None, geometry=None):
    """Build the standard failure frame from the failing rows of a layer."""
    key_col = ctx.record_keys[layer]
    frame = pd.DataFrame({
        "record_key": rows[key_col].to_numpy(),
        "licence_number": rows["licence_number"].to_numpy(),
        "detail": np.asarray(detail, dtype=object),
        "metric": np.full(len(rows), np.nan) if metric is None else np.asarray(metric, dtype=float),
    })
    geoms = rows.geometry.to_numpy() if geometry is None else np.asarray(geometry, dtype=object)
    return gpd.GeoDataFrame(frame, geometry=gpd.GeoSeries(geoms, crs=ctx.crs), crs=ctx.crs)


def has_geometry(gdf):
    return gdf.geometry.notna() & ~gdf.geometry.is_empty


# --- 1. Location inside the province ------------------------------------------------------

def outside_boundary(ctx, layer, params):
    gdf = ctx.layer(layer)
    pts = gdf[has_geometry(gdf)]
    inside = shapely.contains(ctx.boundary, pts.geometry.to_numpy())
    outside = pts[~inside]
    distance = shapely.distance(ctx.boundary, outside.geometry.to_numpy())
    failed = distance > params["tolerance_m"]
    rows = outside[failed]
    detail = [f"{d:,.0f} m outside the Alberta boundary" for d in distance[failed]]
    return CheckResult(make_failures(rows, ctx, layer, detail, distance[failed]), len(pts))


# --- 2. Geometry vs attribute lat/long ---------------------------------------------------

def latlong_mismatch(ctx, layer, params):
    gdf = ctx.layer(layer)
    lat, lon = gdf[params["lat_column"]], gdf[params["lon_column"]]
    checked = gdf[has_geometry(gdf) & lat.notna() & lon.notna()]
    transformer = Transformer.from_crs(params["latlong_crs"], ctx.crs, always_xy=True)
    x, y = transformer.transform(checked[params["lon_column"]].to_numpy(float),
                                 checked[params["lat_column"]].to_numpy(float))
    distance = np.hypot(checked.geometry.x.to_numpy() - x, checked.geometry.y.to_numpy() - y)
    failed = distance > params["tolerance_m"]
    rows = checked[failed]
    detail = [f"geometry is {d:,.1f} m from lat/long ({la:.6f}, {lo:.6f}) in {params['latlong_crs']}"
              for d, la, lo in zip(distance[failed], rows[params["lat_column"]], rows[params["lon_column"]])]
    links = shapely.shortest_line(rows.geometry.to_numpy(), shapely.points(x[failed], y[failed]))
    return CheckResult(make_failures(rows, ctx, layer, detail, distance[failed], links), len(checked))


# --- 3. DLS label vs location -------------------------------------------------------------
# Approximate Alberta Township System grid, no ATS polygon layer (see DECISIONS.md DEC-010):
#   township n covers latitudes [base + (n-1)*h, base + n*h)
#   range n covers ground distances [(n-1)*w, n*w) west of the labelled meridian,
#   measured along the parallel on the GRS80 ellipsoid.
# Positions are continuous ("2.4" = 40% into township 3), so the result is a distance
# outside the labelled cell in cell units, not a yes/no on integers.

def dls_positions(gdf, params):
    parts = gdf[params["label_column"]].str.extract(params["label_pattern"]).astype(float)
    parts.columns = ["lsd", "sec", "twp", "rge", "mer"]
    meridian_lon = parts["mer"].map({float(k): v for k, v in params["meridian_longitudes"].items()})

    lon, lat = Transformer.from_crs(gdf.crs, "EPSG:4269", always_xy=True).transform(
        gdf.geometry.x.to_numpy(), gdf.geometry.y.to_numpy())
    phi = np.radians(lat)
    prime_vertical = GRS80_A / np.sqrt(1 - GRS80_E2 * np.sin(phi) ** 2)
    metres_west = np.radians(meridian_lon.to_numpy() - lon) * prime_vertical * np.cos(phi)

    parts["twp_pos"] = (lat - params["base_latitude"]) / params["township_height_deg"]
    parts["rge_pos"] = metres_west / params["range_width_m"]
    parts["parsed"] = parts[["sec", "twp", "rge"]].notna().all(axis=1) & meridian_lon.notna()
    return parts


def distance_outside(position, cell):
    """How far a continuous position lies outside cell [cell-1, cell), in cell units."""
    return np.maximum(0, np.maximum((cell - 1) - position, position - cell))


def dls_trm_offset(parts):
    return np.maximum(distance_outside(parts["twp_pos"], parts["twp"]),
                      distance_outside(parts["rge_pos"], parts["rge"]))


def dls_township_range(ctx, layer, params):
    gdf = ctx.layer(layer)
    checked = gdf[has_geometry(gdf) & gdf[params["label_column"]].notna()]
    parts = dls_positions(checked, params)
    offset = dls_trm_offset(parts)
    failed = (~parts["parsed"] | (offset > params["tolerance_cells"])).to_numpy()
    p, rows = parts[failed], checked[failed]
    detail = [
        f"label {label} not parseable" if not ok else
        f"label {label}: point is near TWP {int(np.floor(t)) + 1:03d} RGE {int(np.floor(r)) + 1:02d} W{int(m)}, "
        f"{o:.2f} cells outside the labelled township/range"
        for label, ok, t, r, m, o in zip(rows[params["label_column"]], p["parsed"], p["twp_pos"], p["rge_pos"],
                                        p["mer"].fillna(0), offset[failed])
    ]
    return CheckResult(make_failures(rows, ctx, layer, detail, offset[failed]), len(checked))


def section_row_col(section):
    """DLS sections run 1-6 east to west on the south row, 7-12 west to east, and so on."""
    row = (section - 1) // 6
    k = (section - 1) % 6
    col_from_east = np.where(row % 2 == 0, k, 5 - k)
    return row, col_from_east


def section_number(row, col_from_east):
    return np.where(row % 2 == 0, row * 6 + col_from_east + 1, row * 6 + (6 - col_from_east))


def dls_section(ctx, layer, params):
    """Only rows whose township/range already match are checked, so failures are not double counted."""
    gdf = ctx.layer(layer)
    candidates = gdf[has_geometry(gdf) & gdf[params["label_column"]].notna()]
    parts = dls_positions(candidates, params)
    keep = (parts["parsed"] & (dls_trm_offset(parts) <= params["tolerance_cells"])).to_numpy()
    checked, parts = candidates[keep], parts[keep]

    north = (parts["twp_pos"] - (parts["twp"] - 1)) * 6  # 0..6 section rows from the south
    west = (parts["rge_pos"] - (parts["rge"] - 1)) * 6   # 0..6 section columns from the east
    row, col = section_row_col(parts["sec"].astype(int))
    offset = np.maximum(distance_outside(north, row + 1), distance_outside(west, col + 1)).to_numpy()
    failed = offset > params["tolerance_sections"]
    expected = section_number(np.clip(np.floor(north), 0, 5).astype(int), np.clip(np.floor(west), 0, 5).astype(int))
    rows = checked[failed]
    detail = [f"label {label}: point is near section {int(s):02d}, {o:.2f} sections outside section {int(sec):02d}"
              for label, s, o, sec in zip(rows[params["label_column"]], expected[failed], offset[failed],
                                          parts["sec"][failed])]
    return CheckResult(make_failures(rows, ctx, layer, detail, offset[failed]), len(checked))


# --- 4. Referential integrity -------------------------------------------------------------

def missing_reference(ctx, layer, params):
    gdf, key = ctx.layer(layer), params["key"]
    reference_keys = pd.concat([ctx.reference(r)[key] for r in params["reference_layers"]]).dropna().unique()
    checked = gdf[gdf[key].notna()]
    if "exclude" in params:  # records where a missing reference is expected
        checked = checked[~checked[params["exclude"]["column"]].isin(params["exclude"]["values"])]
    rows = checked[~checked[key].isin(reference_keys)]
    targets = " or ".join(params["reference_layers"])
    detail = [f"{key} {k} not found in {targets} (whole province)" for k in rows[key]]
    return CheckResult(make_failures(rows, ctx, layer, detail), len(checked))


# --- 5. Trajectory consistency ------------------------------------------------------------

def trajectory_endpoint(ctx, layer, params):
    traj, key = ctx.layer(layer), params["join_key"]
    target = ctx.layer(params["target_layer"])
    target = target[has_geometry(target)].drop_duplicates(key).set_index(key).geometry

    position = 0 if params["endpoint"] == "start" else -1
    lines = traj.geometry.to_numpy()
    endpoints = shapely.get_point(shapely.get_geometry(lines, position), position)
    target_points = traj[key].map(target).to_numpy()

    evaluated = ~shapely.is_missing(endpoints) & ~pd.isna(target_points)
    checked = traj[evaluated]
    endpoints, target_points = endpoints[evaluated], target_points[evaluated]
    distance = shapely.distance(endpoints, target_points)
    failed = distance > params["tolerance_m"]
    rows = checked[failed]
    detail = [f"{params['endpoint']} vertex is {d:,.1f} m from the {params['target_layer']} point ({key} {k})"
              for d, k in zip(distance[failed], rows[key])]
    links = shapely.force_2d(shapely.shortest_line(endpoints[failed], target_points[failed]))
    return CheckResult(make_failures(rows, ctx, layer, detail, distance[failed], links), len(checked))


def length_3d(lines):
    coords, index = shapely.get_coordinates(lines, include_z=True, return_index=True)
    same_line = index[1:] == index[:-1]
    segment = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    return np.bincount(index[1:][same_line], weights=segment[same_line], minlength=len(lines))


def zero_length(ctx, layer, params):
    traj = ctx.layer(layer)
    checked = traj[has_geometry(traj)]
    lines = checked.geometry.to_numpy()
    lengths = length_3d(lines) if params["dimension"] == "3d" else shapely.length(lines)
    failed = lengths == 0
    rows = checked[failed]
    start = shapely.force_2d(shapely.get_point(shapely.get_geometry(lines[failed], 0), 0))
    detail = [f"{params['dimension'].upper()} length is 0; geometry source {s}" for s in rows["geometry_source"]]
    return CheckResult(make_failures(rows, ctx, layer, detail, np.zeros(len(rows)), start), len(checked))


# --- 6. Status consistency ----------------------------------------------------------------

def pattern_mismatch(ctx, layer, params):
    gdf, col = ctx.layer(layer), params["column"]
    checked = gdf[gdf[col].notna()]
    rows = checked[~checked[col].str.fullmatch(params["pattern"])]
    detail = [f"{col} = {v!r}" for v in rows[col]]
    return CheckResult(make_failures(rows, ctx, layer, detail), len(checked))


def status_conflict(ctx, layer, params):
    gdf = ctx.layer(layer)
    mode, group = gdf[params["mode_column"]], gdf[params["licence_group_column"]]
    checked = gdf[mode.notna() & group.notna()]
    conflict = (checked[params["mode_column"]].isin(params["producing_modes"])
                & checked[params["licence_group_column"]].isin(params["closed_groups"]))
    rows = checked[conflict]
    detail = [f"status_mode {m!r} but licence status {s!r} ({g})"
              for m, s, g in zip(rows[params["mode_column"]], rows["licence_status"], rows[params["licence_group_column"]])]
    return CheckResult(make_failures(rows, ctx, layer, detail), len(checked))


def source_compare(ctx, layer, params):
    """Excel sample vs the GDB (full-province reference table). Rows are non-spatial."""
    key = params["key"]
    excel, gdb = ctx.excel_sample, ctx.reference(layer)
    merged = excel.merge(gdb, on=key, how="inner", suffixes=("_excel", "_gdb"))
    differences, pairs = {}, []
    for col in params["columns"]:
        a, b = merged[f"{col}_gdb"], merged[f"{col}_excel"]
        differs = ~(a.eq(b).fillna(False) | (a.isna() & b.isna()))
        differences[col] = np.where(differs, [f"{col}: gdb={x!r} excel={y!r}" for x, y in zip(a, b)], None)
        pairs.append(merged[differs].groupby([f"{col}_gdb", f"{col}_excel"], dropna=False).size()
                     .rename("rows").reset_index()
                     .rename(columns={f"{col}_gdb": "gdb_value", f"{col}_excel": "excel_value"})
                     .assign(column=col))
    ctx.side_outputs["gdb_vs_excel_value_pairs"] = (
        pd.concat(pairs)[["column", "gdb_value", "excel_value", "rows"]].sort_values(["column", "rows"], ascending=[True, False]))

    # One failure row per record, listing every column that differs.
    diff = pd.DataFrame(differences, index=merged.index)
    n_diff = diff.notna().sum(axis=1)
    rows = merged[n_diff > 0]
    table = pd.DataFrame({
        "record_key": rows[key], "licence_number": rows["licence_number_gdb"],
        "detail": ["; ".join(v for v in r if isinstance(v, str)) for r in diff[n_diff > 0].to_numpy()],
        "metric": n_diff[n_diff > 0]}).reset_index(drop=True)
    failures = gpd.GeoDataFrame(table, geometry=gpd.GeoSeries([None] * len(table), crs=ctx.crs), crs=ctx.crs)
    return CheckResult(failures, len(merged))


# --- 7. Duplicates, missing and accepted values -------------------------------------------

def duplicate_key(ctx, layer, params):
    gdf, key = ctx.layer(layer), params["key"]
    counts = ctx.reference(params["reference_layer"])[key].value_counts()
    checked = gdf[gdf[key].notna()]
    n = checked[key].map(counts)
    rows = checked[n > 1]
    detail = [f"{key} {k} appears {c} times in the full layer" for k, c in zip(rows[key], n[n > 1])]
    return CheckResult(make_failures(rows, ctx, layer, detail, n[n > 1]), len(checked))


def missing_values(ctx, layer, params):
    gdf = ctx.layer(layer)
    missing = pd.DataFrame({
        col: ~has_geometry(gdf) if col == "geometry" else gdf[col].isna() for col in params["columns"]})
    failed = missing.any(axis=1)
    rows = gdf[failed]
    detail = ["missing: " + ", ".join(missing.columns[m]) for m in missing[failed].to_numpy()]
    return CheckResult(make_failures(rows, ctx, layer, detail, missing[failed].sum(axis=1)), len(gdf))


def accepted_values(ctx, layer, params):
    gdf, col = ctx.layer(layer), params["column"]
    accepted = set(pd.read_csv(ctx.repo_path(params["values_file"]), dtype=str)[params["values_column"]])
    checked = gdf[gdf[col].notna()]
    rows = checked[~checked[col].isin(accepted)]
    detail = [f"{col} = {v!r} not in {params['values_file']}" for v in rows[col]]
    return CheckResult(make_failures(rows, ctx, layer, detail), len(checked))


def date_out_of_range(ctx, layer, params):
    """Dates before min_date or after max_date ("today" = the run date)."""
    gdf = ctx.layer(layer)
    low = pd.Timestamp(params["min_date"])
    high = pd.Timestamp.today().normalize() if params["max_date"] == "today" else pd.Timestamp(params["max_date"])
    dates = pd.DataFrame({c: pd.to_datetime(gdf[c]) for c in params["columns"]})
    bad = (dates < low) | (dates > high)
    failed = bad.any(axis=1)
    rows = gdf[failed]
    detail = ["; ".join(f"{c} = {dates.at[i, c].date()}" for c in params["columns"] if bad.at[i, c])
              + f" (allowed {low.date()} to {high.date()})" for i in rows.index]
    return CheckResult(make_failures(rows, ctx, layer, detail), int(dates.notna().any(axis=1).sum()))


CHECKS = {fn.__name__: fn for fn in [
    outside_boundary, latlong_mismatch, dls_township_range, dls_section, missing_reference,
    trajectory_endpoint, zero_length, pattern_mismatch, status_conflict, source_compare,
    duplicate_key, missing_values, accepted_values, date_out_of_range,
]}
