"""Monthly volume estimated from sensor readings, reconciled with the Petrinex volume (DEC-032).

Hourly volume of a well:
- a Good flow reading: rate / 24 (rates are per day);
- no Good flow reading, but a Good RUN = 0 reading: 0 (shut in);
- otherwise: the mean Good flow rate of the running hours of the same local day / 24 (the month's mean if that day
  has none).

A flagged well's likely cause depends on the Good ratio of its flow tag: below the threshold the sensor is checked first,
otherwise it is a production mismatch (DEC-035). Wells that are not flagged have no cause.
"""
import numpy as np
import pandas as pd

from sensor.config import flow_code, local_day, tag_name


def estimate_well_volume(classified, well, cfg):
    """Estimated monthly volume of one well, the hours filled from the daily mean and the flow tag's Good ratio %."""
    by_tag = classified.set_index("tag_name")
    flow = by_tag.loc[tag_name(cfg, well, flow_code(well))].set_index("ts_utc")
    run = by_tag.loc[tag_name(cfg, well, "RUN")].set_index("ts_utc").reindex(flow.index)

    good_flow = flow["quality"].eq("Good")
    shut_in = run["quality"].eq("Good") & run["value"].eq(0)
    running_good = good_flow & ~shut_in & flow["value"].gt(cfg["downtime"]["flow_off_below"][well["product"]])

    day = pd.Series(local_day(flow.index, cfg), index=flow.index)
    rate = flow["value"].where(running_good)
    day_mean = rate.groupby(day).transform("mean").fillna(rate.mean())

    hourly = np.where(good_flow, flow["value"], np.where(shut_in, 0.0, day_mean)) / 24
    filled = int((~good_flow & ~shut_in).sum())
    return float(hourly.sum()), filled, float(good_flow.mean() * 100)


def reconcile(classified, cfg):
    """Per well: sensor volume, Petrinex volume, difference, difference %, the investigate flag and its likely cause."""
    threshold = cfg["reconciliation"]["threshold_pct"]
    good_threshold = cfg["reconciliation"]["sensor_good_ratio_threshold_pct"]
    causes = cfg["reconciliation"]["likely_cause"]
    rows = []
    for well in cfg["wells"]:
        sensor_volume, filled, flow_good_ratio = estimate_well_volume(classified, well, cfg)
        reported = well["petrinex_volume"]
        diff = sensor_volume - reported
        diff_pct = diff / reported * 100
        flagged = abs(diff_pct) > threshold
        cause = None
        if flagged:
            cause = causes["sensor"] if flow_good_ratio < good_threshold else causes["production"]
        rows.append({"well_key": well["well_key"], "facility_id": well["facility_id"], "product": well["product"],
                     "sensor_volume": sensor_volume, "reported_volume": reported, "difference": diff,
                     "difference_pct": diff_pct, "hours_filled": filled, "flow_good_ratio_pct": flow_good_ratio,
                     "needs_investigation": flagged, "likely_cause": cause})
    return pd.DataFrame(rows)
