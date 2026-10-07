#!/usr/bin/env python3
"""Track declared orchestration work as local metadata-only JSON.

The ledger records short task identifiers, short labels, dependencies, states, and
timestamps. Do not put prompts, source code, logs, credentials, or secrets in it.
Runtime files live under .codex/.bounded-orchestrator/runs/ and are ignored by Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
RUNTIME_RELATIVE = Path(".codex/.bounded-orchestrator")
RUNS_RELATIVE = RUNTIME_RELATIVE / "runs"
CURRENT_RELATIVE = RUNTIME_RELATIVE / "current.json"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_LABEL_LENGTH = 160
MAX_REASON_LENGTH = 240
TASK_STATES = {"pending", "in_progress", "complete", "blocked", "skipped", "interrupted", "waiting_user", "needs_repair"}
MAX_ATTEMPTS = 2
EVAL_RUNTIME = Path(".codex/.bounded-orchestrator/evals")


class LedgerError(RuntimeError):
    """Expected command or ledger validation failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def discover_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise LedgerError("Run this command inside a Git repository.")
    return Path(result.stdout.strip()).resolve()


def validate_id(value: str, kind: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise LedgerError(
            f"Invalid {kind} ID {value!r}; use 1-64 letters, digits, '.', '_' or '-'."
        )
    return value


def validate_text(value: str, kind: str, maximum: int, *, allow_empty: bool = False) -> str:
    value = value.strip()
    if not value and not allow_empty:
        raise LedgerError(f"{kind} must not be empty.")
    if any(character in value for character in "\r\n\x00"):
        raise LedgerError(f"{kind} must be one line.")
    if len(value) > maximum:
        raise LedgerError(f"{kind} must be at most {maximum} characters.")
    return value


def ensure_runtime(root: Path) -> Path:
    runtime = root / RUNTIME_RELATIVE
    if runtime.is_symlink() or (runtime.exists() and not runtime.is_dir()):
        raise LedgerError("The reserved runtime path must be a real directory.")
    ignore = runtime / ".gitignore"
    if not ignore.is_file() or ignore.read_text(encoding="utf-8") != "*\n!.gitignore\n":
        raise LedgerError(
            "The reserved runtime ignore is missing or changed; reinstall before using the ledger."
        )
    runs = root / RUNS_RELATIVE
    if runs.is_symlink() or (runs.exists() and not runs.is_dir()):
        raise LedgerError("The ledger runs path must be a real directory.")
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(runtime, 0o700)
        os.chmod(runs, 0o700)
    except OSError:
        pass
    return runs


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        try:
            permission_setter = getattr(os, "fchmod", None)
            if permission_setter is not None:
                try:
                    permission_setter(descriptor, 0o600)
                except OSError:
                    pass
            data = (
                json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise OSError("Could not write ledger temporary file.")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            # Close on every permission or write failure path. Windows refuses
            # to replace or remove a file while its descriptor is open.
            os.close(descriptor)
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_json(path: Path, description: str) -> dict[str, Any]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LedgerError(f"Invalid {description} path: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LedgerError(f"{description} was not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f"Cannot read {description}: {path}") from exc
    if not isinstance(value, dict):
        raise LedgerError(f"Invalid {description}: expected a JSON object.")
    return value


def append_event(task: dict[str, Any], state: str, evidence: str | None = None) -> None:
    events = task.setdefault("events", [])
    event = {"event_id": f"e{len(events) + 1:04d}", "state": state, "at": utc_now()}
    if evidence:
        event["evidence"] = evidence
    events.append(event)


def migrate_run(run: dict[str, Any]) -> dict[str, Any]:
    """Upgrade schema 1 in memory without changing stable run or task IDs."""
    if run.get("schema") == SCHEMA_VERSION:
        return run
    if run.get("schema") != 1 or not isinstance(run.get("tasks"), dict):
        raise LedgerError("Unsupported ledger schema.")
    run["schema"] = SCHEMA_VERSION
    run.setdefault("evaluation", {"required": False, "label": None})
    for task in run["tasks"].values():
        task.setdefault("owner_role", "owner")
        task.setdefault("route_back_to", task["owner_role"])
        count = 1 if task.get("status") in {"in_progress", "complete"} else 0
        task.setdefault("attempt_count", count)
        task.setdefault("active_attempt_id", f"a{count:02d}" if count else None)
        task.setdefault("attempts", ([{"attempt_id": "a01", "started_at": task.get("updated_at", run.get("created_at")), "ended_at": task.get("updated_at") if task.get("status") == "complete" else None}] if count else []))
        task.setdefault("events", [{"event_id": "e0001", "state": task.get("status", "pending"), "at": task.get("updated_at", run.get("created_at"))}])
        task.setdefault("last_failure", task.get("reason") if task.get("status") == "blocked" else None)
        task.setdefault("last_transition_at", task.get("updated_at", run.get("created_at")))
    return run


def current_run_id(root: Path) -> str:
    current = load_json(root / CURRENT_RELATIVE, "current run pointer")
    run_id = current.get("run_id")
    if not isinstance(run_id, str):
        raise LedgerError("Invalid current run pointer.")
    return validate_id(run_id, "run")


def run_path(root: Path, run_id: str) -> Path:
    return root / RUNS_RELATIVE / f"{validate_id(run_id, 'run')}.json"


def git_bytes(root: Path, arguments: list[str], *, allow_failure: bool = False) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False, timeout=30)
    if result.returncode and not allow_failure:
        raise LedgerError("Cannot fingerprint the current Git candidate.")
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
            subhead = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, check=False, timeout=10).stdout.strip()
            digest.update(b"DIR\0" + subhead + b"\0")
        else:
            digest.update(b"MISSING\0")
    return digest.hexdigest()


