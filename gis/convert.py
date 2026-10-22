"""Standardize staged layers and write the GeoPackage, wells Shapefile and wells CSV.

Standardization (all driven by config/gis.yaml):
- keep and rename the configured fields to snake_case
- trim text, turn empty strings into NULL
- parse dates with a per-column format and store them as real dates
- map licence status values through config/gis_licence_status_map.csv
- reproject every layer to the target CRS

Usage:
    python -m gis.convert [--mode sample|full]
"""
import json

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyogrio

from gis.common import base_arg_parser, get_logger, load_config, output_dir, repo_path, staging_dir

log = get_logger("gis.convert")
DATE_DTYPE = pd.ArrowDtype(pa.date32())
UNMAPPED_STATUS = "Unmapped"


def clean_text(gdf):
    for col in gdf.columns:
        if col != "geometry" and (gdf[col].dtype == object or pd.api.types.is_string_dtype(gdf[col])):
            stripped = gdf[col].astype("string").str.strip()
            gdf[col] = stripped.mask(stripped == "")
    return gdf


def parse_dates(gdf, date_formats, layer_key):
    for col, fmt in date_formats.items():
        raw = gdf[col]
        if fmt == "datetime":
            parsed = pd.to_datetime(raw, utc=True, errors="coerce").dt.tz_localize(None)
        else:
            parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
        failed = raw.notna() & parsed.isna()
        if failed.any():
            log.warning("%s.%s: %s values do not match %r, e.g. %s",
                        layer_key, col, f"{failed.sum():,}", fmt, raw[failed].unique()[:5].tolist())
        gdf[col] = parsed.dt.date.astype(DATE_DTYPE)
    return gdf


def map_licence_status(gdf, status_map, layer_key):
    if "licence_status_raw" not in gdf.columns:
        return gdf
    lookup = status_map.set_index("raw_value")
    raw = gdf["licence_status_raw"]
    status = raw.map(lookup["licence_status"])
    group = raw.map(lookup["status_group"])
    unmapped = raw.notna() & status.isna()
    if unmapped.any():
        log.warning("%s: %s rows with unmapped licence status %s; add them to the mapping table",
                    layer_key, f"{unmapped.sum():,}", raw[unmapped].unique().tolist())
    position = gdf.columns.get_loc("licence_status_raw") + 1
    gdf.insert(position, "licence_status", status.mask(unmapped, UNMAPPED_STATUS))
    gdf.insert(position + 1, "licence_status_group", group.mask(unmapped, UNMAPPED_STATUS))
    return gdf


def standardize_layer(gdf, layer_cfg, status_map, target_crs, layer_key):
    rename = layer_cfg["rename"]
    gdf = gdf[list(rename) + ["geometry"]].rename(columns=rename)
    gdf = clean_text(gdf)
    gdf = parse_dates(gdf, layer_cfg["dates"], layer_key)
    gdf = map_licence_status(gdf, status_map, layer_key)
    return gdf.to_crs(target_crs)


def standardize_boundary(gdf, boundary_cfg, target_crs):
    rename = boundary_cfg["rename"]
    gdf = gdf[list(rename) + ["geometry"]].rename(columns=rename)
    return clean_text(gdf).to_crs(target_crs)


def write_gpkg_layer(gdf, path, layer):
    pyogrio.write_dataframe(gdf, path, layer=layer, driver="GPKG", use_arrow=True)


def export_wells(wells, cfg, out):
    exports = cfg["exports"]

    shp_names = exports["shp_field_names"]
    unmapped = set(wells.columns) - set(shp_names) - {"geometry"}
    too_long = [name for name in shp_names.values() if len(name) > 10]
    if unmapped or too_long:
        raise ValueError(f"Shapefile names: unmapped columns {sorted(unmapped)}, names over 10 chars {too_long}")
    shp_path = out / exports["wells_shapefile"]
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(wells.rename(columns=shp_names), shp_path, driver="ESRI Shapefile",
                            encoding="UTF-8", use_arrow=True)

    table = pd.DataFrame(wells.drop(columns="geometry"))
    epsg = wells.crs.to_epsg()
    table[f"x_epsg{epsg}"] = wells.geometry.x.round(3)
    table[f"y_epsg{epsg}"] = wells.geometry.y.round(3)
    table.to_csv(out / exports["wells_csv"], index=False, encoding="utf-8")
    return shp_path


def main():
    args = base_arg_parser(__doc__).parse_args()
    cfg = load_config(args.config, args.mode)
    stage, out = staging_dir(cfg), output_dir(cfg)
    target_crs = cfg["target_crs"]
    status_map = pd.read_csv(repo_path(cfg["paths"]["licence_status_map"]), dtype=str)
    if status_map["raw_value"].duplicated().any():
        raise ValueError("Duplicate raw_value in the licence status mapping table")

    gpkg_path = out / cfg["exports"]["geopackage"]
    gpkg_path.unlink(missing_ok=True)  # rebuilt from scratch on every run
    manifest = {"mode": cfg["run"]["mode"], "target_crs": target_crs, "layers": {}}

    for key, layer_cfg in cfg["layers"].items():
        staged = gpd.read_parquet(stage / f"{key}.parquet")
        gdf = standardize_layer(staged, layer_cfg, status_map, target_crs, key)
        if len(gdf) != len(staged):
            raise AssertionError(f"{key}: row count changed during standardization ({len(staged)} -> {len(gdf)})")
        write_gpkg_layer(gdf, gpkg_path, layer_cfg["output_layer"])
        manifest["layers"][layer_cfg["output_layer"]] = {"rows_staged": len(staged), "rows_written": len(gdf)}
        log.info("%s -> %s: %s rows", key, layer_cfg["output_layer"], f"{len(gdf):,}")
        if key == cfg["wells_layer"]:
            wells = gdf

    boundary = standardize_boundary(gpd.read_parquet(stage / "boundary.parquet"), cfg["boundary"], target_crs)
    write_gpkg_layer(boundary, gpkg_path, cfg["boundary"]["output_layer"])
    manifest["layers"][cfg["boundary"]["output_layer"]] = {"rows_staged": 1, "rows_written": len(boundary)}

    shp_path = export_wells(wells, cfg, out)
    manifest["wells_exports"] = {"shapefile": str(shp_path.relative_to(out)), "csv": cfg["exports"]["wells_csv"],
                                 "rows": len(wells)}
    (out / "convert_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("GeoPackage: %s", gpkg_path)
    log.info("Wells exports: %s, %s", shp_path, out / cfg["exports"]["wells_csv"])


if __name__ == "__main__":
    main()
