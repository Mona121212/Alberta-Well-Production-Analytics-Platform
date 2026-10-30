"""Defect log tests: config/defect_log.yaml is the single source (DEC-024). Run: python -m pytest tests"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_defect_log", ROOT / "scripts" / "build_defect_log.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

CFG = B.load()
DOC_TEXT = B.DOC.read_text(encoding="utf-8")


def test_markdown_matches_yaml():
    assert B.check(CFG, DOC_TEXT) == []


def test_ids_unique_and_required_fields_present():
    ids = [d["id"] for d in CFG["defects"]]
    assert len(ids) == len(set(ids))
    for d in CFG["defects"]:
        for field in ["id", "issue", "impact", "handling", "status", "area", "severity"]:
            assert d.get(field) not in (None, ""), f"{d.get('id')} has no {field}"


def test_accepted_values_and_status_map():
    accepted = CFG["accepted_values"]
    for d in CFG["defects"]:
        for field in ["status", "area", "severity"]:
            assert d[field] in accepted[field], f"{d['id']} {field} = {d[field]}"
    assert set(accepted["status"]) <= set(CFG["status_map"])


def test_stale_table_is_detected():
    stale = B.replace_table(DOC_TEXT, "| ID |\n| --- |\n| D-999 |")
    assert any("out of date" in p for p in B.check(CFG, stale))


@pytest.mark.parametrize("text, found", [
    ("**D-001 x.** 1,310 negative values.", True),
    ("**D-001 x.** 11,310 negative values.", False),   # digits before the value
    ("**D-001 x.** 1,310.5 negative values.", False),  # decimal after the value
])
def test_metric_must_appear_as_whole_number(text, found):
    cfg = {"defects": [{"id": "D-001", "metrics": [{"name": "n", "value": 1310}]}]}
    assert (B.missing_metrics(cfg, text) == []) is found
