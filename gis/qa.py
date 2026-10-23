"""Run the config-driven GIS QA rules on the standardized GeoPackage.

Outputs in gis/output/<mode>/qa/:
- qa_defect_log.csv            one row per rule, same columns as the Lakehouse defect_log table
- qa_rule_summary.csv          rows checked / failed and PASS/FAIL/WARN/INFO per rule
- qa_failures.csv              every failed record (no geometry)
- qa_examples.md               the first examples_per_rule failures per rule
- qa_scorecard_by_licensee.csv share of wells passing all FAIL/WARN rules, by licensee
- qa_results.gpkg              one layer per rule with failures, plus wells_qa (every well with its QA flags)
- gdb_vs_excel_value_pairs.csv value pairs that differ between the GDB and the Excel extract

A FAIL rule with more than max_failures failed rows stops the pipeline (exit code 1)
after all outputs are written.

Usage:
    python -m gis.qa [--mode sample|full] [--qa-config config/gis_qa.yaml]
"""
import sys
import time
from datetime import date
from functools import cached_property

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
import yaml

from gis.common import (REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir, repo_path, reset_gpkg,
                        staging_dir)
from gis.convert import standardize_attributes
from gis.qa_checks import CHECKS, FAILURE_COLUMNS

log = get_logger("gis.qa")
DEFAULT_QA_CONFIG = REPO_ROOT / "config" / "gis_qa.yaml"
ALL_LICENSEES = "(all licensees)"


class QAFailed(Exception):
    """Raised when at least one FAIL-severity rule exceeds its max_failures."""


INJECTED_LICENCE = "INJECTED"


def inject_bad_row(wells, east_edge_x, shift_m=50_000):
    """Demo/test only: copy the first well, move its geometry shift_m east of the province's eastern edge
    (east_edge_x) and keep its lat/long attributes, so the FAIL rule surface_latlong_mismatch must stop the run."""
    bad = wells.iloc[[0]].copy()
    bad["licence_number"] = INJECTED_LICENCE
    bad["geometry"] = bad.geometry.translate(xoff=east_edge_x + shift_m - bad.geometry.x.iat[0])
    return gpd.GeoDataFrame(pd.concat([wells, bad], ignore_index=True), crs=wells.crs)


class QAContext:
    """Lazy access to everything the checks need. Layers are read once and cached."""

    def __init__(self, cfg, qa_cfg, inject_bad_row=False):
        self.cfg = cfg
        self.crs = cfg["target_crs"]
        self.gpkg = output_dir(cfg) / cfg["exports"]["geopackage"]
        self.stage = staging_dir(cfg)
        self.record_keys = qa_cfg["record_keys"]
        self.layer_cfg_by_output = {c["output_layer"]: c for c in cfg["layers"].values()}
        self.layer_key_by_output = {c["output_layer"]: k for k, c in cfg["layers"].items()}
        self.side_outputs = {}
        self.inject_layer = cfg["layers"][cfg["wells_layer"]]["output_layer"] if inject_bad_row else None
        self._layers, self._references = {}, {}

    @staticmethod
    def repo_path(relative):
        return repo_path(relative)

    def layer(self, name):
        if name not in self._layers:
            self._layers[name] = pyogrio.read_dataframe(self.gpkg, layer=name, use_arrow=True)
            if name == self.inject_layer:
                self._layers[name] = inject_bad_row(self._layers[name], self.boundary.bounds[2])
                log.warning("Injected one bad well (%s) into %s for the stop demo", INJECTED_LICENCE, name)
        return self._layers[name]

    def reference(self, name):
        """Full-province key/status table for a layer, standardized like the layer itself."""
        if name not in self._references:
            df = pd.read_parquet(self.stage / f"{self.layer_key_by_output[name]}_reference.parquet")
            self._references[name] = standardize_attributes(df, self.layer_cfg_by_output[name]["rename"])
        return self._references[name]

    @cached_property
    def boundary(self):
        boundary = pyogrio.read_dataframe(self.gpkg, layer=self.cfg["boundary"]["output_layer"])
        geom = boundary.geometry.union_all()
        shapely.prepare(geom)
        return geom

    @cached_property
    def excel_sample(self):
        layer = self.cfg["layers"][self.cfg["excel_sample"]["layer"]]
        return standardize_attributes(pd.read_parquet(self.stage / "excel_sample.parquet"), layer["rename"])


