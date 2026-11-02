"""Settings from config/sensor.json, the UTC hour grid of the simulated month, and tag naming (DEC-032)."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "sensor.json"


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def month_bounds_utc(cfg):
    """UTC instants of local midnight on the 1st of the month and of the next month."""
    year, month = (int(x) for x in cfg["month"]["production_month"].split("-"))
    tz = ZoneInfo(cfg["month"]["timezone"])
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=tz)
    return pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")


def hour_grid_utc(cfg):
    """One UTC timestamp per hour of the local month: 721 for 2025-11, when daylight saving time ends."""
    start, end = month_bounds_utc(cfg)
    return pd.date_range(start, end, freq="h", inclusive="left")


def local_day(ts_utc, cfg):
    """Local calendar day of UTC timestamps."""
    return ts_utc.tz_convert(cfg["month"]["timezone"]).date


def tag_specs(cfg):
    return {t["code"]: t for t in cfg["tags"]}


def well_tag_codes(cfg, well):
    """Tag codes of a well: every tag without a product, plus the flow tag of the well's product."""
    return [t["code"] for t in cfg["tags"] if t.get("product") in (None, well["product"])]


def tag_name(cfg, well, code):
    return cfg["af"]["tag_name_pattern"].format(facility_id=well["facility_id"], well_key=well["well_key"],
                                                tag_code=code)


def flow_code(well):
    return f"FLOW_{well['product']}"
