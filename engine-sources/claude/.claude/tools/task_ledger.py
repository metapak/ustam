#!/usr/bin/env python3
"""Maintain a small, private metadata-only task ledger for bounded orchestration."""

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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2
STATE_RELATIVE = Path(".claude/.bounded-orchestrator/tasks.json")
LOCK_SUFFIX = ".lock"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_SUMMARY = 160
MAX_EVIDENCE = 240
MAX_ROLE = 48
STATUSES = {"pending", "in_progress", "blocked", "complete", "interrupted", "waiting_user", "needs_repair"}
MAX_ATTEMPTS = 2
EVAL_RUNTIME = Path(".claude/.bounded-orchestrator/evals")


class LedgerError(RuntimeError):
    """Expected command failure."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: str, label: str, limit: int) -> str:
    value = " ".join(value.split())
    if not value:
        raise LedgerError(f"{label} must not be empty")
    if len(value) > limit:
        raise LedgerError(f"{label} must be at most {limit} characters")
    return value


def state_path(repo: Path) -> Path:
    root = repo.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise LedgerError(f"repository directory does not exist: {root}")
    return root / STATE_RELATIVE


def git_bytes(root: Path, arguments: list[str], *, allow_failure: bool = False) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False, timeout=30)
    if result.returncode and not allow_failure:
        raise LedgerError("cannot fingerprint the current Git candidate")
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
        file_path = root / relative
        digest.update(b"PATH\0" + raw + b"\0")
        if file_path.is_symlink():
            digest.update(b"SYMLINK\0" + os.fsencode(os.readlink(file_path)) + b"\0")
        elif file_path.is_file():
            digest.update(b"FILE\0")
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        elif file_path.is_dir():
            subhead = subprocess.run(["git", "-C", str(file_path), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, check=False, timeout=10).stdout.strip()
            digest.update(b"DIR\0" + subhead + b"\0")
        else:
            digest.update(b"MISSING\0")
    return digest.hexdigest()


@contextmanager
def lock(path: Path, timeout: float = 5.0) -> Iterator[None]:
    lock_path = path.with_name(path.name + LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise LedgerError(f"ledger is busy: {lock_path}") from None
            time.sleep(0.05)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def event(task: dict, state: str, evidence: str = "") -> None:
    events = task.setdefault("events", [])
    item = {"event_id": f"e{len(events) + 1:04d}", "state": state, "at": now()}
    if evidence:
        item["evidence"] = evidence
    events.append(item)


def migrate(data: dict) -> dict:
    if data.get("schema") == SCHEMA_VERSION:
        return data
    if data.get("schema") != 1 or not isinstance(data.get("tasks"), list):
        raise LedgerError("unsupported or malformed task ledger")
    data["schema"] = SCHEMA_VERSION
    data.setdefault("run_id", "default")
    data.setdefault("evaluation", {"required": False, "label": None})
    for task in data["tasks"]:
        task.setdefault("owner_role", task.get("role", "owner"))
        task.setdefault("route_back_to", task["owner_role"])
        count = 1 if task.get("status") in {"in_progress", "complete"} else 0
        task.setdefault("attempt_count", count)
        task.setdefault("active_attempt_id", f"a{count:02d}" if count else None)
        task.setdefault("attempts", ([{"attempt_id": "a01", "started_at": task.get("updated_at"), "ended_at": task.get("updated_at") if task.get("status") == "complete" else None}] if count else []))
        task.setdefault("events", [{"event_id": "e0001", "state": task.get("status", "pending"), "at": task.get("updated_at")}])
        task.setdefault("last_failure", task.get("evidence") if task.get("status") == "blocked" else "")
        task.setdefault("last_transition_at", task.get("updated_at"))
    return data


def validate(data: dict) -> None:
    if data.get("schema") != SCHEMA_VERSION or not isinstance(data.get("tasks"), list):
        raise LedgerError("unsupported or malformed task ledger")
    ids: set[str] = set()
    for task in data["tasks"]:
        if not isinstance(task, dict):
            raise LedgerError("malformed task entry")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not ID_PATTERN.fullmatch(task_id):
            raise LedgerError("malformed task id")
        if task_id in ids:
            raise LedgerError(f"duplicate task id: {task_id}")
        ids.add(task_id)
        if task.get("status") not in STATUSES:
            raise LedgerError(f"invalid status for {task_id}")
        if not isinstance(task.get("depends_on"), list) or not all(
            isinstance(item, str) for item in task["depends_on"]
        ):
            raise LedgerError(f"invalid dependencies for {task_id}")
        if not isinstance(task.get("owner_role"), str) or not task["owner_role"]:
            raise LedgerError(f"invalid owner role for {task_id}")
        if not isinstance(task.get("attempts"), list) or len(task["attempts"]) != task.get("attempt_count"):
            raise LedgerError(f"invalid attempt history for {task_id}")
        events = task.get("events")
        if not isinstance(events, list) or len({item.get("event_id") for item in events if isinstance(item, dict)}) != len(events):
            raise LedgerError(f"invalid event history for {task_id}")
    for task in data["tasks"]:
        missing = set(task["depends_on"]) - ids
        if missing:
            raise LedgerError(f"{task['id']} has missing dependencies: {', '.join(sorted(missing))}")


def load(path: Path) -> dict:
    if not path.exists():
        raise LedgerError("ledger is not initialized; run `init` first")
    if path.is_symlink():
        raise LedgerError("refusing to read a symlinked ledger")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f"cannot read task ledger: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError("malformed task ledger")
    data = migrate(data)
    validate(data)
    return data


def save(path: Path, data: dict) -> None:
    data["updated_at"] = now()
    validate(data)
    atomic_write(path, data)


def find_task(data: dict, task_id: str) -> dict:
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    raise LedgerError(f"unknown task: {task_id}")


def command_init(path: Path, force: bool) -> None:
    with lock(path):
        if path.exists() and not force:
            load(path)
            print(f"Ledger already initialized: {path}")
            return
        timestamp = now()
        atomic_write(path, {"schema": SCHEMA_VERSION, "run_id": "default", "created_at": timestamp, "updated_at": timestamp, "evaluation": {"required": False, "label": None}, "tasks": []})
    print(f"Initialized private task ledger: {path}")


def command_add(path: Path, task_id: str, summary: str, role: str, dependencies: list[str], owner_role: str | None = None) -> None:
    if not ID_PATTERN.fullmatch(task_id):
        raise LedgerError("task id must be 1-32 letters, numbers, underscores, or hyphens")
    summary = clean_text(summary, "summary", MAX_SUMMARY)
    role = clean_text(role, "role", MAX_ROLE)
    dependencies = list(dict.fromkeys(dependencies))
    if task_id in dependencies:
        raise LedgerError("a task cannot depend on itself")
    with lock(path):
        data = load(path)
        if any(item["id"] == task_id for item in data["tasks"]):
            raise LedgerError(f"task already exists: {task_id}")
        existing = {item["id"] for item in data["tasks"]}
        missing = set(dependencies) - existing
        if missing:
            raise LedgerError(f"unknown dependencies: {', '.join(sorted(missing))}")
        timestamp = now()
        owner = clean_text(owner_role or role, "owner role", MAX_ROLE)
        data["tasks"].append({
            "id": task_id,
            "summary": summary,
            "role": role,
            "status": "pending",
            "depends_on": dependencies,
            "evidence": "",
            "created_at": timestamp,
            "updated_at": timestamp,
            "owner_role": owner,
            "route_back_to": owner,
            "attempt_count": 0,
            "active_attempt_id": None,
            "attempts": [],
            "events": [{"event_id": "e0001", "state": "pending", "at": timestamp}],
            "last_failure": "",
            "last_transition_at": timestamp,
        })
        save(path, data)
    print(f"Added {task_id}")


def command_start(path: Path, task_id: str) -> None:
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        if task["status"] not in {"pending", "blocked"}:
            raise LedgerError(f"cannot start {task_id} from {task['status']}")
        incomplete = [dep for dep in task["depends_on"] if find_task(data, dep)["status"] != "complete"]
        if incomplete:
            raise LedgerError(f"cannot start {task_id}; incomplete dependencies: {', '.join(incomplete)}")
        task["status"] = "in_progress"
        task["evidence"] = ""
        task["updated_at"] = task["last_transition_at"] = now()
        if task["active_attempt_id"] is None:
            task["attempt_count"] += 1
            attempt_id = f"a{task['attempt_count']:02d}"
            task["active_attempt_id"] = attempt_id
            task["attempts"].append({"attempt_id": attempt_id, "started_at": task["updated_at"], "ended_at": None})
        event(task, "in_progress")
        save(path, data)
    print(f"Started {task_id}")


def command_complete(path: Path, task_id: str, evidence: str) -> None:
    evidence = clean_text(evidence, "evidence", MAX_EVIDENCE)
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        if task["status"] != "in_progress":
            raise LedgerError(f"cannot complete {task_id} from {task['status']}")
        incomplete = [dep for dep in task["depends_on"] if find_task(data, dep)["status"] != "complete"]
        if incomplete:
            raise LedgerError(f"cannot complete {task_id}; incomplete dependencies: {', '.join(incomplete)}")
        task["status"] = "complete"
        task["evidence"] = evidence
        task["updated_at"] = task["last_transition_at"] = now()
        if task["active_attempt_id"]:
            task["attempts"][-1]["ended_at"] = task["updated_at"]
            task["active_attempt_id"] = None
        event(task, "complete", evidence)
        save(path, data)
    print(f"Completed {task_id}")


def command_block(path: Path, task_id: str, evidence: str) -> None:
    evidence = clean_text(evidence, "evidence", MAX_EVIDENCE)
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        if task["status"] not in {"pending", "in_progress"}:
            raise LedgerError(f"cannot block {task_id} from {task['status']}")
        task["status"] = "blocked"
        task["evidence"] = evidence
        task["last_failure"] = evidence
        task["updated_at"] = task["last_transition_at"] = now()
        event(task, "blocked", evidence)
        save(path, data)
    print(f"Blocked {task_id}")


def command_show(path: Path, as_json: bool) -> None:
    data = load(path)
    states = {task["status"] for task in data["tasks"]}
    derived = "waiting_for_user" if "waiting_user" in states else "repair_needed" if "needs_repair" in states else "interrupted" if "interrupted" in states else "blocked" if "blocked" in states else "active" if "in_progress" in states else "ready_for_review" if all(task["status"] == "complete" for task in data["tasks"]) else "planned"
    if as_json:
        payload = dict(data)
        payload["derived_status"] = derived
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not data["tasks"]:
        print("No tasks")
        return
    print(f"Workflow status: {derived}")
    for task in data["tasks"]:
        deps = ",".join(task["depends_on"]) or "-"
        print(f"{task['id']:<16} {task['status']:<12} role={task['role']} deps={deps}  {task['summary']}")


def evaluation_problem(path: Path, data: dict) -> str | None:
    selected = data.get("evaluation", {})
    if not selected.get("required"):
        return None
    label = selected.get("label")
    summary_path = path.parent / "evals" / f"{label}.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"local evaluation {label}=not-configured"
    if summary.get("label") != label or summary.get("outcome") != "pass":
        return f"local evaluation {label}={summary.get('outcome', 'invalid')}"
    if summary.get("candidate_fingerprint") != candidate_fingerprint(path.parents[2]):
        return f"local evaluation {label}=stale candidate"
    return None


def command_check(path: Path) -> None:
    data = load(path)
    unfinished = [task for task in data["tasks"] if task["status"] != "complete"]
    if unfinished:
        details = ", ".join(f"{task['id']}={task['status']}" for task in unfinished)
        raise LedgerError(f"completion gate failed: {details}")
    problem = evaluation_problem(path, data)
    if problem:
        raise LedgerError(f"completion gate failed: {problem}")
    print(f"Completion gate passed: {len(data['tasks'])} task(s) complete")


def command_special(path: Path, task_id: str, status: str, evidence: str) -> None:
    evidence = clean_text(evidence, "evidence", MAX_EVIDENCE)
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        allowed = {"interrupted": {"in_progress"}, "waiting_user": {"pending", "in_progress", "blocked"}, "needs_repair": {"complete", "in_progress"}}
        if task["status"] not in allowed[status]:
            raise LedgerError(f"cannot move {task_id} from {task['status']} to {status}")
        task["status"] = status
        task["evidence"] = evidence
        task["last_failure"] = evidence
        task["updated_at"] = task["last_transition_at"] = now()
        if status in {"interrupted", "needs_repair"} and task["active_attempt_id"]:
            task["attempts"][-1]["ended_at"] = task["updated_at"]
            task["active_attempt_id"] = None
        if status == "needs_repair":
            task["route_back_to"] = task["owner_role"]
        event(task, status, evidence)
        save(path, data)
    print(f"{task_id}: {status}; route back to {task['route_back_to']}")


def command_retry(path: Path, task_id: str, evidence: str) -> None:
    evidence = clean_text(evidence, "retry evidence", MAX_EVIDENCE)
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        if task["status"] not in {"interrupted", "needs_repair"}:
            raise LedgerError(f"cannot retry {task_id} from {task['status']}")
        if task["attempt_count"] >= MAX_ATTEMPTS:
            raise LedgerError(f"retry limit reached for {task_id}")
        task["status"] = "pending"
        task["evidence"] = evidence
        task["updated_at"] = task["last_transition_at"] = now()
        event(task, "retry_scheduled", evidence)
        save(path, data)
    print(f"{task_id}: retry scheduled; route back to {task['route_back_to']}")


def command_resume(path: Path, task_id: str, evidence: str) -> None:
    evidence = clean_text(evidence, "resume evidence", MAX_EVIDENCE)
    with lock(path):
        data = load(path)
        task = find_task(data, task_id)
        if task["status"] != "waiting_user":
            raise LedgerError(f"cannot resume {task_id} from {task['status']}")
        if task["active_attempt_id"]:
            incomplete = [dep for dep in task["depends_on"] if find_task(data, dep)["status"] != "complete"]
            if incomplete:
                raise LedgerError(f"cannot resume {task_id}; incomplete dependencies: {', '.join(incomplete)}")
            task["status"] = "in_progress"
        else:
            task["status"] = "pending"
        task["evidence"] = evidence
        task["updated_at"] = task["last_transition_at"] = now()
        event(task, "resumed", evidence)
        save(path, data)
    print(f"{task_id}: {task['status']}")


def command_require_eval(path: Path, label: str) -> None:
    if not ID_PATTERN.fullmatch(label):
        raise LedgerError("evaluation label must be a short safe identifier")
    with lock(path):
        data = load(path)
        data["evaluation"] = {"required": True, "label": label}
        save(path, data)
    print(f"Local evaluation required before review: {label}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=Path.cwd(), help="target repository (default: current directory)")
    subparsers = result.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="create an empty ledger")
    init.add_argument("--force", action="store_true", help="replace an existing ledger")
    add = subparsers.add_parser("add", help="add a pending task")
    add.add_argument("task_id")
    add.add_argument("--summary", required=True)
    add.add_argument("--role", required=True)
    add.add_argument("--owner-role")
    add.add_argument("--depends-on", action="append", default=[], metavar="TASK_ID")
    start = subparsers.add_parser("start", help="start a ready task")
    start.add_argument("task_id")
    complete = subparsers.add_parser("complete", help="complete an in-progress task")
    complete.add_argument("task_id")
    complete.add_argument("--evidence", required=True)
    blocked = subparsers.add_parser("block", help="mark a task blocked")
    blocked.add_argument("task_id")
    blocked.add_argument("--evidence", required=True)
    for name in ("interrupt", "wait-user", "needs-repair"):
        special = subparsers.add_parser(name)
        special.add_argument("task_id")
        special.add_argument("--evidence", required=True)
    retry = subparsers.add_parser("retry")
    retry.add_argument("task_id")
    retry.add_argument("--evidence", required=True)
    resume = subparsers.add_parser("resume")
    resume.add_argument("task_id")
    resume.add_argument("--evidence", required=True)
    require_eval = subparsers.add_parser("require-eval")
    require_eval.add_argument("--label", required=True)
    show = subparsers.add_parser("show", help="show ledger state")
    show.add_argument("--json", action="store_true")
    subparsers.add_parser("check", help="require all tasks to be complete")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        path = state_path(args.repo)
        if args.command == "init":
            command_init(path, args.force)
        elif args.command == "add":
            command_add(path, args.task_id, args.summary, args.role, args.depends_on, args.owner_role)
        elif args.command == "start":
            command_start(path, args.task_id)
        elif args.command == "complete":
            command_complete(path, args.task_id, args.evidence)
        elif args.command == "block":
            command_block(path, args.task_id, args.evidence)
        elif args.command in {"interrupt", "wait-user", "needs-repair"}:
            command_special(path, args.task_id, {"interrupt": "interrupted", "wait-user": "waiting_user", "needs-repair": "needs_repair"}[args.command], args.evidence)
        elif args.command == "retry":
            command_retry(path, args.task_id, args.evidence)
        elif args.command == "resume":
            command_resume(path, args.task_id, args.evidence)
        elif args.command == "require-eval":
            command_require_eval(path, args.label)
        elif args.command == "show":
            command_show(path, args.json)
        elif args.command == "check":
            command_check(path)
        else:
            raise LedgerError(f"unsupported command: {args.command}")
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
