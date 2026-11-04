"""Power BI files in powerbi/ (DEC-025): no credentials, and every measure documented. Run: python -m pytest tests"""
import json
import re
from pathlib import Path

import pytest

POWERBI = Path(__file__).resolve().parents[1] / "powerbi"
FILES = sorted(p for p in POWERBI.rglob("*") if p.is_file() and p.suffix in {".tmdl", ".json", ".pbir", ".pbism", ""})
TABLES = sorted((POWERBI / "sm_petrinex_production.SemanticModel" / "definition" / "tables").glob("*.tmdl"))

# Patterns that would mean a secret or a personal identifier was exported
FORBIDDEN = {
    "password": r"(?i)\b(password|pwd)\s*[=:]",
    "secret": r"(?i)client_?secret|\bsecret\s*[=:]",
    "storage key": r"(?i)accountkey\s*=|sharedaccesssignature|\bsig=",
    "token": r"(?i)\bbearer\s+[a-z0-9._-]{20,}",
    "tenant": r"(?i)tenant(id)?\W{1,6}[0-9a-f]{8}-",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
}


def test_files_exported():
    assert TABLES, "semantic model tables missing"
    assert (POWERBI / "well_production_dashboard.Report" / "definition.pbir").is_file()


@pytest.mark.parametrize("name, pattern", FORBIDDEN.items())
def test_no_credentials(name, pattern):
    hits = [f"{p.relative_to(POWERBI)}:{i}" for p in FILES
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert hits == [], f"{name} found in {hits}"


def test_connection_string_only_in_pbir_and_without_credentials():
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        if "connectionString" in text:
            assert p.name == "definition.pbir", p
            conn = re.search(r'"connectionString":\s*"([^"]*)"', text).group(1)
            assert "integrated security=ClaimsToken" in conn
            assert not re.search(r"(?i)user id|password", conn)


@pytest.mark.parametrize("name, sample", [
    ("password", "Password=hunter2"),
    ("secret", '"client_secret": "abc"'),
    ("storage key", "AccountKey=abc123"),
    ("token", "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOi"),
    ("tenant", '"tenantId": "72f988bf-86f1-41af-91ab-2d7cd011db47"'),
    ("email", "owner: someone@example.com"),
])
def test_patterns_catch_known_samples(name, sample):
    assert re.search(FORBIDDEN[name], sample)


def measures():
    """(table, measure, description, display folder) for every measure in the TMDL files."""
    found = []
    for path in TABLES:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^\tmeasure\s+'?([^'=]+?)'?\s*=", line)
            if not m:
                continue
            desc = lines[i - 1].strip()[3:].strip() if lines[i - 1].strip().startswith("///") else ""
            folder = ""
            for nxt in lines[i + 1:]:
                if re.match(r"^\t\S", nxt):
                    break
                if nxt.startswith("\t\tdisplayFolder:"):
                    folder = nxt.split(":", 1)[1].strip()
            found.append((path.stem, m.group(1).strip(), desc, folder))
    return found


def test_every_measure_has_description_and_display_folder():
    found = measures()
    assert len(found) == 89
    missing = [f"{t}[{name}]" for t, name, desc, folder in found if not desc or not folder]
    assert missing == [], f"measures without description or display folder: {missing}"


# Well Operations and Sensor Health pages (DEC-035) ------------------------------------------------------------------

DEFINITION = POWERBI / "sm_petrinex_production.SemanticModel" / "definition"
PAGES = POWERBI / "well_production_dashboard.Report" / "definition" / "pages"
SENSOR_TABLES = ["gold_sensor_hourly", "gold_downtime_events", "gold_sensor_reconciliation", "gold_sensor_tags"]


def page_visuals(page):
    return {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in (PAGES / page / "visuals").glob("*/visual.json")}


def test_sensor_tables_relate_to_the_well_dimension_only():
    relationships = (DEFINITION / "relationships.tmdl").read_text(encoding="utf-8")
    model = (DEFINITION / "model.tmdl").read_text(encoding="utf-8")
    for table in SENSOR_TABLES:
        assert f"ref table {table}" in model
        assert f"fromColumn: {table}.well_key" in relationships
    assert len(re.findall(r"toColumn: gold_(sensor|downtime)", relationships)) == 0


def test_tables_that_keep_every_run_or_have_empty_keys_are_not_in_the_model():
    model = (DEFINITION / "model.tmdl").read_text(encoding="utf-8")
    for table in ("sensor_reconciliation_results", "sensor_quality_test_results", "silver_sensor_readings", "sensor_asset_hierarchy"):
        assert f"ref table {table}" not in model


