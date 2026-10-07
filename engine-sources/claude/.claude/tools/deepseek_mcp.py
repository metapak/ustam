#!/usr/bin/env python3
"""Dependency-free MCP bridge for bounded DeepSeek implementation proposals."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

SERVER_NAME = "deepseek-bounded-proposal"
SERVER_VERSION = "0.5.0"
DEFAULT_ENDPOINT = "https://api.deepseek.com/responses"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_EFFORT = "high"
ALLOWED_EFFORTS = frozenset({"low", "high", "max"})
MAX_FIELD_CHARS = 250_000
MAX_REQUEST_CHARS = 300_000
MODEL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

SYSTEM_PROMPT = """You are an external, proposal-only implementation assistant.
Return a bounded implementation proposal, preferably as a unified diff. You cannot
read or write the workspace. Use only the reviewed context supplied in this request.
Change only explicitly allowed paths, preserve unrelated behavior, never include
secrets, and state missing evidence instead of guessing. A native Claude implementer
is the only writer and will review any proposal before applying it."""


class BridgeError(RuntimeError):
    """Expected bridge failure safe to return to the MCP client."""


def _text(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BridgeError(f"{name} must be a string")
    if required and not value.strip():
        raise BridgeError(f"{name} is required")
    if len(value) > MAX_FIELD_CHARS:
        raise BridgeError(f"{name} is too large")
    return value


def _paths(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BridgeError("allowed_paths must be a non-empty string array")
    if len(value) > 128:
        raise BridgeError("allowed_paths has too many entries")
    result: list[str] = []
    for item in value:
        path = _text(item, "allowed_paths item", required=True)
        if len(path) > 512:
            raise BridgeError("allowed_paths item is too large")
        if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            raise BridgeError(f"unsafe allowed path: {path}")
        result.append(path)
    return result


def build_prompt(arguments: dict[str, Any]) -> str:
    task = _text(arguments.get("task"), "task", required=True)
    context = _text(arguments.get("context"), "context", required=True)
    constraints = _text(arguments.get("constraints"), "constraints")
    allowed_paths = _paths(arguments.get("allowed_paths"))
    prompt = (
        f"TASK\n{task}\n\nALLOWED PATHS\n"
        + "\n".join(f"- {path}" for path in allowed_paths)
        + f"\n\nCONSTRAINTS\n{constraints or 'None supplied.'}"
        + f"\n\nREVIEWED CONTEXT PROVIDED BY THE OWNER\n{context}\n"
    )
    if len(prompt) > MAX_REQUEST_CHARS:
        raise BridgeError("combined request context is too large")
    return prompt


def validate_runtime() -> tuple[str, str, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise BridgeError("DEEPSEEK_API_KEY is not set in the Claude Code environment")
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    if not MODEL_TOKEN.fullmatch(model):
        raise BridgeError("DEEPSEEK_MODEL is invalid")
    effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", DEFAULT_EFFORT)
    if effort not in ALLOWED_EFFORTS:
        raise BridgeError("DEEPSEEK_REASONING_EFFORT must be low, high, or max")
    return api_key, model, effort


def extract_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    result = "\n".join(parts).strip()
    if not result:
        raise BridgeError("DeepSeek response did not contain output text")
    return result


def call_deepseek(prompt: str, *, endpoint: str | None = None) -> str:
    api_key, model, effort = validate_runtime()
    body = json.dumps(
        {
            "model": model,
            "reasoning": {"effort": effort},
            "instructions": SYSTEM_PROMPT,
            "input": prompt,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint or DEFAULT_ENDPOINT,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise BridgeError(f"DeepSeek API returned HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"DeepSeek API request failed: {exc}") from exc
    return extract_output_text(payload)


TOOL = {
    "name": "deepseek_bounded_proposal",
    "description": (
        "Ask DeepSeek for a proposal-only bounded implementation. The tool cannot "
        "read or write the workspace; pass only reviewed context and allowed paths."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Exact implementation objective"},
            "allowed_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Repository-relative paths the proposal may change",
            },
            "context": {"type": "string", "description": "Reviewed relevant file contents and evidence"},
            "constraints": {"type": "string", "description": "Invariants and acceptance criteria"},
        },
        "required": ["task", "allowed_paths", "context"],
        "additionalProperties": False,
    },
}


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        requested = message.get("params", {}).get("protocolVersion", "2025-06-18")
        result = {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params", {})
        if params.get("name") != TOOL["name"]:
            raise BridgeError(f"unknown tool: {params.get('name')}")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            raise BridgeError("tool arguments must be an object")
        proposal = call_deepseek(build_prompt(arguments))
        result = {"content": [{"type": "text", "text": proposal}], "isError": False}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for raw in sys.stdin:
        message: dict[str, Any] | None = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise BridgeError("message must be a JSON object")
            response = dispatch(message)
        except (BridgeError, json.JSONDecodeError) as exc:
            request_id = message.get("id") if isinstance(message, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
