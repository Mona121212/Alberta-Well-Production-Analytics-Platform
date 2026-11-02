"""Live stream of simulated sensor readings for Fabric Eventstream (DEC-033). All readings are simulated.

The whole run is planned up front from a seed, so a dry run and the tests see exactly what a real run sends. A small,
exact share of the readings is sent late (the timestamp stays, the send is delayed) or sent twice (same tag_name and
ts_utc, same value). The events carry no flag saying so, as with a real feed: Silver has to detect both from the key
and the timestamps, and the run manifest holds the expected counts.
"""
import json
import math
import re
from datetime import timedelta

import numpy as np

from sensor.config import flow_code, tag_name, well_tag_codes


def format_utc(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def reading_values(rng, well, cfg):
    """One running well's values at one instant, using the same base levels as the backfill."""
    g = cfg["generator"]
    p = g["pressure_kpa"][well["product"]]
    pn = g["pressure_noise_pct"] / 100
    t = g["temperature_degc"]
    rate = well["petrinex_volume"] / well["petrinex_hours"] * 24
    flow = rate * max(1 + rng.normal(0, g["flow_hourly_noise_pct"] / 100), 0.5)
    values = {"TP": p["tubing"] * (1 + rng.normal(0, pn)), "CP": p["casing"] * (1 + rng.normal(0, pn)),
              "TEMP": t[well["product"]] + rng.normal(0, t["noise"]), flow_code(well): flow, "RUN": 1.0}
    return {code: float(round(v, g["decimals"][code])) for code, v in values.items()}


def build_schedule(cfg, start_utc, duration_minutes, run_id, seed=None):
    """Events to send at each tick of the run, as a list with one list of events per tick, and the expected counts."""
    s = cfg["stream"]
    interval = s["sample_interval_seconds"]
    n_ticks = max(1, math.floor(duration_minutes * 60 / interval))
    last = n_ticks - 1
    rng = np.random.default_rng([cfg["seed"] if seed is None else seed, n_ticks])

    readings = []
    for tick in range(n_ticks):
        ts = format_utc(start_utc + timedelta(seconds=tick * interval))
        for well in cfg["wells"]:
            values = reading_values(rng, well, cfg)
            for code in well_tag_codes(cfg, well):
                readings.append((tick, {"tag_name": tag_name(cfg, well, code), "ts_utc": ts, "value": values[code],
                                        "source": s["source"], "run_id": run_id}))

    # Only a reading whose delay still fits inside the run can really arrive late
    late_min, late_max = s["late_delay_samples"]
    eligible = [i for i, (tick, _) in enumerate(readings) if tick + late_min <= last]
    n_late = min(len(eligible), round(len(readings) * s["late_pct"] / 100))
    late = set(rng.choice(eligible, n_late, replace=False).tolist()) if n_late else set()
    others = [i for i in range(len(readings)) if i not in late]
    n_dup = round(len(readings) * s["duplicate_pct"] / 100)
    duplicated = set(rng.choice(others, n_dup, replace=False).tolist()) if n_dup else set()

    schedule = [[] for _ in range(n_ticks)]
    for i, (tick, event) in enumerate(readings):
        if i in late:
            schedule[min(tick + int(rng.integers(late_min, late_max + 1)), last)].append(event)
        else:
            schedule[tick].append(event)
        if i in duplicated:
            delay = int(rng.integers(s["duplicate_delay_samples"][0], s["duplicate_delay_samples"][1] + 1))
            schedule[min(tick + delay, last)].append(event)

    expected = {"events": sum(len(batch) for batch in schedule), "unique_readings": len(readings),
                "late": len(late), "duplicates": len(duplicated),
                "late_threshold_seconds": late_min * interval,
                "value_sum": round(sum(e["value"] for batch in schedule for e in batch), 3)}
    return schedule, expected


def run_stream(schedule, start_utc, interval_seconds, send, now, sleep):
    """Send each tick's events on time, each stamped with its send time. Returns the number of events sent."""
    sent = 0
    for tick, events in enumerate(schedule):
        due = start_utc + timedelta(seconds=tick * interval_seconds)
        wait = (due - now()).total_seconds()
        if wait > 0:
            sleep(wait)
        sent_utc = format_utc(now())
        send([{**event, "sent_utc": sent_utc} for event in events])
        sent += len(events)
    return sent


def redact(text, secret=None):
    """Remove the connection string and anything that looks like an access key from an error message."""
    text = str(text)
    if secret:
        text = text.replace(secret, "[redacted]")
    return re.sub(r"(?i)(SharedAccessKey(Name)?|Endpoint)\s*=\s*[^;\s]+", r"\1=[redacted]", text)


class EventHubSender:
    """Sends events as JSON in as many batches as they need. The producer is an azure-eventhub EventHubProducerClient."""

    def __init__(self, producer, event_data):
        self.producer = producer
        self.event_data = event_data

    def __call__(self, events):
        batch = self.producer.create_batch()
        pending = 0
        for event in events:
            data = self.event_data(json.dumps(event))
            try:
                batch.add(data)
            except ValueError:  # batch full
                self.producer.send_batch(batch)
                batch = self.producer.create_batch()
                pending = 0
                batch.add(data)
            pending += 1
        if pending:
            self.producer.send_batch(batch)