def test_new_measures_are_in_the_expected_folders():
    found = {name: folder for table, name, _, folder in measures() if table in SENSOR_TABLES}
    required = ["Downtime Events", "Downtime Hours", "Lost Oil (m3)", "Lost Gas (e3m3)", "Sensor Oil (m3)", "Sensor Gas (e3m3)",
                "Reported vs Sensor Diff %", "Wells to Investigate", "Good Ratio %", "Frozen Readings",
                "Out-of-Range Readings", "Missing Readings"]
    for name in required:
        assert found[name].startswith("Well Operations\\"), name


def test_pages_are_in_order_and_16_to_9():
    assert json.loads((PAGES / "pages.json").read_text(encoding="utf-8"))["pageOrder"][-2:] == ["WellOperations", "SensorHealth"]
    for page in ("WellOperations", "SensorHealth"):
        meta = json.loads((PAGES / page / "page.json").read_text(encoding="utf-8"))
        assert (meta["width"], meta["height"]) == (1280, 720)


@pytest.mark.parametrize("page", ["WellOperations", "SensorHealth"])
def test_visuals_stay_on_the_page_and_do_not_overlap(page):
    boxes = [v["position"] | {"name": n} for n, v in page_visuals(page).items()]
    for b in boxes:
        assert b["x"] >= 0 and b["x"] + b["width"] <= 1280 and b["y"] + b["height"] <= 720, b["name"]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            apart = (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                     or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])
            assert apart, (a["name"], b["name"])


@pytest.mark.parametrize("page", ["WellOperations", "SensorHealth"])
def test_table_columns_fit_and_tables_have_no_totals(page):
    for name, v in page_visuals(page).items():
        if v["visual"]["visualType"] != "tableEx":
            continue
        widths = [float(w["properties"]["value"]["expr"]["Literal"]["Value"].rstrip("D")) for w in v["visual"]["objects"]["columnWidth"]]
        assert sum(widths) < v["position"]["width"], name
        assert len(widths) == len(v["visual"]["query"]["queryState"]["Values"]["projections"]), name
        assert v["visual"]["objects"]["total"][0]["properties"]["totals"]["expr"]["Literal"]["Value"] == "false", name


def test_well_columns_use_the_dimension_so_a_click_reaches_the_other_tables():
    for page in ("WellOperations", "SensorHealth"):
        for name, v in page_visuals(page).items():
            state = v["visual"].get("query", {}).get("queryState", {})
            refs = [p["queryRef"] for bucket in state.values() for p in bucket["projections"]]
            assert not any(r.endswith(".well_key") and not r.startswith("gold_dim_well.") for r in refs), name


def test_tables_filter_the_trends_and_the_trends_filter_nothing():
    links = json.loads((PAGES / "WellOperations" / "page.json").read_text(encoding="utf-8"))["visualInteractions"]
    kind = {(i["source"], i["target"]): i["type"] for i in links}
    for source in ("p4_events", "p4_recon", "p4_top10"):
        for target in ("p4_flow", "p4_pressure", "p4_card_0", "p4_card_5"):
            assert kind[(source, target)] == "DataFilter"
    assert kind[("p4_events", "p4_top10")] == kind[("p4_recon", "p4_top10")] == "HighlightFilter"
    assert all(i["type"] == "NoFilter" for i in links if i["source"] in ("p4_flow", "p4_pressure"))


def test_reconciliation_table_shows_the_flow_good_ratio_beside_the_likely_cause():
    state = page_visuals("WellOperations")["p4_recon"]["visual"]["query"]["queryState"]
    refs = [p["queryRef"] for p in state["Values"]["projections"]]
    assert refs.index("gold_sensor_reconciliation.likely_cause") - refs.index("gold_sensor_reconciliation.Flow Good Ratio %") == 1


def test_pages_state_that_the_data_is_simulated():
    for page, note in (("WellOperations", "p4_note"), ("SensorHealth", "p5_note")):
        text = json.dumps(page_visuals(page)[note])
        assert "simulated" in text and "on purpose" in text


def test_tag_health_table_names_tags_from_the_tag_table_and_keeps_the_code_as_its_grouping():
    state = page_visuals("SensorHealth")["p5_tags"]["visual"]["query"]["queryState"]
    refs = [p["queryRef"] for p in state["Values"]["projections"]]
    assert refs[:4] == ["gold_dim_well.well_key", "gold_sensor_hourly.tag_code", "gold_sensor_tags.Tag Name", "gold_sensor_tags.Tag Unit"]
    assert "gold_sensor_hourly.unit" not in refs
