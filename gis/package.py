"""Client data package: wells, pads, QA flags, liability class and boundary for one selection.

Select by licensee, township list or AOI polygon file. Output in deliverables/<name>/:
data.gpkg (styles embedded), wells.csv, data_dictionary.md, README.md and one .qml per layer.

Usage:
    python -m gis.package --licensee "Canadian Natural Resources Limited" [--mode full]
    python -m gis.package --townships 076-06W4,095-06W4 --name athabasca_sample
    python -m gis.package --aoi path/to/aoi.gpkg [--aoi-layer name] --name my_aoi
    python -m gis.package --top-licensees 3
"""
import difflib
import re
import sqlite3
import sys
from datetime import date, datetime, timezone

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
import yaml

from gis import styles
from gis.common import REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir, repo_path, staging_dir

log = get_logger("gis.package")
DEFAULT_PACKAGE_CONFIG = REPO_ROOT / "config" / "gis_package.yaml"
DICTIONARY = REPO_ROOT / "config" / "gis_field_dictionary.yaml"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# --- Inputs -------------------------------------------------------------------------------

class Sources:
    """Province-wide outputs of the earlier steps, read once."""

    def __init__(self, cfg):
        out = output_dir(cfg)
        self.cfg = cfg
        self.wells = pyogrio.read_dataframe(out / "liability" / "liability.gpkg", layer="wells_liability",
                                            use_arrow=True)
        qa_cols = ["licence_number", "qa_pass", "n_fail_rules", "n_warn_rules", "n_info_rules", "flagged_rules"]
        qa = pyogrio.read_dataframe(out / "qa" / "qa_results.gpkg", layer="wells_qa", columns=qa_cols,
                                    read_geometry=False, use_arrow=True)
        members = pd.read_parquet(out / "analysis" / "pad_membership.parquet")
        self.wells = (self.wells.drop(columns=["review_pad_id"], errors="ignore")
                      .merge(qa, on="licence_number", how="left")
                      .merge(members, on="licence_number", how="left"))
        self.pads = pyogrio.read_dataframe(out / "analysis" / "analysis.gpkg", layer="pads", use_arrow=True)
        self.failures = pd.read_csv(out / "qa" / "qa_failures.csv", dtype=str)
        self.failures["metric"] = pd.to_numeric(self.failures["metric"])
        self.boundary = pyogrio.read_dataframe(out / cfg["exports"]["geopackage"],
                                               layer=cfg["boundary"]["output_layer"])
        months = pd.read_parquet(staging_dir(cfg) / "petrinex_wells.parquet", columns=["ProductionMonth"])
        self.petrinex_months = (months["ProductionMonth"].min(), months["ProductionMonth"].max())
        # Latest status date that is not in the future (QA rule GQA-23 flags future dates).
        status_dates = pd.to_datetime(self.wells["licence_status_date"])
        self.st37_latest_status = status_dates[status_dates <= pd.Timestamp.today()].max().date()


# --- Selection ----------------------------------------------------------------------------

def select(src, licensee=None, townships=None, aoi=None, aoi_layer=None, buffer_m=1000):
    wells = src.wells
    if licensee:
        mask = wells["licensee"].str.casefold() == licensee.casefold()
        if not mask.any():
            names = wells["licensee"].dropna().unique().tolist()
            close = difflib.get_close_matches(licensee, names, n=5, cutoff=0.6)
            raise SystemExit(f"Licensee {licensee!r} not found. Close matches: {close}")
        chosen = wells[mask]
        extent = chosen.geometry.union_all().convex_hull.buffer(buffer_m)
        return chosen, "licensee", chosen["licensee"].iat[0], extent
    if townships:
        keys = [t.strip().upper() for t in townships.split(",") if t.strip()]
        chosen = wells[wells["township"].isin(keys)]
        missing = sorted(set(keys) - set(chosen["township"]))
        if chosen.empty:
            raise SystemExit(f"No wells in townships {keys}")
        if missing:
            log.warning("Townships without wells: %s", missing)
        extent = chosen.geometry.union_all().convex_hull.buffer(buffer_m)
        return chosen, "townships", ", ".join(keys), extent
    if aoi:
        area = pyogrio.read_dataframe(aoi, layer=aoi_layer).to_crs(wells.crs).geometry.union_all()
        chosen = wells[shapely.intersects(area, wells.geometry.to_numpy())]
        if chosen.empty:
            raise SystemExit(f"No wells inside the AOI {aoi}")
        return chosen, "aoi", str(aoi), area
    raise SystemExit("Give --licensee, --townships, --aoi or --top-licensees")


