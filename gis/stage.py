"""Stage raw inputs as (Geo)Parquet, once per mode. Values are kept exactly as in the source.

- <layer>.parquet: each GDB layer (sample bbox or full)
- <layer>_reference.parquet: key and status columns for the whole province, no geometry
- boundary.parquet: the Alberta polygon
- excel_sample.parquet: first rows of an Excel extract, for the GDB vs Excel comparison
- petrinex_wells.parquet: well rows (WI) of all monthly Petrinex volume files, whole province

Reading the 1.7 GB File Geodatabase is slow, so later steps read Parquet only.

Usage:
    python -m gis.stage [--mode sample|full] [--force]
"""
import time

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
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


def stage_reference(cfg, key, layer_cfg, out_path):
    """Selected attribute columns for the whole province, without geometry (always full)."""
    gdb = repo_path(cfg["paths"]["gdb"])
    df = pyogrio.read_dataframe(gdb, layer=layer_cfg["source_layer"], columns=layer_cfg["reference_columns"],
                                read_geometry=False, use_arrow=True)
    df.to_parquet(out_path, index=False)
    log.info("%s reference: %s rows x %d columns -> %s", key, f"{len(df):,}", df.shape[1], out_path.name)


def stage_excel_sample(cfg, out_path):
    excel = cfg["excel_sample"]
    df = pd.read_excel(repo_path(excel["file"]), sheet_name=excel["sheet"], nrows=excel["rows"], dtype=str)
    df.to_parquet(out_path, index=False)
    log.info("Excel sample: first %s rows of %s -> %s", f"{len(df):,}", excel["sheet"], out_path.name)


def stage_petrinex(cfg, out_path):
    """Well rows of every monthly Petrinex file, as text (casting happens in the analysis step)."""
    pet = cfg["petrinex"]
    files = sorted(repo_path(pet["folder"]).glob(pet["file_pattern"]))
    if not files:
        raise FileNotFoundError(f"No Petrinex files match {pet['folder']}/{pet['file_pattern']}")
    convert = pa_csv.ConvertOptions(include_columns=pet["columns"],
                                    column_types={c: pa.string() for c in pet["columns"]})
    tables = []
    for path in files:
        table = pa_csv.read_csv(path, convert_options=convert)
        table = table.filter(pc.equal(table["FromToIDType"], pet["id_type"]))
        tables.append(table.append_column("source_file", pa.array([path.name] * table.num_rows)))
    combined = pa.concat_tables(tables)
    pq.write_table(combined, out_path)
    log.info("Petrinex: %d files, %s %s rows -> %s", len(files), f"{combined.num_rows:,}", pet["id_type"], out_path.name)


def stage_boundary(cfg, out_path):
    # The boundary is never sample-filtered: QA needs the whole province.
    path = repo_path(cfg["paths"]["boundary"])
    gdf = pyogrio.read_dataframe(path, where=cfg["boundary"]["where"], use_arrow=True)
    if len(gdf) != 1:
        raise ValueError(f"Boundary filter {cfg['boundary']['where']!r} returned {len(gdf)} rows, expected 1")
    gdf.to_parquet(out_path, index=False)
    log.info("boundary: 1 polygon (%s) -> %s", gdf.crs.to_string(), out_path.name)


def run(cfg, force=False):
    out = staging_dir(cfg)
    log.info("Mode: %s, staging folder: %s", cfg["run"]["mode"], out)

    jobs = []
    for key, layer_cfg in cfg["layers"].items():
        jobs.append((key, out / f"{key}.parquet", lambda p, k=key, c=layer_cfg: stage_layer(cfg, k, c, p)))
        jobs.append((f"{key} reference", out / f"{key}_reference.parquet",
                     lambda p, k=key, c=layer_cfg: stage_reference(cfg, k, c, p)))
    jobs.append(("boundary", out / "boundary.parquet", lambda p: stage_boundary(cfg, p)))
    jobs.append(("excel sample", out / "excel_sample.parquet", lambda p: stage_excel_sample(cfg, p)))
    jobs.append(("petrinex", out / "petrinex_wells.parquet", lambda p: stage_petrinex(cfg, p)))

    for name, out_path, stage_fn in jobs:
        if out_path.exists() and not force:
            log.info("%s: already staged, skipping (use --force to rebuild)", name)
            continue
        started = time.time()
        stage_fn(out_path)
        log.info("%s: %.1fs", name, time.time() - started)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--force", action="store_true", help="Re-stage even if Parquet files exist")
    args = parser.parse_args()
    run(load_config(args.config, args.mode), force=args.force)


if __name__ == "__main__":
    main()
