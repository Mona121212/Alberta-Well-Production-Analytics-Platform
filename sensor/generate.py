"""Hourly backfill of simulated sensor readings for the configured month (DEC-032).

Each well's true monthly volume is its real Petrinex volume x (1 + bias): a small random bias for most wells and the
configured bias for the anomaly wells. Hourly volumes are noisy but rescaled so they add up to that true volume
exactly. Deliberate bad readings (out of range, frozen, missing) replace readings only; the true volume of the hour
is unchanged, so a correct estimate from the Good readings stays close to the true volume.
"""
import numpy as np
import pandas as pd

from sensor.config import flow_code, hour_grid_utc, tag_name, tag_specs, well_tag_codes


def place_downtime(rng, n_hours, well, cfg):
    """Non-overlapping downtime events as (start index, hours), at least 2 running hours apart."""
    dt = cfg["downtime"]
    min_event = dt["min_event_hours"]
    gap = cfg["month"]["expected_hours"] - well["petrinex_hours"]
    total = gap if gap >= min_event else int(rng.integers(dt["random_total_hours"][0], dt["random_total_hours"][1] + 1))
    k = int(rng.integers(dt["events_per_well"][0], dt["events_per_well"][1] + 1))
    k = max(1, min(k, total // min_event))
    durations = min_event + rng.multinomial(total - k * min_event, [1 / k] * k)
    margin = dt["lost_volume_lookback_hours"]           # running hours before the first event, for the lost volume
    free = n_hours - total - margin - 2 * k
    offsets = np.sort(rng.choice(free + 1, size=k, replace=False))
    events, used = [], 0
    for j, (offset, hours) in enumerate(zip(offsets, durations)):
        start = margin + int(offset) + used + 2 * j
        events.append((start, int(hours)))
        used += int(hours)
    return events


def hours_since_shut_in(running):
    """For each hour, the number of hours the well has been shut in (0 while running)."""
    out = np.zeros(len(running))
    for i in range(len(running)):
        out[i] = 0 if running[i] else (out[i - 1] + 1 if i else 1)
    return out


def true_values(rng, well, running, true_volume, cfg):
    """True readings of every tag of the well, before deliberate bad readings."""
    g = cfg["generator"]
    n = len(running)
    noise = np.clip(1 + rng.normal(0, g["flow_hourly_noise_pct"] / 100, n), 0.5, None)
    hourly = np.where(running, noise, 0.0)
    hourly = hourly / hourly.sum() * true_volume
    shut = hours_since_shut_in(running)

    p = g["pressure_kpa"][well["product"]]
    build = 1 - np.exp(-shut / g["shut_in_build_hours"])
    pn = g["pressure_noise_pct"] / 100
    tubing = (p["tubing"] + (p["casing"] - p["tubing"]) * build) * (1 + rng.normal(0, pn, n))
    casing = p["casing"] * (1 + rng.normal(0, pn, n))

    t = g["temperature_degc"]
    t_run = t[well["product"]]
    temp = t["ambient"] + (t_run - t["ambient"]) * np.exp(-shut / t["cooling_hours"]) + rng.normal(0, t["noise"], n)

    values = {"TP": tubing, "CP": casing, "TEMP": temp, flow_code(well): hourly * 24, "RUN": running.astype(float)}
    return {code: np.round(v, g["decimals"][code]) for code, v in values.items()}, hourly


def inject(rng, values, running, well, cfg):
    """Replace some readings with frozen values and out-of-range values, and pick readings to drop.

    Returns the list of injections and the set of (tag code, hour index) to drop as missing."""
    inj = cfg["injections"]
    specs = tag_specs(cfg)
    analog = [c for c in well_tag_codes(cfg, well) if specs[c]["kind"] == "analog"]
    run_idx = np.flatnonzero(running)
    used, records, missing = set(), [], set()

    def free(code, idx):
        return all(running[i] and (code, i) not in used for i in idx)

    for _ in range(inj["frozen_runs_per_well"]):
        length = inj["frozen_run_hours"]
        while True:
            code = analog[rng.integers(len(analog))]
            start = int(run_idx[rng.integers(len(run_idx))])
            idx = range(start - 1, start + length)
            if not (start >= 2 and start + length < len(running) and free(code, idx)):
                continue
            # The readings just outside the run must differ from the frozen value, so the run is exactly as injected
            frozen_value = values[code][start - 1]
            if values[code][start - 2] != frozen_value and values[code][start + length] != frozen_value:
                break
        values[code][start:start + length] = values[code][start - 1]
        used.update((code, i) for i in idx)
        records.append({"kind": "frozen", "tag_code": code, "start": start, "hours": length})

    for kind, count in [("out_of_range", inj["out_of_range_per_well"]), ("missing", inj["missing_per_well"])]:
        for _ in range(count):
            while True:
                code = analog[rng.integers(len(analog))]
                i = int(run_idx[rng.integers(len(run_idx))])
                if free(code, [i]):
                    break
            used.add((code, i))
            if kind == "out_of_range":
                values[code][i] = inj["out_of_range_value"]
            else:
                missing.add((code, i))
            records.append({"kind": kind, "tag_code": code, "start": i, "hours": 1})
    return records, missing


def generate_backfill(cfg):
    """Readings (tag_name, ts_utc, value, source) and the ground truth of the simulation."""
    grid = hour_grid_utc(cfg)
    n = len(grid)
    bias_cfg = {a["well_key"]: a["volume_bias_pct"] for a in cfg["anomaly_wells"]["wells"]}
    max_bias = cfg["generator"]["normal_volume_bias_max_pct"]
    frames = []
    truth = {"wells": [], "downtime_events": [], "injections": []}

    for i, well in enumerate(cfg["wells"]):
        rng = np.random.default_rng([cfg["seed"], i])
        running = np.ones(n, dtype=bool)
        events = place_downtime(rng, n, well, cfg)
        for start, hours in events:
            running[start:start + hours] = False
        bias = bias_cfg.get(well["well_key"], float(rng.uniform(-max_bias, max_bias)))
        true_volume = well["petrinex_volume"] * (1 + bias / 100)
        values, hourly = true_values(rng, well, running, true_volume, cfg)
        records, missing = inject(rng, values, running, well, cfg)

        for code, series in values.items():
            keep = np.array([(code, h) not in missing for h in range(n)])
            frames.append(pd.DataFrame({"tag_name": tag_name(cfg, well, code), "ts_utc": grid[keep],
                                        "value": series[keep]}))
        truth["wells"].append({"well_key": well["well_key"], "product": well["product"],
                               "petrinex_volume": well["petrinex_volume"], "volume_bias_pct": round(bias, 6),
                               "true_volume": round(float(hourly.sum()), 6),
                               "running_hours": int(running.sum()), "downtime_hours": int((~running).sum())})
        for start, hours in events:
            truth["downtime_events"].append({"well_key": well["well_key"], "start_utc": grid[start].isoformat(),
                                             "end_utc": (grid[start] + pd.Timedelta(hours=hours)).isoformat(),
                                             "hours": hours})
        for r in records:
            truth["injections"].append({"well_key": well["well_key"], "kind": r["kind"],
                                        "tag_name": tag_name(cfg, well, r["tag_code"]),
                                        "start_utc": grid[r["start"]].isoformat(), "hours": r["hours"]})

    readings = pd.concat(frames, ignore_index=True)
    readings["source"] = "backfill"
    readings = readings.sort_values(["tag_name", "ts_utc"], ignore_index=True)
    return readings, truth
