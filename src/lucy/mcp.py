"""MCP-first integration boundary."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


class McpTransport(Protocol):
    async def call_tool(self, server: str, tool: str, arguments: Dict[str, Any]) -> Any:
        ...


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


class LocalMcpCommandTransport:
    """Deterministic local MCP transport for tests and development."""

    def __init__(self) -> None:
        self.commands: List[Dict[str, Any]] = []

    async def call_tool(self, server: str, tool: str, arguments: Dict[str, Any]) -> Any:
        command = {
            "command_id": "mcp_%s_%s_%s"
            % (server, tool, len(self.commands) + 1),
            "server": server,
            "tool": tool,
            "status": "queued",
            "arguments": arguments,
        }
        self.commands.append(command)
        return command


class McpClient:
    def __init__(
        self,
        transport: McpTransport,
        allowed_tools: List[str],
        tool_schemas: Optional[Dict[str, McpToolSchema]] = None,
        default_timeout_ms: int = 1000,
    ):
        self.transport = transport
        self.allowed_tools = set(allowed_tools)
        self.tool_schemas = tool_schemas or {}
        self.default_timeout_ms = default_timeout_ms
        self.audit_log: List[McpAuditEvent] = []

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: Dict[str, Any],
        timeout_ms: Optional[int] = None,
    ) -> Any:
        key = "%s.%s" % (server, tool)
        allowed = key in self.allowed_tools or tool in self.allowed_tools
        if not allowed:
            event = McpAuditEvent(
                server=server,
                tool=tool,
                allowed=False,
                timestamp=time.time(),
                arguments=arguments,
                error="tool not allowed",
            )
            self.audit_log.append(event)
            raise McpPermissionError("MCP tool is not allowed: %s" % key)

        schema = self.tool_schemas.get(key) or self.tool_schemas.get(tool)
        if schema is not None:
            try:
                schema.validate(arguments)
            except McpSchemaError as exc:
                self.audit_log.append(
                    McpAuditEvent(
                        server=server,
                        tool=tool,
                        allowed=True,
                        timestamp=time.time(),
                        arguments=arguments,
                        error=str(exc),
                    )
                )
                raise

        try:
            result = await asyncio.wait_for(
                self.transport.call_tool(server, tool, arguments),
                timeout=(timeout_ms or self.default_timeout_ms) / 1000,
            )
        except asyncio.TimeoutError as exc:
            self.audit_log.append(
                McpAuditEvent(
                    server=server,
                    tool=tool,
                    allowed=True,
                    timestamp=time.time(),
                    arguments=arguments,
                    error="deadline exceeded",
                )
            )
            raise McpTimeoutError("deadline exceeded") from exc

        self.audit_log.append(
            McpAuditEvent(
                server=server,
                tool=tool,
                allowed=True,
                timestamp=time.time(),
                arguments=arguments,
                result=result,
            )
        )
        return result

    def replay_events(self) -> Dict[str, Any]:
        return {"events": [asdict(event) for event in self.audit_log]}

    def write_replay_file(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.replay_events(), indent=2, sort_keys=True),
            encoding="utf8",
        )
