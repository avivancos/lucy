"""Sentence assembly and TTS planning (ADR 0011).

The streaming driver feeds LLM ``TokenDelta`` text into a
:class:`SentenceAssembler`, which flushes a clause as soon as a boundary char
arrives and the buffered clause is long enough, so the first clause can be
speaking while generation continues. :class:`TtsPlanner` turns each clause into
an ordered ``TtsSpeak`` directive and reconstructs what the caller actually
heard when a turn is interrupted.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from lucy.transport.schema import TtsCancel, TtsPlayback, TtsSpeak

CLAUSE_BOUNDARY_CHARS: Tuple[str, ...] = (".", "!", "?", ",", ";", ":")
# The single home for the minimum clause length before a boundary triggers a flush.
MIN_FLUSH_CHARS = 12


class SentenceAssembler:
    def __init__(self, min_flush_chars: int = MIN_FLUSH_CHARS) -> None:
        self._min = min_flush_chars
        self._buffer = ""

    def feed(self, text: str) -> List[str]:
        self._buffer += text
        flushed: List[str] = []
        while True:
            index = self._first_flushable_boundary()
            if index is None:
                break
            clause = self._buffer[: index + 1].strip()
            self._buffer = self._buffer[index + 1 :]
            if clause:
                flushed.append(clause)
        return flushed

    def finalize(self) -> List[str]:
        remainder = self._buffer.strip()
        self._buffer = ""
        return [remainder] if remainder else []

    def has_buffered(self) -> bool:
        """True when an unflushed clause is forming. The tool-round driver
        suppresses a latency-masking filler when a clause is already buffered
        (card 34) so the filler never overlaps real speech."""
        return bool(self._buffer.strip())

    def _first_flushable_boundary(self) -> int | None:
        for i, char in enumerate(self._buffer):
            if (
                char in CLAUSE_BOUNDARY_CHARS
                and len(self._buffer[: i + 1].strip()) >= self._min
            ):
                return i
        return None


# Utterance-id namespace for planner-issued directives (single home; the
# planner grew no second caller that needed a distinct prefix, card 64).
_UTTERANCE_ID_PREFIX = "utt"


class TtsPlanner:
    """Plans ordered ``TtsSpeak`` directives and tracks playback so the driver
    can reconstruct the spoken text (truncated to what was heard on a barge-in)."""

    def __init__(self) -> None:
        self._counter = 0
        self._utterances: List[Tuple[str, str]] = []  # (utterance_id, text) in order
        self._marks: Dict[str, int] = {}
        self._finished: set[str] = set()

    def plan(self, clause: str) -> TtsSpeak:
        self._counter += 1
        utterance_id = "%s_%04d" % (_UTTERANCE_ID_PREFIX, self._counter)
        self.register(utterance_id, clause)
        return TtsSpeak(utterance_id=utterance_id, text=clause, flush=True)

    def register(self, utterance_id: str, text: str) -> None:
        """Track an utterance planned elsewhere (e.g. by the driver) so this
        planner can still reconstruct spoken text from playback events."""
        self._utterances.append((utterance_id, text))

    def is_last(self, utterance_id: str) -> bool:
        """True when the id names the most recently registered utterance. The
        session treats only the last utterance's ``finished`` playback as the
        turn's terminal event (multi-clause turns, card 64)."""
        return bool(self._utterances) and self._utterances[-1][0] == utterance_id

    def record_playback(self, event: TtsPlayback) -> None:
        self._marks[event.utterance_id] = max(
            self._marks.get(event.utterance_id, 0), event.mark_chars
        )
        if event.state == "finished":
            self._finished.add(event.utterance_id)

    def spoken_text(self) -> str:
        parts: List[str] = []
        for utterance_id, text in self._utterances:
            if utterance_id in self._finished:
                parts.append(text)
            elif utterance_id in self._marks:
                heard = text[: self._marks[utterance_id]]
                if heard:
                    parts.append(heard)
                break  # nothing after the interrupted utterance was heard
            else:
                break
        return " ".join(parts)

    def cancel_directive(self) -> TtsCancel:
        return TtsCancel(utterance_id="all")
