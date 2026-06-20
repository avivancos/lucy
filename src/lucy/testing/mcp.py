"""Deterministic local MCP transport for tests and development (ADR 0003)."""

from __future__ import annotations

from typing import Any, Dict, List


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
