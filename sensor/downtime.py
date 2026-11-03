"""Downtime events detected from readings, PI event frame style (DEC-034). 05_sensor_gold applies the same rules in Spark.

An event is a run of at least `detect_min_hours` consecutive hours with a Good RUN = 0 reading. Its lost volume is an
estimate: the mean Good flow rate of the running hours in the `lost_volume_lookback_hours` before the event (the month's
mean if there are none, as for an event at the start of the month) x the event's hours / 24 (rates are per day).
"""
import pandas as pd

from sensor.config import flow_code, tag_name


def detect_downtime(classified, cfg):
    """One row per downtime event: well_key, start_utc, end_utc, duration_hours, lost_volume."""
    dt = cfg["downtime"]
    by_tag = classified.set_index("tag_name")
    rows = []
    for well in cfg["wells"]:
        flow = by_tag.loc[tag_name(cfg, well, flow_code(well))].set_index("ts_utc").sort_index()
        run = by_tag.loc[tag_name(cfg, well, "RUN")].set_index("ts_utc").reindex(flow.index)
        down = run["quality"].eq("Good") & run["value"].eq(0)
        running_good = (flow["quality"].eq("Good") & ~down
                        & flow["value"].gt(dt["flow_off_below"][well["product"]]))
        rate = flow["value"].where(running_good)

        group = (down != down.shift()).cumsum()
        for _, hours in down[down].groupby(group[down]):
            if len(hours) < dt["detect_min_hours"]:
                continue
            start = hours.index[0]
            before = rate[(rate.index >= start - pd.Timedelta(hours=dt["lost_volume_lookback_hours"]))
                          & (rate.index < start)]
            mean_rate = before.mean() if before.notna().any() else rate.mean()
            rows.append({"well_key": well["well_key"], "start_utc": start,
                         "end_utc": hours.index[-1] + pd.Timedelta(hours=1), "duration_hours": len(hours),
                         "lost_volume": float(mean_rate * len(hours) / 24)})
    return pd.DataFrame(rows, columns=["well_key", "start_utc", "end_utc", "duration_hours", "lost_volume"])
