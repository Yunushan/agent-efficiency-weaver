"""Standalone stdio MCP protocol for exposing selected portable Skill text."""

import json
from typing import BinaryIO, NoReturn

SERVER_NAME = "weaver-skills"
TOOL_NAME = "load_skill"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON property")
    return result


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError("Invalid JSON constant")


def serve(reader: BinaryIO, writer: BinaryIO, skills: dict[str, tuple[str, str]]) -> None:
    phase = "starting"
    description = "Load the instructions for one relevant portable Skill. Use each Skill once per conversation.\n"
    description += "\n".join(f"- {name} — {summary}" for name, (summary, _) in skills.items())
    tool = {
        "name": TOOL_NAME,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": list(skills)}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    }

    def respond(identifier: int | str | None, **fields: object) -> None:
        response = {"jsonrpc": "2.0", "id": identifier, **fields}
        writer.write((json.dumps(response, allow_nan=False) + "\n").encode("utf-8"))
        writer.flush()

    def error(identifier: int | str | None, code: int, message: str) -> None:
        respond(identifier, error={"code": code, "message": message})

    max_frame = 1024 * 1024
    while line := reader.readline(max_frame + 1):
        if len(line) > max_frame:
            error(None, -32600, "Request frame too large")
            return
        try:
            message = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            error(None, -32700, "Invalid JSON")
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            error(None, -32600, "Invalid request")
            continue
        method = message["method"]
        if "id" not in message:
            if method == "notifications/initialized" and phase == "initializing":
                phase = "ready"
            continue
        identifier = message["id"]
        if type(identifier) not in (str, int):
            error(None, -32600, "Invalid request ID")
            continue
        params = message.get("params", {})
        if not isinstance(params, dict):
            error(identifier, -32602, "Invalid params")
        elif method == "initialize" and phase == "starting":
            requested = params.get("protocolVersion")
            if not isinstance(requested, str):
                error(identifier, -32602, "A protocolVersion is required")
                continue
            phase = "initializing"
            respond(identifier, result={
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            })
        elif method == "ping":
            respond(identifier, result={})
        elif phase != "ready":
            error(identifier, -32600, "Initialize the session first")
        elif method == "tools/list":
            if "cursor" in params:
                error(identifier, -32602, "Invalid tools/list cursor")
            else:
                respond(identifier, result={"tools": [tool]})
        elif method == "tools/call":
            arguments = params.get("arguments")
            name = arguments.get("name") if isinstance(arguments, dict) and set(arguments) == {"name"} else None
            if params.get("name") != TOOL_NAME or not isinstance(name, str) or name not in skills:
                error(identifier, -32602, "Select an advertised Skill by its exact name")
            else:
                respond(identifier, result={"content": [{"type": "text", "text": skills[name][1]}]})
        else:
            error(identifier, -32601, "Method not found")
