"""config/silver.json and the month hour limit used by hours_above_month_max (D-010, DEC-030). Run: python -m pytest tests"""
import ast
import calendar
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULE = json.loads((ROOT / "config" / "silver.json").read_text(encoding="utf-8"))["dq_rules"]["hours_above_month_max"]
NOTEBOOK = json.loads((ROOT / "notebooks" / "02_silver_transform.ipynb").read_text(encoding="utf-8"))
MONTHS = [date(2025, m, 1) for m in range(7, 13)] + [date(2026, m, 1) for m in range(1, 7)]


def load_month_hour_limit():
    """The month_hour_limit function exactly as it is in 02_silver_transform, with the imports it needs."""
    for cell in NOTEBOOK["cells"]:
        source = "".join(cell["source"])
        if "def month_hour_limit(" in source:
            tree = ast.parse(source)
            func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "month_hour_limit")
            namespace = {}
            exec("import calendar\nfrom datetime import datetime\nfrom zoneinfo import ZoneInfo\n"
                 + ast.get_source_segment(source, func), namespace)
            return namespace["month_hour_limit"]
    raise AssertionError("month_hour_limit not found in 02_silver_transform")


month_hour_limit = load_month_hour_limit()


def test_config_method_is_accepted():
    assert RULE["method"] in RULE["accepted_methods"]
    assert set(RULE["accepted_methods"]) == {"days_x_24", "local_clock_hours", "max_of_calendar_and_clock"}
    assert RULE["method"] == "max_of_calendar_and_clock"
    assert RULE["timezone"] == "America/Edmonton"


@pytest.mark.parametrize("month, clock", [(date(2025, 11, 1), 721), (date(2026, 3, 1), 743)])
def test_clock_hours_in_dst_months(month, clock):
    assert month_hour_limit(month, "local_clock_hours", RULE["timezone"]) == clock


@pytest.mark.parametrize("month, limit", [(date(2025, 11, 1), 721), (date(2026, 3, 1), 744)])
def test_configured_limit_accepts_both_habits(month, limit):
    assert month_hour_limit(month, RULE["method"], RULE["timezone"]) == limit


@pytest.mark.parametrize("month", MONTHS, ids=str)
def test_other_months_keep_days_x_24(month):
    days_x_24 = calendar.monthrange(month.year, month.month)[1] * 24
    for method in RULE["accepted_methods"]:
        expected = days_x_24 if month not in (date(2025, 11, 1), date(2026, 3, 1)) else None
        if expected is not None:
            assert month_hour_limit(month, method, RULE["timezone"]) == expected, method
    assert month_hour_limit(month, "max_of_calendar_and_clock", RULE["timezone"]) >= days_x_24


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        month_hour_limit(date(2025, 11, 1), "days_x_25", RULE["timezone"])
