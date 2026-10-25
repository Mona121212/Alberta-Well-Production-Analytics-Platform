"""ST37 change detection between two well snapshots.

A snapshot is the standardized surface-hole layer saved as GeoParquet. Comparing two snapshots
gives a changelog: new and removed licences, status changes, licensee transfers, DLS label
changes and moved surface locations.

Usage:
    # 1. Save a snapshot of the current run (do this after each ST37 refresh)
    python -m gis.changes snapshot --mode full [--date 2026-10-22]
    # 2. Compare two snapshots
    python -m gis.changes compare --old gis/output/snapshots/wells_2026-10-22.parquet \\
                                  --new gis/output/snapshots/wells_2026-11-30.parquet
    # Demo: inject known changes into a copy of the newest snapshot and check they are all found
    python -m gis.changes demo --mode sample

Outputs of compare/demo in gis/output/changes/<old>_to_<new>/: changelog.csv, change_summary.csv
and changes.gpkg (changes: points at the current location; moves: old -> new lines).
"""
import argparse
import sys
from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
import yaml

from gis.common import REPO_ROOT, get_logger, load_config, output_dir, repo_path, reset_gpkg, DEFAULT_CONFIG

log = get_logger("gis.changes")
DEFAULT_CHANGES_CONFIG = REPO_ROOT / "config" / "gis_changes.yaml"
CHANGELOG_COLUMNS = ["change_type", "licence_number", "field", "old_value", "new_value", "distance_m", "licensee"]


