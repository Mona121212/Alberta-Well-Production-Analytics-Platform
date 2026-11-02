"""Sensor stream, stage 2 (DEC-033): the planned run, deliberate late and duplicate readings, the sender and the
secret handling. Nothing is sent. Run: python -m pytest tests"""
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sensor.config import load_config, tag_name, well_tag_codes
from sensor.stream import EventHubSender, build_schedule, redact, run_stream

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sensor_simulator.py"
CFG = load_config()
STREAM = CFG["stream"]
START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
TAGS = {tag_name(CFG, w, c) for w in CFG["wells"] for c in well_tag_codes(CFG, w)}
FAKE_CONN = ("Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=key_fake;"
             "SharedAccessKey=FAKEfake123/+=;EntityPath=es_fake")


@pytest.fixture(scope="module")
def planned():
    return build_schedule(CFG, START, 15, "run-1")


def flat(schedule):
    return [(tick, e) for tick, batch in enumerate(schedule) for e in batch]


def run_cli(*args, env_value=None):
    env = {k: v for k, v in os.environ.items() if k != STREAM["connection_string_env"]}
    if env_value:
        env[STREAM["connection_string_env"]] = env_value
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT)


# The planned run ------------------------------------------------------------------------------------------------

def test_counts_for_15_minutes(planned):
    schedule, expected = planned
    assert len(schedule) == 15
    assert expected["unique_readings"] == 15 * 40 * 5 == 3000
    assert expected["late"] == expected["duplicates"] == 30
    assert expected["events"] == 3030 == len(flat(schedule))


def test_value_sum_covers_every_event_sent_including_duplicates(planned):
    schedule, expected = planned
    assert expected["value_sum"] == pytest.approx(sum(e["value"] for _, e in flat(schedule)), abs=0.001)


def test_same_seed_gives_same_plan_and_another_seed_does_not(planned):
    assert build_schedule(CFG, START, 15, "run-1")[0] == planned[0]
    assert build_schedule(CFG, START, 15, "run-1", seed=1)[0] != planned[0]


def test_events_have_keys_and_known_tags(planned):
    for _, e in flat(planned[0]):
        assert set(e) == {"tag_name", "ts_utc", "value", "source", "run_id"}
        assert e["tag_name"] in TAGS and e["ts_utc"].endswith("Z")
        assert isinstance(e["value"], float)
        assert e["source"] == "stream" and e["run_id"] == "run-1"


def test_every_tag_has_a_reading_each_minute(planned):
    keys = {(e["tag_name"], e["ts_utc"]) for _, e in flat(planned[0])}
    assert len(keys) == len(TAGS) * 15


def test_duplicates_are_exact_copies(planned):
    schedule, expected = planned
    counts = Counter(json.dumps(e, sort_keys=True) for _, e in flat(schedule))
    assert Counter(counts.values()) == {1: expected["unique_readings"] - expected["duplicates"],
                                        2: expected["duplicates"]}
    assert len({(e["tag_name"], e["ts_utc"]) for _, e in flat(schedule)}) == expected["unique_readings"]


def test_late_readings_arrive_at_least_the_threshold_late(planned):
    schedule, expected = planned
    interval = STREAM["sample_interval_seconds"]
    late = []
    for tick, e in flat(schedule):
        lag = tick * interval - (datetime.fromisoformat(e["ts_utc"]) - START).total_seconds()
        assert lag >= 0
        if lag >= expected["late_threshold_seconds"]:
            late.append(e)
    assert len(late) == expected["late"]


def test_short_run_has_no_late_readings_but_keeps_counts():
    schedule, expected = build_schedule(CFG, START, 1, "run-1")
    assert len(schedule) == 1 and expected["late"] == 0
    assert expected["events"] == expected["unique_readings"] + expected["duplicates"] == len(schedule[0])


def test_values_stay_inside_the_normal_range(planned):
    ranges = {t["code"]: t["normal_range"] for t in CFG["tags"]}
    for _, e in flat(planned[0]):
        low, high = ranges[e["tag_name"].rsplit(".", 1)[1]]
        assert low <= e["value"] <= high, e


# Sending --------------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = START
        self.slept = 0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += seconds
        self.t += timedelta(seconds=seconds)


def test_run_stream_paces_ticks_and_stamps_send_time():
    clock = Clock()
    batches = []
    schedule = [[{"a": 1}], [{"a": 2}, {"a": 3}], [{"a": 4}]]
    assert run_stream(schedule, START, 60, batches.append, clock.now, clock.sleep) == 4
    assert clock.slept == 120
    assert [e["sent_utc"] for e in batches[2]] == ["2026-11-02T12:02:00Z"]


class FakeBatch:
    def __init__(self, size):
        self.size, self.items = size, []

    def add(self, item):
        if len(self.items) == self.size:
            raise ValueError("full")
        self.items.append(item)


class FakeProducer:
    def __init__(self, size):
        self.size, self.sent = size, []

    def create_batch(self):
        return FakeBatch(self.size)

    def send_batch(self, batch):
        self.sent.append(batch.items)


def test_sender_splits_full_batches_and_loses_nothing():
    producer = FakeProducer(2)
    EventHubSender(producer, str)([{"n": i} for i in range(5)])
    assert [len(b) for b in producer.sent] == [2, 2, 1]
    assert [json.loads(x)["n"] for b in producer.sent for x in b] == list(range(5))


def test_sender_sends_nothing_for_no_events():
    producer = FakeProducer(2)
    EventHubSender(producer, str)([])
    assert producer.sent == []


# Command line and secrets ---------------------------------------------------------------------------------------

def test_dry_run_needs_no_connection_string_and_sends_nothing():
    result = run_cli("--dry-run", "--duration-minutes", "15")
    assert result.returncode == 0, result.stderr
    summary = json.JSONDecoder().raw_decode(result.stdout)[0]
    assert summary["status"] == "DRY_RUN" and summary["sent_events"] == 0
    assert summary["expected"]["events"] == 3030


def test_dry_run_does_not_echo_the_connection_string():
    result = run_cli("--dry-run", "--duration-minutes", "1", env_value=FAKE_CONN)
    assert result.returncode == 0, result.stderr
    assert "FAKEfake123" not in result.stdout + result.stderr


def test_real_run_without_the_variable_stops_cleanly():
    result = run_cli("--duration-minutes", "1")
    assert result.returncode != 0
    assert STREAM["connection_string_env"] in result.stderr
    assert "Traceback" not in result.stderr


def test_redact_removes_connection_details():
    text = redact(f"could not connect with {FAKE_CONN} and SharedAccessKey=other123", FAKE_CONN)
    assert "FAKEfake123" not in text and "other123" not in text and "fake.servicebus" not in text


def test_redact_handles_a_string_with_only_a_key():
    assert "abc" not in redact("Endpoint=sb://abc;SharedAccessKey=abc")
