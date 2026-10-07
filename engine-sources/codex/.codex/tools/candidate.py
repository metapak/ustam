#!/usr/bin/env python3
"""Freeze and verify an exact Git worktree candidate without storing file contents.

The state file lives under .codex/.candidate/, whose bundled .gitignore keeps the
runtime state out of Git status. The snapshot records commit identity, staged and
unstaged diff hashes, status, and hashes of untracked non-ignored files.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
STATE_RELATIVE_PATH = Path(".codex/.candidate/candidate.json")
STABLE_ATTEMPTS = 3
CHUNK_SIZE = 1024 * 1024


class CandidateError(RuntimeError):
    """Expected candidate tool failure."""


@dataclass(frozen=True)
class GitResult:
    stdout: bytes
    stderr: bytes
    returncode: int


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def run_git(
    args: Iterable[str],
    *,
    cwd: Path,
    check: bool = True,
) -> GitResult:
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")
    process = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = GitResult(process.stdout, process.stderr, process.returncode)
    if check and process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise CandidateError(
            f"git {' '.join(args)} failed with exit {process.returncode}: {message}"
        )
    return result


def discover_root(start: Path) -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], cwd=start)
    raw = result.stdout.rstrip(b"\n")
    if not raw:
        raise CandidateError("Git returned an empty repository root.")
    return Path(os.fsdecode(raw)).resolve()


def decode_git_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def file_identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        st.st_mode,
        st.st_size,
        st.st_mtime_ns,
        getattr(st, "st_ino", 0),
        getattr(st, "st_dev", 0),
    )


def hash_regular_file(path: Path) -> tuple[str, os.stat_result]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise CandidateError(f"Untracked path changed type while hashing: {path}")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT}:
            raise CandidateError(f"Untracked path changed while hashing: {path}") from exc
        raise

    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    after = path.lstat()
    if file_identity(before) != file_identity(after):
        raise CandidateError(f"Untracked file changed while hashing: {path}")
    return digest.hexdigest(), after


def untracked_entry(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise CandidateError(f"Untracked path disappeared while hashing: {relative}") from exc

    mode = stat.S_IMODE(st.st_mode)
    entry: dict[str, Any] = {
        "path": relative,
        "mode": f"{mode:04o}",
        "size": st.st_size,
    }

    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(path)
        target_bytes = os.fsencode(target)
        entry.update(
            {
                "type": "symlink",
                "sha256": sha256_bytes(target_bytes),
            }
        )
        return entry

    if stat.S_ISREG(st.st_mode):
        digest, final_stat = hash_regular_file(path)
        entry.update(
            {
                "type": "file",
                "size": final_stat.st_size,
                "sha256": digest,
            }
        )
        return entry

    entry.update(
        {
            "type": "special",
            "sha256": sha256_bytes(
                canonical_json_bytes(
                    {
                        "mode": st.st_mode,
                        "size": st.st_size,
                        "mtime_ns": st.st_mtime_ns,
                    }
                )
            ),
        }
    )
    return entry


def collect_untracked(root: Path) -> list[dict[str, Any]]:
    result = run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    raw_paths = [item for item in result.stdout.split(b"\0") if item]
    decoded = [decode_git_path(item) for item in raw_paths]
    decoded.sort(key=os.fsencode)
    return [untracked_entry(root, relative) for relative in decoded]


def optional_git_text(args: list[str], root: Path, fallback: str) -> str:
    result = run_git(args, cwd=root, check=False)
    if result.returncode != 0:
        return fallback
    return result.stdout.decode("utf-8", errors="replace").strip() or fallback


def collect_snapshot_once(root: Path) -> dict[str, Any]:
    head = optional_git_text(["rev-parse", "--verify", "HEAD"], root, "UNBORN")
    branch = optional_git_text(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], root, "DETACHED"
    )

    diff_options = [
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    ]
    staged = run_git(["diff", "--cached", *diff_options], cwd=root).stdout
    unstaged = run_git(["diff", *diff_options], cwd=root).stdout
    status_output = run_git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
    ).stdout
    untracked = collect_untracked(root)

    core: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "head": head,
        "branch": branch,
        "clean": len(status_output) == 0,
        "staged_diff_sha256": sha256_bytes(staged),
        "unstaged_diff_sha256": sha256_bytes(unstaged),
        "status_sha256": sha256_bytes(status_output),
        "untracked_count": len(untracked),
        "untracked_manifest_sha256": sha256_bytes(canonical_json_bytes(untracked)),
        "untracked": untracked,
    }
    core["fingerprint"] = sha256_bytes(canonical_json_bytes(core))
    return core


def collect_stable_snapshot(root: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, STABLE_ATTEMPTS + 1):
        try:
            first = collect_snapshot_once(root)
            second = collect_snapshot_once(root)
        except (CandidateError, OSError) as exc:
            last_error = exc
            if attempt < STABLE_ATTEMPTS:
                time.sleep(0.1 * attempt)
                continue
            raise CandidateError(f"Could not capture a stable candidate: {exc}") from exc

        if first == second:
            return first
        last_error = CandidateError("Repository changed while the snapshot was captured.")
        if attempt < STABLE_ATTEMPTS:
            time.sleep(0.1 * attempt)

    raise CandidateError(f"Could not capture a stable candidate: {last_error}")


def state_path(root: Path) -> Path:
    return root / STATE_RELATIVE_PATH


def read_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidateError(
            "No frozen candidate exists. Run 'candidate.py freeze' first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CandidateError(f"Candidate state is invalid JSON: {path}") from exc

    if data.get("schema") != SCHEMA_VERSION or not isinstance(data.get("snapshot"), dict):
        raise CandidateError(f"Unsupported or malformed candidate state: {path}")
    return data


def write_state(root: Path, data: dict[str, Any]) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="candidate.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def output(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
        return

    status = value.get("status", "UNKNOWN")
    print(status)
    for key in (
        "label",
        "generation",
        "fingerprint",
        "head",
        "branch",
        "clean",
        "created_utc",
        "verified_utc",
        "message",
    ):
        if key in value:
            print(f"{key}: {value[key]}")
    mismatches = value.get("mismatches")
    if mismatches:
        print("mismatches:")
        for mismatch in mismatches:
            print(f"  - {mismatch}")


def freeze(root: Path, label: str, require_clean: bool, as_json: bool) -> int:
    snapshot = collect_stable_snapshot(root)
    if require_clean and not snapshot["clean"]:
        raise CandidateError("The worktree is not clean, but --require-clean was set.")

    path = state_path(root)
    generation = 1
    if path.exists():
        try:
            existing = read_state(root)
            generation = int(existing.get("generation", 0)) + 1
        except (CandidateError, TypeError, ValueError):
            generation = 1

    data = {
        "schema": SCHEMA_VERSION,
        "generation": generation,
        "label": label,
        "created_utc": utc_now(),
        "snapshot": snapshot,
    }
    write_state(root, data)
    output(
        {
            "status": "FROZEN",
            "label": label,
            "generation": generation,
            "fingerprint": snapshot["fingerprint"],
            "head": snapshot["head"],
            "branch": snapshot["branch"],
            "clean": snapshot["clean"],
            "created_utc": data["created_utc"],
        },
        as_json,
    )
    return 0


def verify(root: Path, as_json: bool) -> int:
    saved = read_state(root)
    expected = saved["snapshot"]
    current = collect_stable_snapshot(root)

    fields = (
        "head",
        "branch",
        "clean",
        "staged_diff_sha256",
        "unstaged_diff_sha256",
        "status_sha256",
        "untracked_count",
        "untracked_manifest_sha256",
        "fingerprint",
    )
    mismatches = [
        f"{field}: expected {expected.get(field)!r}, got {current.get(field)!r}"
        for field in fields
        if expected.get(field) != current.get(field)
    ]

    if mismatches:
        output(
            {
                "status": "MISMATCH",
                "label": saved.get("label", ""),
                "generation": saved.get("generation"),
                "fingerprint": current["fingerprint"],
                "head": current["head"],
                "branch": current["branch"],
                "clean": current["clean"],
                "verified_utc": utc_now(),
                "mismatches": mismatches,
            },
            as_json,
        )
        return 1

    output(
        {
            "status": "VERIFIED",
            "label": saved.get("label", ""),
            "generation": saved.get("generation"),
            "fingerprint": current["fingerprint"],
            "head": current["head"],
            "branch": current["branch"],
            "clean": current["clean"],
            "verified_utc": utc_now(),
        },
        as_json,
    )
    return 0


def show(root: Path, as_json: bool) -> int:
    saved = read_state(root)
    if as_json:
        print(json.dumps(saved, ensure_ascii=True, indent=2, sort_keys=True))
        return 0

    snapshot = saved["snapshot"]
    output(
        {
            "status": "FROZEN",
            "label": saved.get("label", ""),
            "generation": saved.get("generation"),
            "fingerprint": snapshot.get("fingerprint"),
            "head": snapshot.get("head"),
            "branch": snapshot.get("branch"),
            "clean": snapshot.get("clean"),
            "created_utc": saved.get("created_utc"),
        },
        False,
    )
    return 0


def clear(root: Path, as_json: bool) -> int:
    path = state_path(root)
    existed = path.exists()
    if existed:
        path.unlink()
    output(
        {
            "status": "CLEARED" if existed else "EMPTY",
            "message": str(path.relative_to(root)),
        },
        as_json,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze and verify an exact Git worktree candidate."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Path inside the Git repository. Defaults to the current directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subparsers.add_parser("freeze", help="Capture a candidate.")
    freeze_parser.add_argument("--label", default="pre-review")
    freeze_parser.add_argument("--require-clean", action="store_true")
    freeze_parser.add_argument("--json", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Verify the candidate.")
    verify_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="Show the frozen candidate.")
    show_parser.add_argument("--json", action="store_true")

    clear_parser = subparsers.add_parser("clear", help="Remove candidate state.")
    clear_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = discover_root(args.repo.resolve())
        if args.command == "freeze":
            return freeze(root, args.label, args.require_clean, args.json)
        if args.command == "verify":
            return verify(root, args.json)
        if args.command == "show":
            return show(root, args.json)
        if args.command == "clear":
            return clear(root, args.json)
        parser.error(f"Unknown command: {args.command}")
    except (CandidateError, OSError) as exc:
        print(f"candidate error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
