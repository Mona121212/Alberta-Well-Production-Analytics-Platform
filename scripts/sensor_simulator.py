"""Send simulated PI-style sensor readings to the Fabric Eventstream es_sensor_readings (DEC-033).

Every reading is simulated; this is not AVEVA PI System. A small share of the readings is sent late or twice on
purpose, for Silver to handle. Settings are in the "stream" section of config/sensor.json.

The connection string comes only from the environment variable named in the config. It is never printed or written.
A real run writes a manifest with the expected counts to data/sensor/stream_runs/ (not in Git); upload it and run
sensor_check_stream to reconcile with Bronze:
  fab cp data/sensor/stream_runs/<file>.json "well-production-mvp.Workspace/alberta_wells.Lakehouse/Files/raw/sensor/stream_runs/"
  fab job run "well-production-mvp.Workspace/sensor_check_stream.Notebook"

Run: python scripts/sensor_simulator.py --dry-run --duration-minutes 15
     python scripts/sensor_simulator.py --duration-minutes 15
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sensor.config import REPO_ROOT, load_config  # noqa: E402
from sensor.stream import EventHubSender, build_schedule, format_utc, redact, run_stream  # noqa: E402


def utc_now():
    return datetime.now(timezone.utc)


def real_run(cfg, schedule, start, manifest, out_dir, conn_str):
    # Imported here so a dry run needs neither the package nor a connection string
    from azure.eventhub import EventData, EventHubProducerClient

    path = Path(out_dir) / f"stream_run_{manifest['run_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    interval = cfg["stream"]["sample_interval_seconds"]
    counted = {"sent": 0}

    manifest["status"] = "FAILED"
    try:
        producer = EventHubProducerClient.from_connection_string(conn_str)
        sender = EventHubSender(producer, EventData)

        def send(events):
            sender(events)
            counted["sent"] += len(events)
            print(f"{format_utc(utc_now())} sent {len(events)} events, total {counted['sent']}", flush=True)

        with producer:
            run_stream(schedule, start, interval, send, utc_now, time.sleep)
        manifest["status"] = "COMPLETED"
    except KeyboardInterrupt:
        manifest["status"] = "INTERRUPTED"
    except Exception as exc:  # the message may quote connection details
        manifest["error"] = f"{type(exc).__name__}: {redact(exc, conn_str)}"
        print("Send failed:", manifest["error"], file=sys.stderr)
    finally:
        manifest["sent_events"] = counted["sent"]
        manifest["ended_utc"] = format_utc(utc_now())
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Manifest:", path)
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["status"] == "COMPLETED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration-minutes", type=float, default=15)
    parser.add_argument("--dry-run", action="store_true", help="plan the run and print the counts; nothing is sent")
    parser.add_argument("--seed", type=int, help="overrides the seed in the config")
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "sensor" / "stream_runs"))
    args = parser.parse_args()
    if args.duration_minutes <= 0:
        parser.error("--duration-minutes must be positive")

    cfg = load_config()
    stream = cfg["stream"]
    conn_str = None
    if not args.dry_run:
        conn_str = os.environ.get(stream["connection_string_env"])
        if not conn_str:
            sys.exit(f"Environment variable {stream['connection_string_env']} is not set.")

    start = utc_now().replace(microsecond=0)
    run_id = start.strftime("%Y%m%dT%H%M%SZ")
    schedule, expected = build_schedule(cfg, start, args.duration_minutes, run_id, args.seed)
    manifest = {"run_id": run_id, "started_utc": format_utc(start), "duration_minutes": args.duration_minutes,
                "sample_interval_seconds": stream["sample_interval_seconds"], "eventstream": stream["eventstream"],
                "bronze_table": stream["bronze_table"], "expected": expected}
    if args.dry_run:
        print(json.dumps({**manifest, "status": "DRY_RUN", "sent_events": 0}, indent=2))
        print("Sample event:", json.dumps(schedule[0][0]))
        return 0
    return real_run(cfg, schedule, start, manifest, args.out, conn_str)


if __name__ == "__main__":
    sys.exit(main())
