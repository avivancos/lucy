import gzip
import json

import pytest

from lucy_cloud._wire import (
    FLUSH_INTERVAL_S,
    HEADER_WIRE,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    build_envelope,
    encode_batch,
    json_bytes,
    plan_batches,
)
from .helpers import wire_event


def test_normative_batch_limits_are_exact():
    assert MAX_BATCH_EVENTS == 100
    assert MAX_BATCH_BYTES == 1_048_576
    assert FLUSH_INTERVAL_S == 2.0


def test_envelope_matches_wire_v1_shape():
    events = [wire_event()]
    assert build_envelope("project-a", "1.2.3", events) == {
        "project": "project-a",
        "sdk": {"name": "lucy", "version": "1.2.3"},
        "events": events,
    }


def test_plan_batches_splits_at_max_events():
    batches = plan_batches(
        [wire_event(index) for index in range(MAX_BATCH_EVENTS + 1)],
        project="project-a",
        sdk_version="1.2.3",
    )
    assert [len(batch) for batch in batches] == [MAX_BATCH_EVENTS, 1]


def test_plan_batches_splits_at_max_bytes():
    batches = plan_batches(
        [wire_event(1, payload="a" * 600_000), wire_event(2, payload="b" * 600_000)],
        project="project-a",
        sdk_version="1.2.3",
    )
    assert len(batches) == 2
    assert all(
        len(json_bytes(build_envelope("project-a", "1.2.3", batch))) <= MAX_BATCH_BYTES
        for batch in batches
    )


def test_plan_batches_accepts_exact_envelope_limit_and_rejects_one_more_byte():
    event = wire_event(payload="")
    base_size = len(json_bytes(build_envelope("project-a", "1.2.3", [event])))
    exact = wire_event(payload="x" * (MAX_BATCH_BYTES - base_size))
    assert len(json_bytes(build_envelope("project-a", "1.2.3", [exact]))) == (
        MAX_BATCH_BYTES
    )
    assert plan_batches([exact], project="project-a", sdk_version="1.2.3") == [[exact]]
    too_large = wire_event(payload=exact["payload"] + "x")
    with pytest.raises(ValueError, match="event exceeds"):
        plan_batches([too_large], project="project-a", sdk_version="1.2.3")


def test_plan_batches_rejects_one_oversized_event():
    with pytest.raises(ValueError, match="event exceeds"):
        plan_batches([wire_event(payload="x" * MAX_BATCH_BYTES)])


def test_encode_batch_gzip_round_trips():
    envelope = build_envelope("project-a", "1.2.3", [wire_event()])
    body, headers = encode_batch(envelope)
    assert json.loads(gzip.decompress(body)) == envelope
    assert headers[HEADER_WIRE] == "1"
    assert headers["content-encoding"] == "gzip"
