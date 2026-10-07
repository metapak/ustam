#!/usr/bin/env python3
"""Run one explicitly selected local evaluation without a shell."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

RUNTIME = Path(".claude/.bounded-orchestrator/evals")
MAX_TIMEOUT = 1800
MAX_TAIL = 2000
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EVAL_RUNTIME = Path(".claude/.bounded-orchestrator/evals")


class EvalError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def clean_tail(data: bytes) -> str:
    text = data[-8192:].decode("utf-8", "replace")
    text = "\n".join(line.rstrip() for line in text.splitlines()[-20:])
    text = re.sub(r"(?i)(api[_-]?key|authorization|token|secret)(\s*[:=]\s*)\S+", r"\1\2[redacted]", text)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", text)
    return "".join(ch if ch in "\n\t" or ord(ch) >= 32 else "?" for ch in text)[-MAX_TAIL:]


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot read evaluation manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalError("evaluation manifest must be a JSON object")
    label, argv, timeout = data.get("label"), data.get("argv"), data.get("timeout_seconds", 300)
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise EvalError("label must be a short safe identifier")
    if not isinstance(argv, list) or not argv or len(argv) > 64 or not all(
        isinstance(item, str) and item and "\x00" not in item and len(item) <= 512 for item in argv
    ):
        raise EvalError("argv must be a non-empty bounded string array")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise EvalError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT}")
    return {"label": label, "argv": argv, "timeout_seconds": timeout,
            "include_sanitized_tail": data.get("include_sanitized_tail") is True}


def git_bytes(root: Path, arguments: list[str], *, allow_failure: bool = False) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False, timeout=30)
    if result.returncode and not allow_failure:
        raise EvalError("cannot fingerprint the current Git candidate")
    return result.stdout


def candidate_fingerprint(root: Path) -> str:
    digest = hashlib.sha256(b"bounded-candidate-v1\0")
    head = git_bytes(root, ["rev-parse", "--verify", "HEAD"], allow_failure=True).strip() or b"UNBORN"
    digest.update(b"HEAD\0" + head + b"\0")
    digest.update(b"TRACKED\0" + git_bytes(root, ["diff", "--raw", "--no-ext-diff", "HEAD"], allow_failure=True) + b"\0")
    listed = git_bytes(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    for raw in sorted(item for item in listed.split(b"\0") if item):
        relative = Path(os.fsdecode(raw))
        if relative == EVAL_RUNTIME or EVAL_RUNTIME in relative.parents:
            continue
        path = root / relative
        digest.update(b"PATH\0" + raw + b"\0")
        if path.is_symlink():
            digest.update(b"SYMLINK\0" + os.fsencode(os.readlink(path)) + b"\0")
        elif path.is_file():
            digest.update(b"FILE\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        elif path.is_dir():
            subhead = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     check=False, timeout=10).stdout.strip()
            digest.update(b"DIR\0" + subhead + b"\0")
        else:
            digest.update(b"MISSING\0")
    return digest.hexdigest()


def run(root: Path, manifest_path: Path) -> dict:
    manifest = load_manifest(manifest_path)
    candidate_before = candidate_fingerprint(root)
    started_at, started = now(), time.monotonic()
    outcome, returncode, output = "fail", None, b""
    try:
        result = subprocess.run(manifest["argv"], cwd=root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                check=False, timeout=manifest["timeout_seconds"], shell=False)
        returncode, output = result.returncode, result.stdout[-65536:]
        outcome = "pass" if returncode == 0 else "fail"
    except subprocess.TimeoutExpired as exc:
        outcome, output = "timeout", (exc.output or b"")[-65536:]
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    candidate_after = candidate_fingerprint(root)
    if candidate_after != candidate_before:
        outcome = "candidate_changed"
    summary = {
        "schema": 1, "label": manifest["label"], "started_at": started_at, "ended_at": now(),
        "duration_ms": int((time.monotonic() - started) * 1000), "exit_code": returncode,
        "outcome": outcome,
        "fingerprint": candidate_before,
        "candidate_fingerprint": candidate_before,
        "candidate_fingerprint_before": candidate_before,
        "candidate_fingerprint_after": candidate_after,
        "manifest_fingerprint": hashlib.sha256(manifest_bytes).hexdigest(),
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }
    if manifest["include_sanitized_tail"]:
        summary["sanitized_tail"] = clean_tail(output)
    destination = root / RUNTIME / f"{manifest['label']}.json"
    atomic_json(destination, summary)
    summary["summary_path"] = destination.relative_to(root).as_posix()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.repo.expanduser().resolve()
        if not (root / ".git").exists():
            raise EvalError("repository must be a Git repository")
        summary = run(root, args.manifest.expanduser().resolve())
    except (EvalError, OSError) as exc:
        print(f"local eval error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Local evaluation {summary['label']}: {summary['outcome']}")
        print(f"Summary: {summary['summary_path']}")
    return 0 if summary["outcome"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
