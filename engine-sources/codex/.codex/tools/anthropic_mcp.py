#!/usr/bin/env python3
"""Read-only MCP bridge that asks Claude for a bounded implementation proposal."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import PurePosixPath
from typing import Any, TextIO

SERVER_NAME = "codex-bounded-anthropic-bridge"
SERVER_VERSION = "0.6.0"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "high"
EFFORTS = {"auto", "low", "medium", "high", "xhigh", "max"}
MAX_CONTEXT_CHARS = 200_000
MAX_TASK_CHARS = 20_000
MAX_ALLOWED_PATHS = 64


class BridgeError(RuntimeError):
    """Expected provider or input failure."""


def validate_allowed_paths(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise BridgeError("allowed_paths must be a non-empty list of repository-relative paths")
    if len(values) > MAX_ALLOWED_PATHS:
        raise BridgeError(f"allowed_paths may contain at most {MAX_ALLOWED_PATHS} entries")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise BridgeError("each allowed path must be a non-empty string")
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.endswith("/"):
            raise BridgeError(f"unsafe allowed path: {value!r}")
        result.append(path.as_posix())
    return result


def build_prompt(arguments: dict[str, Any]) -> str:
    task = arguments.get("task")
    context = arguments.get("context")
    constraints = arguments.get("constraints", "")
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
        raise BridgeError(f"task must be 1-{MAX_TASK_CHARS} characters")
    if not isinstance(context, str) or len(context) > MAX_CONTEXT_CHARS:
        raise BridgeError(f"context must be a string up to {MAX_CONTEXT_CHARS} characters")
    if not isinstance(constraints, str) or len(constraints) > MAX_TASK_CHARS:
        raise BridgeError(f"constraints must be a string up to {MAX_TASK_CHARS} characters")
    allowed_paths = validate_allowed_paths(arguments.get("allowed_paths"))
    return (
        "You are an external implementation-proposal model in a bounded workflow. "
        "You cannot read or write the repository. Use only the supplied context. "
        "Return a unified diff limited exactly to the allowed paths, followed by short "
        "validation notes. Do not claim that you applied or tested the patch. If the "
        "context is insufficient, say so instead of guessing.\n\n"
        f"TASK\n{task.strip()}\n\n"
        f"ALLOWED PATHS\n" + "\n".join(f"- {path}" for path in allowed_paths) + "\n\n"
        f"CONSTRAINTS\n{constraints.strip() or '(none supplied)'}\n\n"
        f"SUPPLIED CONTEXT\n{context}"
    )


def extract_text(response: dict[str, Any]) -> str:
    blocks = response.get("content")
    if not isinstance(blocks, list):
        raise BridgeError("Anthropic response did not contain a content list")
    parts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if isinstance(part, str)).strip()
    if not text:
        raise BridgeError("Anthropic response did not contain text output")
    return text


def call_anthropic(
    arguments: dict[str, Any],
    *,
    default_model: str,
    default_effort: str,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 120.0,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BridgeError("ANTHROPIC_API_KEY is not set in the MCP server environment")
    model = arguments.get("model", default_model)
    effort = arguments.get("effort", default_effort)
    max_tokens = arguments.get("max_tokens", 4096)
    if not isinstance(model, str) or not model.strip() or len(model) > 120:
        raise BridgeError("model must be a non-empty string up to 120 characters")
    if effort not in EFFORTS:
        raise BridgeError(f"effort must be one of: {', '.join(sorted(EFFORTS))}")
    if (model == "claude-haiku-4-5-20251001") != (effort == "auto"):
        raise BridgeError("Claude Haiku 4.5 requires auto effort; other Claude models need an explicit effort")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 256 <= max_tokens <= 16384:
        raise BridgeError("max_tokens must be an integer from 256 to 16384")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": build_prompt(arguments)}],
    }
    if effort != "auto":
        body["output_config"] = {"effort": effort}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "user-agent": f"{SERVER_NAME}/{SERVER_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace").replace(
            api_key, "[REDACTED]"
        )
        raise BridgeError(f"Anthropic API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"Anthropic API request failed: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("Anthropic API returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise BridgeError("Anthropic API returned a non-object response")
    return extract_text(payload)


def tool_definition() -> dict[str, Any]:
    return {
        "name": "claude_implementation_proposal",
        "description": (
            "Ask Claude for a read-only, bounded patch proposal. The bridge has no "
            "workspace access and never applies changes; a native single writer must "
            "review and apply any accepted patch."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "context", "allowed_paths"],
            "properties": {
                "task": {"type": "string", "minLength": 1, "maxLength": MAX_TASK_CHARS},
                "context": {"type": "string", "maxLength": MAX_CONTEXT_CHARS},
                "constraints": {"type": "string", "maxLength": MAX_TASK_CHARS},
                "allowed_paths": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ALLOWED_PATHS,
                    "items": {"type": "string", "minLength": 1},
                },
                "model": {"type": "string", "minLength": 1, "maxLength": 120},
                "effort": {"type": "string", "enum": sorted(EFFORTS)},
                "max_tokens": {"type": "integer", "minimum": 256, "maximum": 16384},
            },
        },
    }


def handle_request(
    message: dict[str, Any], *, default_model: str, default_effort: str, endpoint: str
) -> dict[str, Any] | None:
    if "id" not in message:
        return None
    request_id = message.get("id")
    method = message.get("method")
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [tool_definition()]}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict) or params.get("name") != "claude_implementation_proposal":
                raise BridgeError("unknown tool")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise BridgeError("tool arguments must be an object")
            proposal = call_anthropic(
                arguments,
                default_model=default_model,
                default_effort=default_effort,
                endpoint=endpoint,
            )
            result = {"content": [{"type": "text", "text": proposal}], "isError": False}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except BridgeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"Claude bridge error: {exc}"}],
                "isError": True,
            },
        }


def serve(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    default_model: str,
    default_effort: str,
    endpoint: str,
) -> None:
    for raw in input_stream:
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("request must be an object")
            response = handle_request(
                message,
                default_model=default_model,
                default_effort=default_effort,
                endpoint=endpoint,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        if response is not None:
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=sorted(EFFORTS), default=DEFAULT_EFFORT)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve(
        sys.stdin,
        sys.stdout,
        default_model=args.model,
        default_effort=args.effort,
        endpoint=args.endpoint,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