def evaluate(rule, result):
    failed = len(result.failures)
    if failed == 0:
        return "PASS"
    if rule["severity"] == "FAIL":
        return "FAIL" if failed > rule.get("max_failures", 0) else "WARN"
    return rule["severity"]


def run_rules(ctx, qa_cfg):
    summaries, failures = [], []
    for rule in qa_cfg["rules"]:
        started = time.time()
        result = CHECKS[rule["check"]](ctx, rule["layer"], rule.get("params", {}))
        outcome = evaluate(rule, result)
        rows_failed = len(result.failures)
        summaries.append({
            "rule_id": rule["id"], "rule_name": rule["name"], "layer": rule["layer"], "check": rule["check"],
            "severity": rule["severity"], "rows_checked": result.rows_checked, "rows_failed": rows_failed,
            "pct_failed": round(100 * rows_failed / result.rows_checked, 3) if result.rows_checked else None,
            "max_failures": rule.get("max_failures"), "result": outcome, "description": rule["description"],
            "seconds": round(time.time() - started, 2),
        })
        failures.append(result.failures.assign(
            rule_id=rule["id"], rule_name=rule["name"], severity=rule["severity"], layer=rule["layer"]))
        log.info("%s %-45s %-5s %s of %s failed", rule["id"], rule["name"], outcome,
                 f"{rows_failed:,}", f"{result.rows_checked:,}")
    all_failures = gpd.GeoDataFrame(pd.concat(failures, ignore_index=True), crs=ctx.crs)
    return pd.DataFrame(summaries), all_failures


def to_defect_log(summary, qa_cfg):
    """Same schema as the Lakehouse defect_log table (notebooks/load_defect_log.ipynb)."""
    labels = qa_cfg["defect_log"]["severity_labels"]
    return pd.DataFrame({
        "defect_id": summary["rule_id"].str.extract(r"(\d+)$")[0].astype(int),
        "found_date": date.today().isoformat(),
        "area": qa_cfg["defect_log"]["area_prefix"] + ":" + summary["layer"],
        "severity": summary["severity"].map(labels),
        "status": summary["rows_failed"].gt(0).map({True: "Open", False: "Closed"}),
        "description": [
            f"{r.rule_id} {r.rule_name} [{r.result}]: {r.rows_failed:,} of {r.rows_checked:,} rows failed. {r.description}"
            for r in summary.itertuples()],
    })


def wells_with_flags(ctx, failures, qa_cfg):
    wells = ctx.layer(ctx.cfg["layers"][ctx.cfg["wells_layer"]]["output_layer"]).copy()
    # One row per (licence, rule); object dtype keeps groupby vectorized (Arrow strings fall back to Python).
    flagged = (failures.loc[failures["licence_number"].notna(), ["licence_number", "rule_id", "severity"]]
               .astype(object).drop_duplicates().sort_values(["licence_number", "rule_id"]))
    for severity in ("FAIL", "WARN", "INFO"):
        rules = flagged[flagged["severity"] == severity].groupby("licence_number").size()
        wells[f"n_{severity.lower()}_rules"] = wells["licence_number"].map(rules).fillna(0).astype(int)
    rule_list = flagged.groupby("licence_number")["rule_id"].agg(", ".join)
    wells["flagged_rules"] = wells["licence_number"].map(rule_list)
    blocking = [f"n_{s.lower()}_rules" for s in qa_cfg["scorecard"]["severities"]]
    wells["qa_pass"] = wells[blocking].sum(axis=1).eq(0)
    return wells


def scorecard(wells):
    grouped = wells.assign(licensee=wells["licensee"].fillna("(missing)"))
    total = pd.DataFrame({"licensee": [ALL_LICENSEES], "wells": [len(wells)], "wells_passing": [wells["qa_pass"].sum()],
                          "wells_with_fail": [(wells["n_fail_rules"] > 0).sum()],
                          "wells_with_warn": [(wells["n_warn_rules"] > 0).sum()]})
    by_licensee = grouped.groupby("licensee").agg(
        wells=("licence_number", "size"), wells_passing=("qa_pass", "sum"),
        wells_with_fail=("n_fail_rules", lambda s: (s > 0).sum()),
        wells_with_warn=("n_warn_rules", lambda s: (s > 0).sum()),
    ).reset_index().sort_values(["wells", "licensee"], ascending=[False, True])
    card = pd.concat([total, by_licensee], ignore_index=True)
    card["pct_passing"] = (100 * card["wells_passing"] / card["wells"]).round(1)
    return card