def load_changes_config(path=DEFAULT_CHANGES_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- Snapshots ----------------------------------------------------------------------------

def take_snapshot(cfg, ccfg, snapshot_date):
    layer = cfg["layers"][cfg["wells_layer"]]["output_layer"]
    wells = pyogrio.read_dataframe(output_dir(cfg) / cfg["exports"]["geopackage"], layer=layer,
                                   columns=ccfg["snapshot_columns"], use_arrow=True)
    folder = repo_path(ccfg["snapshot_dir"]) / cfg["run"]["mode"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"wells_{snapshot_date}.parquet"
    wells.to_parquet(path, index=False)
    log.info("Snapshot %s: %s wells -> %s", snapshot_date, f"{len(wells):,}", path)
    return path


# --- Compare ------------------------------------------------------------------------------

def as_text(series):
    return series.astype("string").where(series.notna())


def compare(old, new, ccfg):
    """Return (changelog GeoDataFrame of points, moves GeoDataFrame of lines)."""
    key = ccfg["key"]
    for name, snap in (("old", old), ("new", new)):
        if snap[key].duplicated().any():
            raise ValueError(f"{name} snapshot has duplicate {key} values")
    old_i, new_i = old.set_index(key), new.set_index(key)
    added = new_i.index.difference(old_i.index)
    removed = old_i.index.difference(new_i.index)
    common = new_i.index.intersection(old_i.index)

    frames = [
        pd.DataFrame({"change_type": "new_licence", key: added, "licensee": new_i.loc[added, "licensee"].to_numpy(),
                      "geometry": new_i.loc[added].geometry.to_numpy()}),
        pd.DataFrame({"change_type": "removed_licence", key: removed,
                      "licensee": old_i.loc[removed, "licensee"].to_numpy(),
                      "geometry": old_i.loc[removed].geometry.to_numpy()}),
    ]
    o, n = old_i.loc[common], new_i.loc[common]
    for column, change_type in ccfg["attribute_changes"].items():
        a, b = as_text(o[column]), as_text(n[column])
        differs = ~(a.eq(b).fillna(False) | (a.isna() & b.isna()))
        idx = common[differs.to_numpy()]
        frames.append(pd.DataFrame({
            "change_type": change_type, key: idx, "field": column,
            "old_value": a[differs].to_numpy(), "new_value": b[differs].to_numpy(),
            "licensee": n.loc[idx, "licensee"].to_numpy(), "geometry": n.loc[idx].geometry.to_numpy()}))

    distance = shapely.distance(o.geometry.to_numpy(), n.geometry.to_numpy())
    moved = np.nan_to_num(distance, nan=0.0) > ccfg["move_tolerance_m"]
    idx = common[moved]
    frames.append(pd.DataFrame({
        "change_type": "moved_location", key: idx, "field": "geometry", "distance_m": distance[moved].round(1),
        "licensee": n.loc[idx, "licensee"].to_numpy(), "geometry": n.loc[idx].geometry.to_numpy()}))

    changelog = pd.concat(frames, ignore_index=True).reindex(columns=CHANGELOG_COLUMNS + ["geometry"])
    changelog = gpd.GeoDataFrame(changelog, geometry="geometry", crs=new.crs)
    moves = gpd.GeoDataFrame(
        {key: idx, "distance_m": distance[moved].round(1), "licensee": n.loc[idx, "licensee"].to_numpy()},
        geometry=shapely.shortest_line(o.geometry.to_numpy()[moved], n.geometry.to_numpy()[moved]), crs=new.crs)
    return changelog, moves


def write_changes(changelog, moves, out, old_label, new_label):
    out.mkdir(parents=True, exist_ok=True)
    changelog.drop(columns="geometry").to_csv(out / "changelog.csv", index=False)
    summary = changelog.groupby("change_type").size().rename("records").reset_index()
    summary.insert(0, "new_snapshot", new_label)
    summary.insert(0, "old_snapshot", old_label)
    summary.to_csv(out / "change_summary.csv", index=False)
    gpkg = out / "changes.gpkg"
    reset_gpkg(gpkg, log)
    pyogrio.write_dataframe(changelog, gpkg, layer="changes", driver="GPKG", use_arrow=True)
    if not moves.empty:
        pyogrio.write_dataframe(moves, gpkg, layer="moves", driver="GPKG", use_arrow=True)
    log.info("Changes %s -> %s: %s", old_label, new_label, summary.set_index("change_type")["records"].to_dict())
    return summary


# --- Synthetic second snapshot ------------------------------------------------------------

def synthetic_snapshot(old, ccfg):
    """Copy a snapshot and inject a known number of each change into disjoint licences."""
    syn = ccfg["synthetic"]
    key = ccfg["key"]
    rng = np.random.default_rng(syn["seed"])
    new = old.copy()
    kinds = ["removed_licence", "status_change", "licensee_transfer", "moved_location"]
    picks = rng.choice(len(new), size=sum(syn[k] for k in kinds), replace=False)
    groups, start = {}, 0
    for kind in kinds:
        groups[kind] = new.index[picks[start:start + syn[kind]]]
        start += syn[kind]

    statuses = sorted(new["licence_status"].dropna().unique())
    for i in groups["status_change"]:
        new.at[i, "licence_status"] = next(s for s in statuses if s != new.at[i, "licence_status"])
    for i in groups["licensee_transfer"]:
        new.at[i, "licensee"] = "Synthetic Transfer Ltd."
    shift = syn["move_distance_m"]
    moved = new.loc[groups["moved_location"]].geometry.translate(xoff=shift)
    new.loc[groups["moved_location"], "geometry"] = moved

    template = new.drop(index=groups["removed_licence"]).sample(syn["new_licence"], random_state=syn["seed"])
    added = template.copy()
    added[key] = [f"SYN{i:04d}" for i in range(len(added))]
    added["geometry"] = added.geometry.translate(yoff=500)
    new = pd.concat([new.drop(index=groups["removed_licence"]), added], ignore_index=True)
    expected = {k: syn[k] for k in kinds} | {"new_licence": syn["new_licence"]}
    return gpd.GeoDataFrame(new, geometry="geometry", crs=old.crs), expected


def run_demo(cfg, ccfg):
    folder = repo_path(ccfg["snapshot_dir"]) / cfg["run"]["mode"]
    snapshots = sorted(folder.glob("wells_*.parquet"))
    old_path = snapshots[-1] if snapshots else take_snapshot(cfg, ccfg, date.today().isoformat())
    old = gpd.read_parquet(old_path)
    new, expected = synthetic_snapshot(old, ccfg)
    changelog, moves = compare(old, new, ccfg)
    out = output_dir(cfg) / "changes" / f"{old_path.stem}_to_synthetic"
    summary = write_changes(changelog, moves, out, old_path.stem, "synthetic")
    found = summary.set_index("change_type")["records"].to_dict()
    mismatches = {k: (v, found.get(k, 0)) for k, v in expected.items() if found.get(k, 0) != v}
    extra = {k: v for k, v in found.items() if k not in expected}
    if mismatches or extra:
        raise AssertionError(f"Change detection demo: expected vs found {mismatches}, unexpected {extra}")
    log.info("Demo passed: every injected change was found and nothing else (%s)", expected)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["snapshot", "compare", "demo"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--mode", choices=["sample", "full"])
    parser.add_argument("--date", default=date.today().isoformat(), help="Snapshot date label (snapshot only)")
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument("--out", help="Output folder (compare only)")
    args = parser.parse_args()
    cfg, ccfg = load_config(args.config, args.mode), load_changes_config()

    if args.command == "snapshot":
        take_snapshot(cfg, ccfg, args.date)
    elif args.command == "compare":
        if not (args.old and args.new):
            sys.exit("compare needs --old and --new snapshot files")
        old, new = gpd.read_parquet(args.old), gpd.read_parquet(args.new)
        label_old, label_new = repo_path(args.old).stem, repo_path(args.new).stem
        out = repo_path(args.out) if args.out else output_dir(cfg) / "changes" / f"{label_old}_to_{label_new}"
        write_changes(*compare(old, new, ccfg), out, label_old, label_new)
    else:
        run_demo(cfg, ccfg)


if __name__ == "__main__":
    main()
