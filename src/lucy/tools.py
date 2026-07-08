"""Real-time MCP tool contracts and executor (ADR 0011, ADR 0003, ADR 0010).

Wraps the UNCHANGED :class:`~lucy.mcp.McpClient` (``src/lucy/mcp.py``) with
per-tool latency profiles, a latency-masking filler policy, and a typed
:class:`ToolResult` that lets the LLM recover verbally from permission,
schema, timeout, unknown-tool, or budget failures. Permissions, schema
validation, and audit stay INSIDE ``McpClient``; this module never calls a
transport directly. Telemetry leaves only through the injected ``emit``
callback (ADR 0010 client-side-safe seam).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from lucy.clock import Clock
from lucy.mcp import (
    McpClient,
    McpPermissionError,
    McpSchemaError,
    McpTimeoutError,
)


class BargeInPolicy(str, Enum):
    """What happens to an in-flight tool call when the caller barges in:
    cancel it, or let it run to completion (defined here, enforced in card 35)."""

    CANCEL = "cancel"
    RUN_TO_COMPLETION = "run_to_completion"


@dataclass(frozen=True)
class ToolProfile:
    """Per-tool latency and interaction policy: the expected and hard-deadline
    latencies, whether a barge-in cancels the call, and whether the driver may
    speak a latency-masking filler while the tool runs."""

    expected_latency_ms: int
    deadline_ms: int
    on_barge_in: BargeInPolicy = (
        BargeInPolicy.CANCEL
    )  # defined here, enforced in card 35
    speak_filler: bool = False


@dataclass(frozen=True)
class ToolDef:
    server: str
    name: str
    description: str
    json_schema: dict
    profile: ToolProfile

    @property
    def key(self) -> str:
        # The exact "<server>.<name>" key McpClient.allowed_tools matches against.
        return "%s.%s" % (self.server, self.name)


@dataclass
class ToolResult:
    tool_key: str
    ok: bool
    value: Any = None
    # "" | "permission" | "schema" | "timeout" | "unknown_tool" | "budget"
    error_kind: str = ""
    error: str = ""
    elapsed_ms: float = 0.0

    def to_llm_message(self) -> dict:
        """Render a ``role="tool"`` message the LLM can read to recover verbally.

        Content is a JSON string carrying either the value or the typed error;
        keys are exactly those ``LlmMessage`` (extra="forbid") accepts.
        """
        if self.ok:
            content = json.dumps({"ok": True, "value": self.value}, default=str)
        else:
            content = json.dumps(
                {"ok": False, "error_kind": self.error_kind, "error": self.error},
                default=str,
            )
        return {"role": "tool", "content": content, "tool_call_id": self.tool_key}


# The single home for filler text (agents.md: no filler strings anywhere else).
# Locale -> ordered utterances; multiple entries enable deterministic rotation.
DEFAULT_FILLERS: Mapping[str, Sequence[str]] = {
    "en-US": ("One moment.", "Let me check that.", "Give me a second."),
    "es-ES": ("Un momento.", "Déjame comprobarlo.", "Dame un segundo."),
}


class FillerPolicy:
    """Locale -> filler utterances, with a deterministic per-locale rotation so
    tests are reproducible (index 0, 1, 2, 0, ... per policy instance)."""

    def __init__(self, fillers: Mapping[str, Sequence[str]]) -> None:
        self._fillers = fillers
        self._rotation: Dict[str, int] = {}

    def filler_for(self, tool: ToolDef, locale: str) -> Optional[str]:
        if not tool.profile.speak_filler:
            return None
        options = self._fillers.get(locale)
        if not options:
            return None
        index = self._rotation.get(locale, 0) % len(options)
        self._rotation[locale] = index + 1
        return options[index]


class McpToolExecutor:
    """Runs a :class:`ToolDef` through the reused ``McpClient`` and returns a
    typed :class:`ToolResult`. Never re-raises to the driver; converts the
    client's typed exceptions into ``error_kind`` values so the model can
    apologise or retry verbally."""

    def __init__(
        self,
        client: McpClient,
        clock: Clock,
        emit: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._emit = emit

    async def execute(self, tool: ToolDef, arguments: dict) -> ToolResult:
        started = self._clock.monotonic()
        ok = False
        value: Any = None
        error_kind = ""
        error = ""
        try:
            value = await self._client.call_tool(
                tool.server,
                tool.name,
                arguments,
                timeout_ms=tool.profile.deadline_ms,
            )
            ok = True
        except McpPermissionError as exc:
            error_kind, error = "permission", str(exc)
        except McpSchemaError as exc:
            error_kind, error = "schema", str(exc)
        except McpTimeoutError as exc:
            error_kind, error = "timeout", str(exc)
        elapsed_ms = (self._clock.monotonic() - started) * 1000.0

        result = ToolResult(
            tool_key=tool.key,
            ok=ok,
            value=value,
            error_kind=error_kind,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        if self._emit is not None:
            # Redaction-safe by construction: exactly these five non-PII keys,
            # never `arguments` or `value` (ADR 0010 client-side-safe). This is a
            # raw SDK seam, not the wire boundary; if a caller ever needs richer
            # fields, route them through lucy.observe so the wire-spec redaction
            # pass governs them - do not widen this dict with unredacted payload.
            self._emit(
                {
                    "server": tool.server,
                    "tool": tool.name,
                    "ok": ok,
                    "error_kind": error_kind,
                    "elapsed_ms": elapsed_ms,
                }
            )
        return result
