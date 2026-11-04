"""Static checks of the sensor Data Pipeline definition in fabric/ (DEC-034). Run: python -m pytest tests"""
import json
import re
from pathlib import Path

import pytest

from test_powerbi_files import FORBIDDEN

ROOT = Path(__file__).resolve().parents[1]
ITEMS = json.loads((ROOT / "config" / "fabric_items.json").read_text(encoding="utf-8"))
PIPELINE = ITEMS["sensor_pipeline"]
PIPELINE_DIR = ROOT / "fabric" / f"{PIPELINE['name']}.DataPipeline"
ACTIVITIES = json.loads((PIPELINE_DIR / "pipeline-content.json").read_text(encoding="utf-8"))["properties"]["activities"]


def test_platform_file_names_the_pipeline():
    meta = json.loads((PIPELINE_DIR / ".platform").read_text(encoding="utf-8"))["metadata"]
    assert meta["type"] == "DataPipeline"
    assert meta["displayName"] == PIPELINE["name"]


def test_activities_match_config_in_order():
    assert [a["name"] for a in ACTIVITIES] == [s["activity"] for s in PIPELINE["steps"]]


def test_spark_jobs_run_one_after_the_other():
    assert ACTIVITIES[0].get("dependsOn", []) == []
    for prev, act in zip(ACTIVITIES, ACTIVITIES[1:]):
        assert act["dependsOn"] == [{"activity": prev["name"], "dependencyConditions": ["Succeeded"]}], act["name"]


@pytest.mark.parametrize("step", [s for s in PIPELINE["steps"] if "notebook" in s], ids=lambda s: s["activity"])
def test_notebook_activity_points_to_the_right_notebook(step):
    act = next(a for a in ACTIVITIES if a["name"] == step["activity"])
    assert act["type"] == "TridentNotebook"
    assert act["typeProperties"]["notebookId"] == ITEMS["notebooks"][step["notebook"]]
    assert act["typeProperties"]["workspaceId"] == ITEMS["workspace"]["id"]
    assert act["policy"]["timeout"] == step["timeout"]
    assert act["policy"]["retry"] == PIPELINE["retry"]
    assert act["policy"]["retryIntervalInSeconds"] == PIPELINE["retry_interval_seconds"]


def test_refresh_runs_last_on_the_configured_model_and_connection():
    step = PIPELINE["steps"][-1]
    act = ACTIVITIES[-1]
    assert act["type"] == "PBISemanticModelRefresh"
    assert act["dependsOn"] == [{"activity": "SensorGold", "dependencyConditions": ["Succeeded"]}]
    assert act["typeProperties"]["datasetId"] == ITEMS["semantic_model"]["id"]
    assert act["typeProperties"]["groupId"] == ITEMS["workspace"]["id"]
    assert act["externalReferences"]["connection"] == ITEMS["connections"][step["connection"]]
    assert act["policy"]["timeout"] == step["timeout"]
    assert act["policy"]["retry"] == PIPELINE["retry"]
    assert act["policy"]["retryIntervalInSeconds"] == PIPELINE["retry_interval_seconds"]


@pytest.mark.parametrize("name, pattern", FORBIDDEN.items())
def test_no_credentials(name, pattern):
    hits = [f"{p.name}:{i}" for p in PIPELINE_DIR.rglob("*") if p.is_file()
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert hits == [], f"{name} found in {hits}"