# --- Package content ----------------------------------------------------------------------

def package_layers(src, chosen, selection_type, selection_value, extent, pcfg):
    wells = gpd.GeoDataFrame(chosen[pcfg["wells_fields"] + ["geometry"]], crs=src.wells.crs)
    in_package = wells["pad_id"].value_counts().rename("wells_in_package")
    pads = src.pads.merge(in_package, left_on="pad_id", right_index=True, how="inner")
    pads = pads[pcfg["pads_fields"] + ["geometry"]].sort_values("wells", ascending=False)
    qa_flags = src.failures[src.failures["licence_number"].isin(set(wells["licence_number"]))][pcfg["qa_fields"]]
    extent_layer = gpd.GeoDataFrame({"selection_type": [selection_type], "selection_value": [selection_value],
                                     "wells": [len(wells)], "area_km2": [round(extent.area / 1e6, 1)]},
                                    geometry=[extent], crs=src.wells.crs)
    return {"wells": wells, "pads": pads, "qa_flags": qa_flags, "package_extent": extent_layer,
            "alberta_boundary": src.boundary}


def geometry_kind(gdf):
    kind = gdf.geometry.geom_type.dropna().iloc[0] if isinstance(gdf, gpd.GeoDataFrame) else None
    return {"Point": "point", "Polygon": "polygon", "MultiPolygon": "polygon"}.get(kind)


def write_gpkg(layers, path, style_cfg):
    path.unlink(missing_ok=True)
    for name, df in layers.items():
        if isinstance(df, gpd.GeoDataFrame):
            pyogrio.write_dataframe(df, path, layer=name, driver="GPKG", use_arrow=True)
        else:  # attribute-only table
            pyogrio.write_dataframe(df, path, layer=name, driver="GPKG")
    qmls = {name: styles.qml_for_layer(name, geometry_kind(df), style_cfg) for name, df in layers.items()}
    embed_styles(path, {k: v for k, v in qmls.items() if v})
    return qmls


