"""MCP-first integration boundary."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from lucy.observe import Tracer, get_tracer


class McpTransport(Protocol):
    async def call_tool(
        self, server: str, tool: str, arguments: Dict[str, Any]
    ) -> Any: ...


@dataclass
class McpAuditEvent:
    server: str
    tool: str
    allowed: bool
    timestamp: float
    arguments: Dict[str, Any]
    result: Any = None
    error: str = ""


class McpPermissionError(PermissionError):
    """Raised when an MCP tool call is not allowed."""


class McpSchemaError(ValueError):
    """Raised when MCP tool arguments do not match the declared schema."""


class McpTimeoutError(TimeoutError):
    """Raised when an MCP tool call exceeds its deadline."""


@dataclass
class McpToolSchema:
    required_fields: Dict[str, Any]

    def validate(self, arguments: Dict[str, Any]) -> None:
        for name, expected_type in self.required_fields.items():
            if name not in arguments:
                raise McpSchemaError("missing required argument: %s" % name)
            if not isinstance(arguments[name], expected_type):
                raise McpSchemaError(
                    "argument '%s' must be %s" % (name, expected_type.__name__)
                )


class McpClient:
    def __init__(
        self,
        transport: McpTransport,
        allowed_tools: List[str],
        tool_schemas: Optional[Dict[str, McpToolSchema]] = None,
        default_timeout_ms: int = 1000,
        *,
        tracer: Optional[Tracer] = None,
    ):
        self.transport = transport
        self.allowed_tools = set(allowed_tools)
        self.tool_schemas = tool_schemas or {}
        self.default_timeout_ms = default_timeout_ms
        self.audit_log: List[McpAuditEvent] = []
        self._tracer = tracer

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: Dict[str, Any],
        timeout_ms: Optional[int] = None,
        *,
        session_id: str = "",
        turn_id: str = "",
        on_dispatch: Optional[Callable[[], None]] = None,
    ) -> Any:
        key = "%s.%s" % (server, tool)
        allowed = key in self.allowed_tools or tool in self.allowed_tools
        if not allowed:
            self._record_audit(
                McpAuditEvent(
                    server=server,
                    tool=tool,
                    allowed=False,
                    timestamp=time.time(),
                    arguments=arguments,
                    error="tool not allowed",
                ),
                session_id=session_id,
                turn_id=turn_id,
                latency_ms=0.0,
            )
            raise McpPermissionError("MCP tool is not allowed: %s" % key)

        schema = self.tool_schemas.get(key) or self.tool_schemas.get(tool)
        if schema is not None:
            try:
                schema.validate(arguments)
            except McpSchemaError as exc:
                self._record_audit(
                    McpAuditEvent(
                        server=server,
                        tool=tool,
                        allowed=True,
                        timestamp=time.time(),
                        arguments=arguments,
                        error=str(exc),
                    ),
                    session_id=session_id,
                    turn_id=turn_id,
                    latency_ms=0.0,
                )
                raise

        if on_dispatch is not None:
            on_dispatch()

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.transport.call_tool(server, tool, arguments),
                timeout=(timeout_ms or self.default_timeout_ms) / 1000,
            )
        except asyncio.CancelledError:
            self._record_audit(
                McpAuditEvent(
                    server=server,
                    tool=tool,
                    allowed=True,
                    timestamp=time.time(),
                    arguments=arguments,
                    error="cancelled",
                ),
                session_id=session_id,
                turn_id=turn_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        except asyncio.TimeoutError as exc:
            self._record_audit(
                McpAuditEvent(
                    server=server,
                    tool=tool,
                    allowed=True,
                    timestamp=time.time(),
                    arguments=arguments,
                    error="deadline exceeded",
                ),
                session_id=session_id,
                turn_id=turn_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise McpTimeoutError("deadline exceeded") from exc
        except Exception:
            self._record_audit(
                McpAuditEvent(
                    server=server,
                    tool=tool,
                    allowed=True,
                    timestamp=time.time(),
                    arguments=arguments,
                    error="transport error",
                ),
                session_id=session_id,
                turn_id=turn_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise

        self._record_audit(
            McpAuditEvent(
                server=server,
                tool=tool,
                allowed=True,
                timestamp=time.time(),
                arguments=arguments,
                result=result,
            ),
            session_id=session_id,
            turn_id=turn_id,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    def _record_audit(
        self,
        event: McpAuditEvent,
        *,
        session_id: str,
        turn_id: str,
        latency_ms: float,
    ) -> None:
        """Append an audit event and mirror it as a ``tool_call`` telemetry
        event. The tracer's privacy pass redacts ``arguments`` before export
        (wire spec). No-ops without a turn context or when tracing is disabled
        (zero overhead: no event built, no enqueue)."""
        self.audit_log.append(event)
        if not turn_id:
            return
        tracer = self._tracer if self._tracer is not None else get_tracer()
        if not tracer.enabled:
            return
        tracer.tool_call(
            session_id=session_id,
            turn_id=turn_id,
            server=event.server,
            tool=event.tool,
            allowed=event.allowed,
            latency_ms=max(0.0, latency_ms),
            arguments=event.arguments,
            error=event.error or None,
        )

    def replay_events(self) -> Dict[str, Any]:
        return {"events": [asdict(event) for event in self.audit_log]}

    def write_replay_file(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.replay_events(), indent=2, sort_keys=True),
            encoding="utf8",
        )


_MOVED_TO_TESTING = ("LocalMcpCommandTransport",)


def __getattr__(name: str) -> object:
    """Deprecation shim: the local transport moved to lucy.testing (card 22)."""
    if name in _MOVED_TO_TESTING:
        import warnings

        from lucy import testing

        warnings.warn(
            "lucy.mcp.%s moved to lucy.testing; import it from lucy.testing" % name,
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(testing, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
