"""Monthly volume estimated from sensor readings, reconciled with the Petrinex volume (DEC-032).

Hourly volume of a well:
- a Good flow reading: rate / 24 (rates are per day);
- no Good flow reading, but a Good RUN = 0 reading: 0 (shut in);
- otherwise: the mean Good flow rate of the running hours of the same local day / 24 (the month's mean if that day
  has none).
"""
import numpy as np
import pandas as pd

from sensor.config import flow_code, local_day, tag_name


def estimate_well_volume(classified, well, cfg):
    """Estimated monthly volume of one well and the number of hours filled from the daily mean."""
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
    return float(hourly.sum()), filled


def reconcile(classified, cfg):
    """Per well: sensor volume, Petrinex volume, difference, difference % and the investigate flag."""
    threshold = cfg["reconciliation"]["threshold_pct"]
    rows = []
    for well in cfg["wells"]:
        sensor_volume, filled = estimate_well_volume(classified, well, cfg)
        reported = well["petrinex_volume"]
        diff = sensor_volume - reported
        diff_pct = diff / reported * 100
        rows.append({"well_key": well["well_key"], "facility_id": well["facility_id"], "product": well["product"],
                     "sensor_volume": sensor_volume, "reported_volume": reported, "difference": diff,
                     "difference_pct": diff_pct, "hours_filled": filled,
                     "needs_investigation": abs(diff_pct) > threshold})
    return pd.DataFrame(rows)
