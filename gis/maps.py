"""Prepare map layers and styles, then render the QGIS project and PNG maps with PyQGIS.

PyQGIS runs in QGIS's own Python (no geopandas), so this step is split in two:
1. this module (project venv): map-ready layers in gis/output/<mode>/maps/map_layers.gpkg, .qml styles
   from config/gis_styles.yaml and a JSON spec;
2. gis/qgis_maps.py, started with QGIS's python-qgis-ltr.bat: builds gis/alberta_wells.qgz and exports
   one print layout per map to docs/maps/*.png (title, legend, scale bar, north arrow, source/CRS note).

Usage:
    python -m gis.maps [--mode full]
"""
import json
import subprocess
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
import yaml

from gis import analysis, styles
from gis.common import REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir, repo_path, reset_gpkg

log = get_logger("gis.maps")
DEFAULT_MAPS_CONFIG = REPO_ROOT / "config" / "gis_maps.yaml"
QGIS_SCRIPT = REPO_ROOT / "gis" / "qgis_maps.py"


def load_maps_config(path=DEFAULT_MAPS_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def qa_failure_points(out):
    """Every located failure of a FAIL or WARN rule, as points (lines -> their first vertex)."""
    summary = pd.read_csv(out / "qa" / "qa_rule_summary.csv")
    rules = summary[summary["severity"].isin(["FAIL", "WARN"]) & (summary["rows_failed"] > 0)]
    gpkg = out / "qa" / "qa_results.gpkg"
    available = {name for name, _ in pyogrio.list_layers(gpkg)}
    frames = []
    for r in rules.itertuples():
        layer = f"{r.rule_id.lower().replace('-', '')}_{r.rule_name}"
        if layer not in available:
            continue
        gdf = pyogrio.read_dataframe(gpkg, layer=layer)
        geom = gdf.geometry.to_numpy()
        lines = shapely.get_type_id(geom) == 1
        geom[lines] = shapely.get_point(geom[lines], 0)
        frames.append(gpd.GeoDataFrame({"rule_id": r.rule_id, "rule_label": f"{r.rule_id} {r.rule_name}",
                                        "severity": r.severity, "record_key": gdf["record_key"],
                                        "detail": gdf["detail"]},
                                       geometry=shapely.force_2d(geom), crs=gdf.crs))
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)


def dominant_class_hexagons(out, hexes):
    """Most common screening class among drilled wells in each hexagon (same cells as gis.analysis)."""
    acfg = analysis.load_analysis_config()
    wells = analysis.drilled(pyogrio.read_dataframe(out / "liability" / "liability.gpkg", layer="wells_liability",
                                                    columns=["well_class", "licence_status_group", "spud_date"],
                                                    use_arrow=True), acfg)
    size = acfg["hexagons"]["width_m"] / analysis.SQRT3
    q, r = analysis.hex_index(wells.geometry.x.to_numpy(), wells.geometry.y.to_numpy(), size)
    wells["hex_id"] = pd.Series(q).astype(str).to_numpy() + "_" + pd.Series(r).astype(str).to_numpy()
    counts = wells.groupby(["hex_id", "well_class"]).size().rename("n").reset_index()
    counts = counts.sort_values(["hex_id", "n"], ascending=[True, False])
    top = counts.drop_duplicates("hex_id").set_index("hex_id")
    total = counts.groupby("hex_id")["n"].sum()
    result = hexes[["hex_id", "wells", "geometry"]].merge(top, left_on="hex_id", right_index=True)
    result = result.rename(columns={"well_class": "dominant_class", "n": "dominant_wells"})
    result["dominant_share"] = (result["dominant_wells"] / result["hex_id"].map(total)).round(3)
    return result


