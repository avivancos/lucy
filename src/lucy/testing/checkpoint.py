"""Reusable no-mocks conformance checks for checkpoint-store adapters."""

from __future__ import annotations

from lucy.state import Checkpoint, CheckpointStore, ConversationState, checkpoint_id


def _checkpoint(
    *,
    thread_id: str,
    session_id: str,
    turn_id: str,
    superstep: int,
    slots: dict[str, str],
) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id(session_id, turn_id, superstep),
        session_id=session_id,
        thread_id=thread_id,
        turn_id=turn_id,
        superstep=superstep,
        kind="turn_final",
        state=ConversationState(slots=slots, turns=superstep),
        created_at_ms=superstep,
    )


async def check_checkpoint_store(
    store: CheckpointStore,
    *,
    thread_id: str,
) -> None:
    """Assert ordering, cross-session identity, copies, and idempotent save."""
    first = _checkpoint(
        thread_id=thread_id,
        session_id="conformance-call-1",
        turn_id="turn-1",
        superstep=1,
        slots={"status": "first"},
    )
    second = _checkpoint(
        thread_id=thread_id,
        session_id="conformance-call-2",
        turn_id="turn-2",
        superstep=2,
        slots={"status": "second"},
    )
    await store.save(first)
    await store.save(second)

    latest = await store.load_latest(thread_id)
    assert latest is not None
    assert latest.session_id == "conformance-call-2"
    assert [item.session_id for item in await store.history(thread_id)] == [
        "conformance-call-1",
        "conformance-call-2",
    ]

    corrected = second.model_copy(deep=True)
    corrected.state.slots["status"] = "corrected"
    await store.save(corrected)
    history = await store.history(thread_id)
    assert len(history) == 2
    assert history[-1].state.slots == {"status": "corrected"}

    corrected_first = first.model_copy(deep=True)
    corrected_first.state.slots["status"] = "first-corrected"
    await store.save(corrected_first)
    reordered = await store.history(thread_id)
    assert [item.session_id for item in reordered] == [
        "conformance-call-1",
        "conformance-call-2",
    ]
    assert reordered[0].state.slots == {"status": "first-corrected"}

    history[-1].state.slots["status"] = "mutated"
    fresh = await store.load_latest(thread_id)
    assert fresh is not None
    assert fresh.state.slots == {"status": "corrected"}
