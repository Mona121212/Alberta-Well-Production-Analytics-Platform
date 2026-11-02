"""Quality flags of readings, PI style: Good, Questionable or Bad (DEC-032). Bad readings are flagged, not deleted.

- Bad: no reading for an expected timestamp (PI "No Data"), or a value outside the tag's physical range.
- Questionable: a value outside the normal range, or a frozen value: the same value for `frozen_min_points` or more
  consecutive readings on a tag with `frozen_check` (values in `frozen_ignore_values`, such as 0 flow while shut in,
  are not counted as frozen).
- Good: everything else.
"""
import numpy as np
import pandas as pd


def expected_grid(hierarchy, grid):
    """Every tag x every timestamp of the grid."""
    tags = hierarchy.loc[hierarchy["node_type"] == "tag", "tag_name"]
    return pd.MultiIndex.from_product([tags, grid], names=["tag_name", "ts_utc"]).to_frame(index=False)


def frozen_mask(df, min_points, ignore_values):
    """True for readings in a run of at least min_points equal consecutive values (per tag, in time order)."""
    v = df["value"]
    new_run = (v != v.shift()) | (df["tag_name"] != df["tag_name"].shift()) | v.isna()
    run_id = new_run.cumsum()
    run_len = df.groupby(run_id)["value"].transform("size")
    return (run_len >= min_points) & v.notna() & ~v.isin(ignore_values)


def classify(readings, hierarchy, grid, cfg):
    """Readings on the full expected grid with columns quality and quality_reason."""
    q = cfg["quality"]
    tags = hierarchy[hierarchy["node_type"] == "tag"].set_index("tag_name")
    df = expected_grid(hierarchy, grid).merge(readings[["tag_name", "ts_utc", "value"]], how="left",
                                              on=["tag_name", "ts_utc"])
    df = df.sort_values(["tag_name", "ts_utc"], ignore_index=True)
    spec = tags.loc[df["tag_name"]]
    value = df["value"].to_numpy()
    missing = np.isnan(value)
    outside_physical = ~missing & ((value < spec["physical_min"].to_numpy()) | (value > spec["physical_max"].to_numpy()))
    outside_normal = ~missing & ((value < spec["normal_min"].to_numpy()) | (value > spec["normal_max"].to_numpy()))
    checked = spec["tag_code"].map({t["code"]: t["frozen_check"] for t in cfg["tags"]}).to_numpy(dtype=bool)
    frozen = frozen_mask(df, q["frozen_min_points"], q["frozen_ignore_values"]).to_numpy() & checked

    df["quality"] = np.select([missing | outside_physical, outside_normal | frozen], ["Bad", "Questionable"], "Good")
    df["quality_reason"] = np.select(
        [missing, outside_physical, outside_normal, frozen],
        ["missing", "outside_physical_range", "outside_normal_range", "frozen"], "")
    return df
