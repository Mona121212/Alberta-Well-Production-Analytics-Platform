"""Static checks of the Data Pipeline definition in fabric/ (DEC-029). Run: python -m pytest tests"""
import json
import re
from pathlib import Path

import pytest

from test_powerbi_files import FORBIDDEN

ROOT = Path(__file__).resolve().parents[1]
ITEMS = json.loads((ROOT / "config" / "fabric_items.json").read_text(encoding="utf-8"))
PIPELINE_DIR = ROOT / "fabric" / f"{ITEMS['pipeline']['name']}.DataPipeline"
CONTENT = PIPELINE_DIR / "pipeline-content.json"
ACTIVITIES = json.loads(CONTENT.read_text(encoding="utf-8"))["properties"]["activities"]
STEPS = ITEMS["pipeline"]["steps"]


def by_name(name):
    return next(a for a in ACTIVITIES if a["name"] == name)


def test_platform_file_names_the_pipeline():
    meta = json.loads((PIPELINE_DIR / ".platform").read_text(encoding="utf-8"))["metadata"]
    assert meta["type"] == "DataPipeline"
    assert meta["displayName"] == ITEMS["pipeline"]["name"]


def test_activities_match_config_in_order():
    assert [a["name"] for a in ACTIVITIES] == [s["activity"] for s in STEPS]


def test_each_step_depends_only_on_previous_success():
    assert ACTIVITIES[0].get("dependsOn", []) == []
    for prev, act in zip(ACTIVITIES, ACTIVITIES[1:]):
        assert act["dependsOn"] == [{"activity": prev["name"], "dependencyConditions": ["Succeeded"]}], act["name"]


def test_no_failed_or_completed_branches():
    conditions = {c for a in ACTIVITIES for d in a.get("dependsOn", []) for c in d["dependencyConditions"]}
    assert conditions <= {"Succeeded"}


@pytest.mark.parametrize("step", STEPS, ids=[s["activity"] for s in STEPS])
def test_timeout_and_no_retry(step):
    policy = by_name(step["activity"])["policy"]
    assert policy["timeout"] == step["timeout"]
    assert policy["retry"] == 0


@pytest.mark.parametrize("step", [s for s in STEPS if "notebook" in s], ids=lambda s: s["activity"])
def test_notebook_activity_points_to_the_right_notebook(step):
    act = by_name(step["activity"])
    assert act["type"] == "TridentNotebook"
    assert act["typeProperties"]["notebookId"] == ITEMS["notebooks"][step["notebook"]]
    assert act["typeProperties"]["workspaceId"] == ITEMS["workspace"]["id"]


def test_refresh_activity_refreshes_the_semantic_model_and_waits():
    step = next(s for s in STEPS if "semantic_model" in s)
    act = by_name(step["activity"])
    props = act["typeProperties"]
    assert act["type"] == "PBISemanticModelRefresh"
    assert props["datasetId"] == ITEMS["semantic_model"]["id"]
    assert props["groupId"] == ITEMS["workspace"]["id"]
    assert props["waitOnCompletion"] is True
    assert act["externalReferences"]["connection"] == ITEMS["connections"][step["connection"]]


def test_load_defect_log_is_not_in_the_pipeline():
    text = CONTENT.read_text(encoding="utf-8")
    assert ITEMS["notebooks"]["load_defect_log"] not in text
    assert "load_defect_log" not in text


@pytest.mark.parametrize("name, pattern", FORBIDDEN.items())
def test_no_credentials(name, pattern):
    hits = [f"{p.name}:{i}" for p in PIPELINE_DIR.rglob("*") if p.is_file()
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert hits == [], f"{name} found in {hits}"


def test_no_inline_connection_secrets():
    # The refresh activity references a workspace connection by ID; credentials stay in that connection
    text = CONTENT.read_text(encoding="utf-8")
    assert not re.search(r'(?i)"(connectionString|credential|accessToken|servicePrincipalKey)"', text)