def prepare(cfg, mcfg, style_cfg):
    out = output_dir(cfg)
    maps_dir = out / "maps"
    (maps_dir / "styles").mkdir(parents=True, exist_ok=True)
    layers_gpkg = maps_dir / "map_layers.gpkg"
    reset_gpkg(layers_gpkg, log)

    analysis = out / "analysis" / "analysis.gpkg"
    hexes = pyogrio.read_dataframe(analysis, layer="hexagons_5km")
    towns = pyogrio.read_dataframe(analysis, layer="townships")
    qa = qa_failure_points(out)
    package_dir = repo_path(f"deliverables/{mcfg['package_example']}")
    package_name = pyogrio.read_dataframe(package_dir / "data.gpkg", layer="package_extent")["selection_value"].iat[0]

    prepared = {"hexagons": hexes[hexes["wells"] > 0], "townships_abandoned": towns[towns["abandoned_not_reclaimed"] > 0],
                "hexagons_dominant_class": dominant_class_hexagons(out, hexes[hexes["wells"] > 0]),
                "context_townships": towns[["township", "geometry"]], "qa_failures": qa}
    for name, gdf in prepared.items():
        pyogrio.write_dataframe(gdf, layers_gpkg, layer=name, driver="GPKG", use_arrow=True)

    def entry(path, layer, style_key, legend_name, legend=True, qml=None):
        qml_path = maps_dir / "styles" / f"{style_key}.qml"
        geometry = {"Point": "point", "MultiPoint": "point"}.get(pyogrio.read_info(path, layer=layer)["geometry_type"],
                                                                     "polygon")
        qml_path.write_text(qml or styles.qml_for_layer(style_key, geometry, style_cfg), encoding="utf-8")
        return {"path": str(path), "layer": layer, "qml": str(qml_path), "name": legend_name, "legend": legend}

    qa_spec = style_cfg["layers"]["qa_failures_map"]
    rule_labels = qa.drop_duplicates("rule_id").sort_values("rule_id")
    qa_qml = styles.categorized(qa_spec["field"], [
        (r.rule_id, f"{r.rule_label} ({(qa['rule_id'] == r.rule_id).sum()})", qa_spec["colours"][i % len(qa_spec["colours"])])
        for i, r in enumerate(rule_labels.itertuples())], "point", qa_spec["size"], other=False)

    base_gpkg = out / cfg["exports"]["geopackage"]
    registry = {
        "boundary": entry(base_gpkg, "alberta_boundary", "alberta_boundary", "Alberta boundary"),
        "hexagons": entry(layers_gpkg, "hexagons", "hexagon_density", "Drilled wells per 5 km hexagon"),
        "townships_abandoned": entry(layers_gpkg, "townships_abandoned", "township_abandoned",
                                     "Abandoned, not reclaimed wells per township"),
        "context_townships": entry(layers_gpkg, "context_townships", "context_townships", "Townships", legend=False),
        "wells_liability": entry(out / "liability" / "liability.gpkg", "wells_liability", "wells_map", "Screening class"),
        "hexagons_dominant_class": entry(layers_gpkg, "hexagons_dominant_class", "hexagon_dominant_class",
                                         "Most common class per hexagon"),
        "qa_failures": entry(layers_gpkg, "qa_failures", "qa_failures_map", "QA rule (located failures)", qml=qa_qml),
        "package_wells": entry(package_dir / "data.gpkg", "wells", "wells_map", "Package wells by class"),
        "package_pads": entry(package_dir / "data.gpkg", "pads", "pads", "Pads (wells per pad)"),
        "package_extent": entry(package_dir / "data.gpkg", "package_extent", "package_extent", "Package extent"),
    }
    numbers = {"drilled_wells": f"{int(hexes['wells'].sum()):,}",
               "abandoned": f"{int(towns['abandoned_not_reclaimed'].sum()):,}",
               "licences": f"{pyogrio.read_info(out / 'liability' / 'liability.gpkg', layer='wells_liability')['features']:,}",
               "qa_points": f"{len(qa):,}", "package": package_name, "folder": mcfg["package_example"]}
    maps = []
    for m in mcfg["maps"]:
        maps.append({"name": m["name"], "title": m["title"].format(**numbers), "subtitle": m["subtitle"].format(**numbers),
                     "layers": m["layers"], "extent": m.get("extent", "boundary"),
                     "png": str(repo_path(mcfg["png_dir"]) / f"{m['name']}.png")})
    spec = {"prefix": mcfg["qgis_prefix"], "project": str(repo_path(mcfg["project"])), "dpi": mcfg["dpi"],
            "note": mcfg["source_note"], "crs": cfg["target_crs"], "layers": registry, "maps": maps}
    spec_path = maps_dir / "map_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return spec_path, spec


def render(mcfg, spec_path, spec):
    Path(spec["maps"][0]["png"]).parent.mkdir(parents=True, exist_ok=True)
    qgis_python = Path(mcfg["qgis_python"])
    if not qgis_python.exists():
        raise FileNotFoundError(f"QGIS Python launcher not found: {qgis_python} (set qgis_python in gis_maps.yaml)")
    result = subprocess.run([str(qgis_python), str(QGIS_SCRIPT), str(spec_path)], capture_output=True, text=True,
                            timeout=900)
    for line in (result.stdout + result.stderr).splitlines():
        # GDAL "PNG driver does not support update access" comes from QGIS's georeferencing pass; harmless.
        if line.strip() and not line.startswith(("Warning 1: Non-conformant", "ERROR 6: The PNG driver")):
            log.info("qgis: %s", line)
    missing = [m["png"] for m in spec["maps"] if not Path(m["png"]).exists()]
    if missing or "ERROR Traceback" in result.stdout or result.stdout.startswith("ERROR "):
        raise RuntimeError(f"QGIS rendering failed (exit {result.returncode}); missing: {missing}")
    log.info("Maps: %s; project: %s", [Path(m["png"]).name for m in spec["maps"]], spec["project"])


def run(cfg, mcfg):
    spec_path, spec = prepare(cfg, mcfg, styles.load_style_config())
    render(mcfg, spec_path, spec)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--maps-config", default=str(DEFAULT_MAPS_CONFIG))
    args = parser.parse_args()
    run(load_config(args.config, args.mode), load_maps_config(args.maps_config))


if __name__ == "__main__":
    main()