def examples_markdown(summary, failures, n, mode):
    lines = [f"# GIS QA examples ({mode} run, {date.today().isoformat()})", "",
             f"Up to {n} failed records per rule, largest metric first where the rule has one.", ""]
    for r in summary.itertuples():
        lines += [f"## {r.rule_id} {r.rule_name} - {r.result}", "",
                  f"{r.description} Layer `{r.layer}`, severity {r.severity}: "
                  f"{r.rows_failed:,} of {r.rows_checked:,} rows failed.", ""]
        rows = failures[failures["rule_id"] == r.rule_id]
        if rows.empty:
            lines += ["No failures.", ""]
            continue
        if rows["metric"].notna().any():
            rows = rows.sort_values("metric", ascending=False)
        lines += ["| record_key | licence_number | metric | detail |", "|---|---|---:|---|"]
        lines += [f"| {x.record_key} | {x.licence_number} | {'' if pd.isna(x.metric) else f'{x.metric:,.2f}'} | {x.detail} |"
                  for x in rows.head(n).itertuples()]
        lines.append("")
    return "\n".join(lines)


def write_outputs(ctx, qa_cfg, summary, failures, wells, card):
    out = output_dir(ctx.cfg) / "qa"
    out.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out / "qa_rule_summary.csv", index=False)
    to_defect_log(summary, qa_cfg).to_csv(out / "qa_defect_log.csv", index=False)
    columns = ["rule_id", "rule_name", "severity", "layer"] + FAILURE_COLUMNS
    failures[columns].to_csv(out / "qa_failures.csv", index=False)
    card.to_csv(out / "qa_scorecard_by_licensee.csv", index=False)
    (out / "qa_examples.md").write_text(
        examples_markdown(summary, failures, qa_cfg["examples_per_rule"], ctx.cfg["run"]["mode"]), encoding="utf-8")
    for name, table in ctx.side_outputs.items():
        table.to_csv(out / f"{name}.csv", index=False)

    gpkg = out / "qa_results.gpkg"
    reset_gpkg(gpkg, log)
    pyogrio.write_dataframe(wells, gpkg, layer="wells_qa", driver="GPKG", use_arrow=True)
    for r in summary.itertuples():
        rows = failures[(failures["rule_id"] == r.rule_id) & failures.geometry.notna()]
        if not rows.empty:
            layer = f"{r.rule_id.lower().replace('-', '')}_{r.rule_name}"
            pyogrio.write_dataframe(rows[["rule_id", "severity"] + FAILURE_COLUMNS + ["geometry"]], gpkg,
                                    layer=layer, driver="GPKG", use_arrow=True)
    return out


def run(cfg, qa_cfg, inject_bad_row=False):
    ctx = QAContext(cfg, qa_cfg, inject_bad_row)
    summary, failures = run_rules(ctx, qa_cfg)
    wells = wells_with_flags(ctx, failures, qa_cfg)
    card = scorecard(wells)
    out = write_outputs(ctx, qa_cfg, summary, failures, wells, card)
    overall = card.iloc[0]
    log.info("Wells passing all %s rules: %s of %s (%.1f%%)", "/".join(qa_cfg["scorecard"]["severities"]),
             f"{overall.wells_passing:,}", f"{overall.wells:,}", overall.pct_passing)
    log.info("QA outputs: %s", out)

    failed_rules = summary.loc[summary["result"] == "FAIL", "rule_id"].tolist()
    if failed_rules:
        raise QAFailed(f"FAIL rules exceeded max_failures: {', '.join(failed_rules)} (see {out / 'qa_examples.md'})")
    return summary


def load_qa_config(path=DEFAULT_QA_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--qa-config", default=str(DEFAULT_QA_CONFIG), help="Path to gis_qa.yaml")
    args = parser.parse_args()
    try:
        run(load_config(args.config, args.mode), load_qa_config(args.qa_config))
    except QAFailed as exc:
        log.error("QA stopped the pipeline: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
