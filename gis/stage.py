"""Stage raw inputs as GeoParquet: one file per GDB layer plus the Alberta boundary.

Reading the 1.7 GB File Geodatabase is slow, so it happens once per mode.
Later steps read the Parquet files only. Values are kept exactly as in the source.

Usage:
    python -m gis.stage [--mode sample|full] [--force]
"""
import time

import pyogrio
from pyproj import Transformer

from gis.common import base_arg_parser, get_logger, load_config, repo_path, staging_dir

log = get_logger("gis.stage")


def sample_bbox(cfg, layer_crs):
    """Transform the configured lon/lat sample box into the layer CRS."""
    sample = cfg["run"]["sample"]
    transformer = Transformer.from_crs(sample["bbox_crs"], layer_crs, always_xy=True)
    return transformer.transform_bounds(*sample["bbox_lonlat"])


def read_kwargs(cfg, layer_crs):
    if cfg["run"]["mode"] != "sample":
        return {}
    return {"bbox": sample_bbox(cfg, layer_crs), "max_features": cfg["run"]["sample"]["max_rows"]}


def stage_layer(cfg, key, layer_cfg, out_path):
    gdb = repo_path(cfg["paths"]["gdb"])
    source_layer = layer_cfg["source_layer"]
    info = pyogrio.read_info(gdb, layer=source_layer)
    missing = set(layer_cfg["rename"]) - set(info["fields"])
    if missing:
        raise KeyError(f"{source_layer}: configured fields not in source: {sorted(missing)}")

    gdf = pyogrio.read_dataframe(gdb, layer=source_layer, use_arrow=True, **read_kwargs(cfg, info["crs"]))
    gdf.to_parquet(out_path, index=False)
    log.info("%s: %s rows (source total %s) -> %s", key, f"{len(gdf):,}", f"{info['features']:,}", out_path.name)


def stage_boundary(cfg, out_path):
    # The boundary is never sample-filtered: QA needs the whole province.
    path = repo_path(cfg["paths"]["boundary"])
    gdf = pyogrio.read_dataframe(path, where=cfg["boundary"]["where"], use_arrow=True)
    if len(gdf) != 1:
        raise ValueError(f"Boundary filter {cfg['boundary']['where']!r} returned {len(gdf)} rows, expected 1")
    gdf.to_parquet(out_path, index=False)
    log.info("boundary: 1 polygon (%s) -> %s", gdf.crs.to_string(), out_path.name)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--force", action="store_true", help="Re-stage even if Parquet files exist")
    args = parser.parse_args()
    cfg = load_config(args.config, args.mode)
    out = staging_dir(cfg)
    log.info("Mode: %s, staging folder: %s", cfg["run"]["mode"], out)

    jobs = [(key, out / f"{key}.parquet", layer_cfg) for key, layer_cfg in cfg["layers"].items()]
    jobs.append(("boundary", out / "boundary.parquet", None))
    for key, out_path, layer_cfg in jobs:
        if out_path.exists() and not args.force:
            log.info("%s: already staged, skipping (use --force to rebuild)", key)
            continue
        started = time.time()
        if layer_cfg is None:
            stage_boundary(cfg, out_path)
        else:
            stage_layer(cfg, key, layer_cfg, out_path)
        log.info("%s: %.1fs", key, time.time() - started)


if __name__ == "__main__":
    main()
