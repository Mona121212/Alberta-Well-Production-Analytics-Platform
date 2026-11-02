"""Static checks of the Eventstream definition in fabric/ (DEC-033): it holds the expected topology and no secret.
Run: python -m pytest tests"""
import json
import os
import re
from pathlib import Path

import pytest

from sensor.config import load_config
from test_powerbi_files import FORBIDDEN

ROOT = Path(__file__).resolve().parents[1]
STREAM = load_config()["stream"]
ITEMS = json.loads((ROOT / "config" / "fabric_items.json").read_text(encoding="utf-8"))
ES_DIR = ROOT / "fabric" / f"{STREAM['eventstream']}.Eventstream"
FILES = sorted(p for p in ES_DIR.rglob("*") if p.is_file())
DEFINITION = json.loads((ES_DIR / "eventstream.json").read_text(encoding="utf-8"))

# Anything that would mean an Event Hubs connection string or key was exported
SECRETS = {
    **FORBIDDEN,
    "access key": r"(?i)shared_?access_?key|primary_?key|secondary_?key|accesskey",
    "connection string": r"(?i)connection_?string|endpoint\s*=|sb://|servicebus\.windows\.net|entitypath",
}


def test_definition_files_exported():
    assert {p.name for p in FILES} == {".platform", "eventstream.json", "eventstreamProperties.json"}


def test_platform_file_names_the_eventstream():
    meta = json.loads((ES_DIR / ".platform").read_text(encoding="utf-8"))["metadata"]
    assert meta["type"] == "Eventstream"
    assert meta["displayName"] == STREAM["eventstream"] == ITEMS["eventstream"]["name"]


def test_custom_endpoint_source_has_no_properties():
    sources = DEFINITION["sources"]
    assert [(s["name"], s["type"]) for s in sources] == [("sensor_simulator", "CustomEndpoint")]
    assert sources[0]["properties"] == {}


def test_destination_writes_json_to_the_bronze_table():
    destinations = DEFINITION["destinations"]
    assert [d["type"] for d in destinations] == ["Lakehouse"]
    props = destinations[0]["properties"]
    assert props["deltaTable"] == STREAM["bronze_table"]
    assert props["inputSerialization"]["type"] == "Json"
    assert props["workspaceId"] == ITEMS["workspace"]["id"]


@pytest.mark.parametrize("name, pattern", SECRETS.items())
def test_no_secrets_in_the_definition(name, pattern):
    hits = [f"{p.name}:{i}" for p in FILES
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert hits == [], f"{name} found in {hits}"


def test_patterns_catch_a_connection_string():
    fake = "Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=abc;EntityPath=y"
    assert re.search(SECRETS["connection string"], fake) and re.search(SECRETS["access key"], fake)


def test_definition_does_not_contain_the_local_connection_string():
    """Only runs where the variable is set; CI has no such secret."""
    value = os.environ.get(STREAM["connection_string_env"])
    if not value:
        pytest.skip("connection string not set")
    assert not any(value in p.read_text(encoding="utf-8") for p in FILES)


def test_simulator_reads_the_connection_string_only_from_the_environment():
    text = (ROOT / "scripts" / "sensor_simulator.py").read_text(encoding="utf-8")
    assert not re.search(r"sb://|SharedAccessKey\s*=", text)
    assert "os.environ.get(stream[\"connection_string_env\"])" in text
    assert not re.search(r"(?i)print\([^)]*conn_str", text)