def validate_run(run: dict[str, Any]) -> None:
    if run.get("schema") != SCHEMA_VERSION:
        raise LedgerError("Unsupported ledger schema.")
    validate_id(str(run.get("run_id", "")), "run")
    if run.get("status") not in {"active", "complete"}:
        raise LedgerError("Invalid run status.")
    tasks = run.get("tasks")
    if not isinstance(tasks, dict):
        raise LedgerError("Invalid ledger tasks collection.")
    for task_id, task in tasks.items():
        validate_id(task_id, "task")
        if not isinstance(task, dict) or task.get("status") not in TASK_STATES:
            raise LedgerError(f"Invalid task entry: {task_id}")
        if not isinstance(task.get("required"), bool):
            raise LedgerError(f"Task {task_id} has an invalid required flag.")
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item in tasks for item in dependencies
        ):
            raise LedgerError(f"Task {task_id} has invalid dependencies.")
        if task_id in dependencies:
            raise LedgerError(f"Task {task_id} cannot depend on itself.")
        if not isinstance(task.get("owner_role"), str) or not task["owner_role"]:
            raise LedgerError(f"Task {task_id} has an invalid owner role.")
        if not isinstance(task.get("attempts"), list) or task.get("attempt_count") != len(task["attempts"]):
            raise LedgerError(f"Task {task_id} has invalid attempt history.")
        events = task.get("events")
        if not isinstance(events, list) or len({event.get("event_id") for event in events if isinstance(event, dict)}) != len(events):
            raise LedgerError(f"Task {task_id} has invalid event history.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise LedgerError("Task dependency cycle detected.")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def load_run(root: Path, run_id: str | None = None) -> tuple[Path, dict[str, Any]]:
    selected = validate_id(run_id, "run") if run_id else current_run_id(root)
    path = run_path(root, selected)
    run = migrate_run(load_json(path, "run ledger"))
    validate_run(run)
    return path, run


def save_run(path: Path, run: dict[str, Any]) -> None:
    validate_run(run)
    run["updated_at"] = utc_now()
    atomic_write_json(path, run)


def blockers(run: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for task_id, task in run["tasks"].items():
        if not task["required"]:
            continue
        status = task["status"]
        if status in {"pending", "in_progress", "blocked"}:
            results.append(f"{task_id}: {status}")
        elif status == "skipped" and not task.get("reason"):
            results.append(f"{task_id}: skipped without justification")
        elif status in {"interrupted", "waiting_user", "needs_repair"}:
            results.append(f"{task_id}: {status}")
    evaluation = run.get("evaluation", {})
    if evaluation.get("required"):
        label = evaluation.get("label")
        results.append(f"local evaluation {label}: not checked")
    return results


def evaluation_blocker(root: Path, run: dict[str, Any]) -> str | None:
    evaluation = run.get("evaluation", {})
    if not evaluation.get("required"):
        return None
    label = evaluation.get("label")
    summary_path = root / RUNTIME_RELATIVE / "evals" / f"{label}.json"
    try:
        summary = load_json(summary_path, "local evaluation summary")
    except LedgerError:
        return f"local evaluation {label}: not-configured"
    if summary.get("label") != label or summary.get("outcome") not in {"pass", "fail", "timeout", "candidate_changed"}:
        return f"local evaluation {label}: invalid summary"
    if summary["outcome"] != "pass":
        return f"local evaluation {label}: {summary['outcome']}"
    if summary.get("candidate_fingerprint") != candidate_fingerprint(root):
        return f"local evaluation {label}: stale candidate"
    return None


def command_start(root: Path, args: argparse.Namespace) -> None:
    runs = ensure_runtime(root)
    run_id = validate_id(args.run_id, "run")
    title = validate_text(args.title, "Run title", MAX_LABEL_LENGTH)
    path = runs / f"{run_id}.json"
    if path.exists():
        raise LedgerError(f"Run already exists: {run_id}")
    now = utc_now()
    run = {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "title": title,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "tasks": {},
        "evaluation": {"required": False, "label": None},
    }
    atomic_write_json(path, run)
    atomic_write_json(root / CURRENT_RELATIVE, {"schema": SCHEMA_VERSION, "run_id": run_id})
    print(f"Started run {run_id}: {title}")


def command_add(root: Path, args: argparse.Namespace) -> None:
    path, run = load_run(root, args.run)
    if run["status"] != "active":
        raise LedgerError("Cannot add tasks to a completed run.")
    task_id = validate_id(args.task_id, "task")
    if task_id in run["tasks"]:
        raise LedgerError(f"Task already exists: {task_id}")
    dependencies = list(dict.fromkeys(args.depends_on or []))
    for dependency in dependencies:
        validate_id(dependency, "dependency")
        if dependency not in run["tasks"]:
            raise LedgerError(f"Dependency does not exist: {dependency}")
    now = utc_now()
    run["tasks"][task_id] = {
        "title": validate_text(args.title, "Task title", MAX_LABEL_LENGTH),
        "required": not args.optional,
        "status": "pending",
        "depends_on": dependencies,
        "reason": None,
        "created_at": now,
        "updated_at": now,
        "owner_role": validate_text(args.owner_role, "Owner role", 48),
        "route_back_to": validate_text(args.owner_role, "Owner role", 48),
        "attempt_count": 0,
        "active_attempt_id": None,
        "attempts": [],
        "events": [{"event_id": "e0001", "state": "pending", "at": now}],
        "last_failure": None,
        "last_transition_at": now,
    }
    save_run(path, run)
    print(f"Added task {task_id} ({'optional' if args.optional else 'required'}).")


def transition(root: Path, args: argparse.Namespace, target: str) -> None:
    path, run = load_run(root, args.run)
    if run["status"] != "active":
        raise LedgerError("Cannot change tasks in a completed run.")
    task_id = validate_id(args.task_id, "task")
    task = run["tasks"].get(task_id)
    if task is None:
        raise LedgerError(f"Task does not exist: {task_id}")
    allowed = {
        "in_progress": {"pending", "blocked"},
        "complete": {"in_progress"},
        "blocked": {"pending", "in_progress"},
        "skipped": {"pending", "blocked"},
        "interrupted": {"in_progress"},
        "waiting_user": {"pending", "in_progress", "blocked"},
        "needs_repair": {"complete", "in_progress"},
    }
    if task["status"] not in allowed[target]:
        raise LedgerError(
            f"Cannot move {task_id} from {task['status']} to {target}."
        )
    if target == "in_progress":
        unresolved = [
            dependency
            for dependency in task["depends_on"]
            if run["tasks"][dependency]["status"] not in {"complete", "skipped"}
            or (
                run["tasks"][dependency]["status"] == "skipped"
                and not run["tasks"][dependency].get("reason")
            )
        ]
        if unresolved:
            raise LedgerError(
                f"Task {task_id} has unresolved dependencies: {', '.join(unresolved)}"
            )
    reason = getattr(args, "reason", None)
    if target in {"blocked", "interrupted", "waiting_user", "needs_repair"}:
        reason = validate_text(reason, "Transition evidence", MAX_REASON_LENGTH)
    elif target == "skipped" and reason is not None:
        reason = validate_text(reason, "Skip reason", MAX_REASON_LENGTH, allow_empty=True)
    else:
        reason = None
    task["status"] = target
    task["reason"] = reason or None
    task["updated_at"] = task["last_transition_at"] = utc_now()
    if target == "in_progress" and task["active_attempt_id"] is None:
        task["attempt_count"] += 1
        attempt_id = f"a{task['attempt_count']:02d}"
        task["active_attempt_id"] = attempt_id
        task["attempts"].append({"attempt_id": attempt_id, "started_at": task["updated_at"], "ended_at": None})
    if target in {"complete", "interrupted", "needs_repair"} and task["active_attempt_id"]:
        task["attempts"][-1]["ended_at"] = task["updated_at"]
        task["active_attempt_id"] = None
    if target in {"blocked", "interrupted", "needs_repair"}:
        task["last_failure"] = reason
    if target == "needs_repair":
        task["route_back_to"] = task["owner_role"]
    append_event(task, target, reason)
    save_run(path, run)
    print(f"Task {task_id}: {target}")


def status_payload(run: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    pending_blockers = blockers(run)
    if root is not None:
        pending_blockers = [item for item in pending_blockers if not item.startswith("local evaluation ")]
        eval_problem = evaluation_blocker(root, run)
        if eval_problem:
            pending_blockers.append(eval_problem)
    counts = {state: 0 for state in sorted(TASK_STATES)}
    for task in run["tasks"].values():
        counts[task["status"]] += 1
    states = {task["status"] for task in run["tasks"].values()}
    derived = "complete" if run["status"] == "complete" else (
        "waiting_for_user" if "waiting_user" in states else
        "repair_needed" if "needs_repair" in states else
        "interrupted" if "interrupted" in states else
        "blocked" if "blocked" in states else
        "active" if "in_progress" in states else
        "ready_for_review" if not pending_blockers else "planned")
    return {
        "run": run,
        "counts": counts,
        "ready_for_review": run["status"] == "active" and not pending_blockers,
        "completion_blockers": pending_blockers,
        "derived_status": derived,
    }


def command_status(root: Path, args: argparse.Namespace) -> None:
    _, run = load_run(root, args.run)
    payload = status_payload(run, root)
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
        return
    print(f"Run {run['run_id']} [{run['status']}]: {run['title']}")
    print(f"Workflow status: {payload['derived_status']}")
    if not run["tasks"]:
        print("  No declared tasks.")
    for task_id, task in run["tasks"].items():
        required = "required" if task["required"] else "optional"
        dependencies = ", ".join(task["depends_on"]) or "none"
        line = f"  {task_id}: {task['status']} ({required}; depends on: {dependencies}) - {task['title']}"
        if task.get("reason"):
            line += f" [{task['reason']}]"
        print(line)
    if payload["completion_blockers"]:
        print("Ready for review: no")
        for item in payload["completion_blockers"]:
            print(f"  blocker: {item}")
    else:
        print("Ready for review: yes")


def command_ready(root: Path, args: argparse.Namespace) -> None:
    _, run = load_run(root, args.run)
    if run["status"] != "active":
        raise LedgerError("Run is already complete.")
    pending_blockers = status_payload(run, root)["completion_blockers"]
    if pending_blockers:
        raise LedgerError("Not ready for review: " + "; ".join(pending_blockers))
    print(f"Run {run['run_id']} is ready for review based on declared required tasks.")


def command_complete_run(root: Path, args: argparse.Namespace) -> None:
    path, run = load_run(root, args.run)
    if run["status"] != "active":
        raise LedgerError("Run is already complete.")
    pending_blockers = status_payload(run, root)["completion_blockers"]
    if pending_blockers:
        raise LedgerError("Cannot complete run: " + "; ".join(pending_blockers))
    run["status"] = "complete"
    run["completed_at"] = utc_now()
    save_run(path, run)
    print(f"Completed run {run['run_id']} based on declared required tasks.")


def command_retry(root: Path, args: argparse.Namespace) -> None:
    path, run = load_run(root, args.run)
    task = run["tasks"].get(validate_id(args.task_id, "task"))
    if task is None:
        raise LedgerError("Task does not exist.")
    if task["status"] not in {"interrupted", "needs_repair"}:
        raise LedgerError(f"Cannot retry {args.task_id} from {task['status']}.")
    if task["attempt_count"] >= MAX_ATTEMPTS:
        raise LedgerError(f"Retry limit reached for {args.task_id}.")
    evidence = validate_text(args.evidence, "Retry evidence", MAX_REASON_LENGTH)
    task["status"] = "pending"
    task["reason"] = evidence
    task["last_transition_at"] = task["updated_at"] = utc_now()
    append_event(task, "retry_scheduled", evidence)
    save_run(path, run)
    print(f"Task {args.task_id}: retry scheduled; route back to {task['route_back_to']}")


def command_resume(root: Path, args: argparse.Namespace) -> None:
    path, run = load_run(root, args.run)
    evidence = validate_text(args.evidence, "Resume evidence", MAX_REASON_LENGTH)
    task_id = validate_id(args.task_id, "task")
    task = run["tasks"].get(task_id)
    if task is None:
        raise LedgerError(f"Task does not exist: {task_id}")
    if task["status"] != "waiting_user":
        raise LedgerError(f"Cannot resume {task_id} from {task['status']}.")
    if task["active_attempt_id"]:
        unresolved = [dependency for dependency in task["depends_on"]
                      if run["tasks"][dependency]["status"] not in {"complete", "skipped"}
                      or (run["tasks"][dependency]["status"] == "skipped" and not run["tasks"][dependency].get("reason"))]
        if unresolved:
            raise LedgerError(f"Task {task_id} has unresolved dependencies: {', '.join(unresolved)}")
        task["status"] = "in_progress"
    else:
        task["status"] = "pending"
    task["reason"] = None
    task["updated_at"] = task["last_transition_at"] = utc_now()
    append_event(task, "resumed", evidence)
    save_run(path, run)
    print(f"Task {task_id}: {task['status']}")


def command_require_eval(root: Path, args: argparse.Namespace) -> None:
    path, run = load_run(root, args.run)
    label = validate_id(args.label, "evaluation label")
    run["evaluation"] = {"required": True, "label": label}
    save_run(path, run)
    print(f"Local evaluation required before review: {label}")


def command_clear(root: Path, args: argparse.Namespace) -> None:
    ensure_runtime(root)
    selected = validate_id(args.run, "run") if args.run else current_run_id(root)
    path = run_path(root, selected)
    if not path.exists():
        raise LedgerError(f"Run does not exist: {selected}")
    path.unlink()
    current_path = root / CURRENT_RELATIVE
    if current_path.exists():
        current = load_json(current_path, "current run pointer")
        if current.get("run_id") == selected:
            current_path.unlink()
    print(f"Cleared local ledger metadata for run {selected}.")


def add_run_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", help="Run ID. Defaults to the current run.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Git repository root. Defaults to discovery from the current directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start and select a new run.")
    start.add_argument("run_id")
    start.add_argument("--title", required=True)
    start.set_defaults(handler=command_start)

    add = subparsers.add_parser("add", help="Declare a task in the selected run.")
    add.add_argument("task_id")
    add.add_argument("--title", required=True)
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument("--optional", action="store_true")
    add.add_argument("--owner-role", default="owner")
    add_run_option(add)
    add.set_defaults(handler=command_add)

    for name, target, help_text in (
        ("begin", "in_progress", "Begin a pending task or resume a blocked task."),
        ("complete", "complete", "Complete an in-progress task."),
        ("block", "blocked", "Block a pending or in-progress task."),
        ("skip", "skipped", "Skip a pending or blocked task."),
        ("interrupt", "interrupted", "Record an interrupted active task."),
        ("wait-user", "waiting_user", "Record a task waiting for the user."),
        ("needs-repair", "needs_repair", "Route failed verification back to its owner."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("task_id")
        add_run_option(command)
        if name in {"block", "interrupt", "wait-user", "needs-repair"}:
            command.add_argument("--reason", required=True)
        elif name == "skip":
            command.add_argument("--reason")
        command.set_defaults(
            handler=lambda root, args, selected=target: transition(root, args, selected)
        )

    status = subparsers.add_parser("status", help="Show human or JSON status.")
    add_run_option(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)

    ready = subparsers.add_parser(
        "ready-for-review", help="Check declared required work before review."
    )
    add_run_option(ready)
    ready.set_defaults(handler=command_ready)

    complete_run = subparsers.add_parser(
        "complete-run", help="Mark a ready run complete."
    )
    add_run_option(complete_run)
    complete_run.set_defaults(handler=command_complete_run)

    retry = subparsers.add_parser("retry", help="Schedule the single bounded retry with new evidence.")
    retry.add_argument("task_id")
    retry.add_argument("--evidence", required=True)
    add_run_option(retry)
    retry.set_defaults(handler=command_retry)

    resume = subparsers.add_parser("resume", help="Resume a task after user input without losing its active attempt.")
    resume.add_argument("task_id")
    resume.add_argument("--evidence", required=True)
    add_run_option(resume)
    resume.set_defaults(handler=command_resume)

    require_eval = subparsers.add_parser("require-eval", help="Require a named local evaluation summary before review.")
    require_eval.add_argument("--label", required=True)
    add_run_option(require_eval)
    require_eval.set_defaults(handler=command_require_eval)

    clear = subparsers.add_parser("clear", help="Delete one run's local metadata.")
    add_run_option(clear)
    clear.set_defaults(handler=command_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("ledger error: Python 3.11 or newer is required.", file=sys.stderr)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = args.root.expanduser().resolve() if args.root else discover_root(Path.cwd())
        args.handler(root, args)
    except (LedgerError, OSError) as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