def embed_styles(gpkg, qmls):
    """Store default styles in the GeoPackage layer_styles table so QGIS applies them on open."""
    with sqlite3.connect(gpkg) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT(256), f_table_schema TEXT(256),
            f_table_name TEXT(256), f_geometry_column TEXT(256), styleName TEXT(30), styleQML TEXT,
            styleSLD TEXT, useAsDefault BOOLEAN, description TEXT, owner TEXT(30), ui TEXT(30),
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        # GeoPackage readers (GDAL/QGIS) only see tables registered in gpkg_contents.
        con.execute("""INSERT OR IGNORE INTO gpkg_contents (table_name, data_type, identifier, description)
                       VALUES ('layer_styles', 'attributes', 'layer_styles', 'QGIS layer styles')""")
        for name, qml in qmls.items():
            con.execute("""INSERT INTO layer_styles (f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                           styleName, styleQML, styleSLD, useAsDefault, description, owner, update_time)
                           VALUES ('', '', ?, 'geom', ?, ?, '', 1, 'Default style (gis/styles.py)', '',
                                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
                        (name, name, qml))


def wells_csv(wells):
    table = pd.DataFrame(wells.drop(columns="geometry"))
    table["x_epsg3400"] = wells.geometry.x.round(3)
    table["y_epsg3400"] = wells.geometry.y.round(3)
    return table


# --- Documents ----------------------------------------------------------------------------

def load_dictionary():
    with open(DICTIONARY, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["fields"]


def data_dictionary(layers, csv_columns, dictionary):
    lines = ["# Data dictionary", "",
             "CRS of every spatial layer: NAD83 / Alberta 10-TM (Forest), EPSG:3400 (metres).", ""]
    missing = []
    sections = [(name, [c for c in df.columns if c != "geometry"], geometry_kind(df) or "none (table)")
                for name, df in layers.items()]
    sections.append(("wells.csv", csv_columns, "none (CSV; coordinates in x_epsg3400 / y_epsg3400)"))
    for name, columns, geom in sections:
        lines += [f"## {name}", "", f"Geometry: {geom}.", "", "| Field | Type / unit | Description |", "|---|---|---|"]
        for col in columns:
            if col not in dictionary:
                missing.append(f"{name}.{col}")
                continue
            kind, text = dictionary[col]
            lines.append(f"| `{col}` | {kind} | {text} |")
        lines.append("")
    if missing:
        raise ValueError(f"Fields without a description in {DICTIONARY.name}: {missing}")
    return "\n".join(lines)


def readme(src, layers, selection_type, selection_value, pcfg):
    wells, qa_flags = layers["wells"], layers["qa_flags"]
    first, last = src.petrinex_months
    rows = [f"| `{name}` | {geometry_kind(df) or 'table'} | {len(df):,} |" for name, df in layers.items()]
    classes = wells["well_class"].value_counts()
    qa = (qa_flags.groupby(["rule_id", "rule_name", "severity"]).size().rename("records").reset_index()
          if not qa_flags.empty else pd.DataFrame(columns=["rule_id", "rule_name", "severity", "records"]))
    lines = [
        f"# Well data package: {selection_value}", "",
        f"Selection: {selection_type} = **{selection_value}**. Generated {date.today().isoformat()} "
        f"by `python -m gis.package` (Alberta Well Production Analytics Platform, GIS track).", "",
        "## As-of dates", "",
        f"- AER ST37 wells: latest licence status date in the data is {src.st37_latest_status.isoformat()} "
        "(future-dated records excluded; see QA rule GQA-23).",
        f"- Petrinex volumes: {first} to {last} (as-of month {last}). Production columns cover these 12 months.", "",
        "## Coordinate reference system", "",
        "NAD83 / Alberta 10-TM (Forest), EPSG:3400, metres. Latitude/longitude attributes are NAD83 (EPSG:4269).", "",
        "## Contents", "",
        "| Layer | Geometry | Rows |", "|---|---|---:|", *rows,
        f"| `wells.csv` | none | {len(wells):,} |", "",
        "Open `data.gpkg` in QGIS: styles are embedded and load automatically. The `.qml` files hold the same styles "
        "(Layer Properties > Style > Load Style). Field definitions: `data_dictionary.md`.", "",
        "## Wells by screening class", "",
        "| Class | Wells |", "|---|---:|", *[f"| {k} | {v:,} |" for k, v in classes.items()], "",
        f"Inactive candidates: {int(wells['inactive_candidate_6m'].sum()):,} (6 months), "
        f"{int(wells['inactive_candidate_12m'].sum()):,} (12 months).", "",
        "## Data quality summary", "",
        f"{int(wells['qa_pass'].fillna(True).sum()):,} of {len(wells):,} wells "
        f"({100 * wells['qa_pass'].fillna(True).mean():.2f}%) pass every FAIL and WARN rule. "
        "Records flagged for this package's licences:", "",
        "| Rule | Name | Severity | Records |", "|---|---|---|---:|",
        *[f"| {r.rule_id} | {r.rule_name} | {r.severity} | {r.records:,} |" for r in qa.itertuples()], "",
        "Rules and severities: `config/gis_qa.yaml` in the project repository. INFO rules describe known source "
        "characteristics and do not count against a well.", "",
        "## Sources", "",
        "| Source | Content | Format |", "|---|---|---|",
        "| Alberta Energy Regulator, ST37 List of Wells in Alberta | Wells, licences, licensees, status, locations | File Geodatabase |",
        "| Petrinex public data, Alberta volumetrics | Monthly well production, injection and disposal volumes | CSV |",
        "| Statistics Canada, 2021 Census boundary files (provinces) | Alberta boundary | Shapefile |", "",
        "Use of the source data is subject to each provider's terms.", "",
        "## Known issues", "", *[f"- {issue}" for issue in pcfg["known_issues"]], "",
    ]
    return "\n".join(lines)


# --- Build --------------------------------------------------------------------------------

def package_root(cfg, pcfg):
    """Full-province packages go to deliverables/; sample packages stay under gis/output/sample/."""
    return repo_path(pcfg["output_root"]) if cfg["run"]["mode"] == "full" else output_dir(cfg) / "deliverables"


def build(src, pcfg, style_cfg, dictionary, name=None, root=None, **selection):
    chosen, sel_type, sel_value, extent = select(src, buffer_m=pcfg["extent_buffer_m"], **selection)
    out = (root or package_root(src.cfg, pcfg)) / (name or slug(sel_value))
    out.mkdir(parents=True, exist_ok=True)
    layers = package_layers(src, chosen, sel_type, sel_value, extent, pcfg)
    csv = wells_csv(layers["wells"])
    (out / "data_dictionary.md").write_text(data_dictionary(layers, list(csv.columns), dictionary), encoding="utf-8")
    qmls = write_gpkg(layers, out / "data.gpkg", style_cfg)
    for layer, qml in qmls.items():
        if qml:
            (out / f"{layer}.qml").write_text(qml, encoding="utf-8")
    csv.to_csv(out / "wells.csv", index=False, encoding="utf-8")
    (out / "README.md").write_text(readme(src, layers, sel_type, sel_value, pcfg), encoding="utf-8")
    log.info("Package %s: %s wells, %s pads, %s QA records -> %s", sel_value, f"{len(layers['wells']):,}",
             f"{len(layers['pads']):,}", f"{len(layers['qa_flags']):,}", out)
    return out


def top_licensees(src, n):
    return src.wells["licensee"].value_counts().head(n).index.tolist()


def run(cfg, top_n):
    """Packages for the top_n licensees by licence count (used by gis.run)."""
    src = Sources(cfg)
    pcfg, style_cfg, dictionary = load_package_config(), styles.load_style_config(), load_dictionary()
    return [build(src, pcfg, style_cfg, dictionary, licensee=name) for name in top_licensees(src, top_n)]


def load_package_config(path=DEFAULT_PACKAGE_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    parser = base_arg_parser(__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--licensee")
    group.add_argument("--townships", help="Comma-separated township keys, e.g. 076-06W4,095-06W4")
    group.add_argument("--aoi", help="Polygon file (GeoPackage, Shapefile, GeoJSON) in any CRS")
    group.add_argument("--top-licensees", type=int, help="Build one package for each of the N largest licensees")
    parser.add_argument("--aoi-layer")
    parser.add_argument("--name", help="Output folder name under deliverables/")
    args = parser.parse_args()
    cfg = load_config(args.config, args.mode)
    started = datetime.now(timezone.utc)
    src = Sources(cfg)
    pcfg, style_cfg, dictionary = load_package_config(), styles.load_style_config(), load_dictionary()
    if args.top_licensees:
        for licensee in top_licensees(src, args.top_licensees):
            build(src, pcfg, style_cfg, dictionary, licensee=licensee)
    else:
        build(src, pcfg, style_cfg, dictionary, name=args.name, licensee=args.licensee, townships=args.townships,
              aoi=args.aoi, aoi_layer=args.aoi_layer)
    log.info("Done in %.0f s", (datetime.now(timezone.utc) - started).total_seconds())


if __name__ == "__main__":
    sys.exit(main())
