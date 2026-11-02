"""Generate the PI-style simulated sensor backfill for the configured month (DEC-032). All readings are simulated.

Writes to data/sensor/ (not in Git):
- backfill_<month>.parquet: tag_name, ts_utc (UTC), value, source
- sensor_asset_hierarchy.parquet: facility -> well -> tag nodes with AF style paths
- backfill_<month>_truth.json: true volumes, downtime events and deliberate bad readings, for later checks

Then upload both parquet files to the Lakehouse and run sensor_load_backfill:
  fab cp data/sensor/backfill_2025-11.parquet "well-production-mvp.Workspace/alberta_wells.Lakehouse/Files/raw/sensor/"
  fab cp data/sensor/sensor_asset_hierarchy.parquet "well-production-mvp.Workspace/alberta_wells.Lakehouse/Files/raw/sensor/"
The truth file goes to the same folder; 04_sensor_silver and 05_sensor_gold test against it (DEC-034).

Run: python scripts/sensor_backfill.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sensor.config import REPO_ROOT, hour_grid_utc, load_config  # noqa: E402
from sensor.generate import generate_backfill  # noqa: E402
from sensor.hierarchy import build_hierarchy  # noqa: E402
from sensor.quality import classify  # noqa: E402
from sensor.reconcile import reconcile  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "sensor"))
    args = parser.parse_args()

    cfg = load_config()
    month = cfg["month"]["production_month"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    grid = hour_grid_utc(cfg)
    hierarchy = build_hierarchy(cfg)
    readings, truth = generate_backfill(cfg)
    truth["month"] = {"production_month": month, "hours": len(grid), "start_utc": grid[0].isoformat(),
                      "end_utc_exclusive": (grid[-1] + (grid[1] - grid[0])).isoformat()}

    readings.to_parquet(out / f"backfill_{month}.parquet", index=False, coerce_timestamps="us")
    hierarchy.to_parquet(out / "sensor_asset_hierarchy.parquet", index=False)
    (out / f"backfill_{month}_truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")

    classified = classify(readings, hierarchy, grid, cfg)
    rec = reconcile(classified, cfg)
    print(f"{len(grid)} hours, {len(hierarchy[hierarchy.node_type == 'tag'])} tags, {len(readings):,} readings")
    print("quality:", classified["quality"].value_counts().to_dict())
    print("wells to investigate (simulated anomalies):", sorted(rec.loc[rec.needs_investigation, "well_key"]))
    print("wrote", out)


if __name__ == "__main__":
    main()
