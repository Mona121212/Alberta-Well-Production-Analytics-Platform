"""Run the whole GIS track with one command, with a run log and a run manifest.

Steps (config/gis_run.yaml): stage -> convert -> qa -> liability -> analysis -> packages -> changes -> maps.
A FAIL-level QA rule or a failed reconciliation stops the run with exit code 1. Optional steps (maps)
log a warning and the run continues.

Outputs in gis/output/<mode>/:
- run_log.txt        every log line of the run
- run_manifest.json  run id, git commit, per-step status and runtime, row counts per layer,
                     QA summary and reconciliation results

Usage:
    python -m gis.run --mode sample
    python -m gis.run --mode full [--force-stage] [--steps qa,liability]
    python -m gis.run --mode sample --inject-bad-row      # shows a FAIL rule stopping the run
"""
import json
import logging
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timezone

import pandas as pd
import pyogrio
import yaml

from gis import analysis, changes, convert, liability, maps, package, qa, stage
from gis.common import REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir

log = get_logger("gis.run")
DEFAULT_RUN_CONFIG = REPO_ROOT / "config" / "gis_run.yaml"
STOPPING_ERRORS = (qa.QAFailed, liability.ReconciliationFailed, analysis.ReconciliationFailed)


def step_functions(cfg, rcfg, args):
    def run_changes():
        ccfg = changes.load_changes_config()
        if rcfg["changes"]["snapshot"]:
            changes.take_snapshot(cfg, ccfg, date.today().isoformat())
        if rcfg["changes"]["demo"]:
            changes.run_demo(cfg, ccfg)

    return {
        "stage": lambda: stage.run(cfg, force=args.force_stage),
        "convert": lambda: convert.run(cfg),
        "qa": lambda: qa.run(cfg, qa.load_qa_config(), inject_bad_row=args.inject_bad_row),
        "liability": lambda: liability.run(cfg, liability.load_liability_config()),
        "analysis": lambda: analysis.run(cfg, analysis.load_analysis_config()),
        "packages": lambda: package.run(cfg, rcfg["packages"]["top_licensees"]),
        "changes": run_changes,
        "maps": lambda: maps.run(cfg, maps.load_maps_config()),
    }


def git_state():
    def git(*cmd):
        result = subprocess.run(["git", *cmd], cwd=REPO_ROOT, capture_output=True, text=True)
        return result.stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def layer_counts(cfg, rcfg):
    out, counts = output_dir(cfg), {}
    for file, layers in rcfg["manifest_layers"].items():
        path = out / file
        if path.exists():
            available = {name for name, _ in pyogrio.list_layers(path)}
            counts[file] = {layer: int(pyogrio.read_info(path, layer=layer)["features"])
                            for layer in layers if layer in available}
    return counts


def qa_summary(cfg):
    path = output_dir(cfg) / "qa" / "qa_rule_summary.csv"
    if not path.exists():
        return None
    summary = pd.read_csv(path)
    card = pd.read_csv(output_dir(cfg) / "qa" / "qa_scorecard_by_licensee.csv").iloc[0]
    return {"rules": len(summary), "results": summary["result"].value_counts().to_dict(),
            "failing_rules": summary.loc[summary["rows_failed"] > 0, "rule_id"].tolist(),
            "wells": int(card["wells"]), "wells_passing_fail_warn": int(card["wells_passing"])}


def reconciliation_results(cfg):
    results = []
    for file in ("liability/liability_reconciliation.csv", "analysis/analysis_reconciliation.csv"):
        path = output_dir(cfg) / file
        if path.exists():
            results += pd.read_csv(path).assign(source=file).to_dict("records")
    return results


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--run-config", default=str(DEFAULT_RUN_CONFIG))
    parser.add_argument("--force-stage", action="store_true", help="Re-read the raw GDB and CSVs even if staged")
    parser.add_argument("--steps", help="Comma-separated subset of steps, e.g. qa,liability")
    parser.add_argument("--inject-bad-row", action="store_true",
                        help="Demo: add one well with a wrong location in memory; the QA step must stop the run")
    args = parser.parse_args()
    cfg = load_config(args.config, args.mode)
    with open(args.run_config, encoding="utf-8") as fh:
        rcfg = yaml.safe_load(fh)

    out = output_dir(cfg)
    handler = logging.FileHandler(out / "run_log.txt", mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)

    run_id = uuid.uuid4().hex[:8]
    manifest = {"run_id": run_id, "mode": cfg["run"]["mode"], "started": datetime.now(timezone.utc).isoformat(),
                "git": git_state(), "status": "running", "steps": []}
    steps = args.steps.split(",") if args.steps else rcfg["steps"]
    functions = step_functions(cfg, rcfg, args)
    log.info("Run %s (%s mode): %s", run_id, cfg["run"]["mode"], " -> ".join(steps))
    exit_code = 0
    for name in steps:
        started = time.time()
        entry = {"step": name, "status": "ok"}
        try:
            functions[name]()
        except STOPPING_ERRORS as exc:
            entry.update(status="stopped", error=str(exc))
            manifest["status"], exit_code = "stopped", 1
        except Exception as exc:  # noqa: BLE001 - recorded in the manifest, then re-raised or skipped
            entry.update(status="failed" if name not in rcfg["optional_steps"] else "warning", error=repr(exc))
            log.exception("Step %s failed", name)
            if name not in rcfg["optional_steps"]:
                manifest["status"], exit_code = "failed", 1
        entry["seconds"] = round(time.time() - started, 1)
        manifest["steps"].append(entry)
        log.info("Step %-10s %-8s %7.1f s", name, entry["status"], entry["seconds"])
        if exit_code:
            break

    if manifest["status"] == "running":
        manifest["status"] = "success" if all(s["status"] == "ok" for s in manifest["steps"]) else "success_with_warnings"
    manifest.update(finished=datetime.now(timezone.utc).isoformat(),
                    total_seconds=round(sum(s["seconds"] for s in manifest["steps"]), 1),
                    row_counts=layer_counts(cfg, rcfg), qa=qa_summary(cfg),
                    reconciliation=reconciliation_results(cfg))
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    log.info("Run %s %s in %.0f s; manifest: %s", run_id, manifest["status"], manifest["total_seconds"],
             out / "run_manifest.json")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
