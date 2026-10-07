
# Delegated task contract

Use one contract per spawned task.

```text
Task ID:
Role and intended model:
Objective:

Scope:
- Files, symbols, subsystem, environment, or question:

Ownership:
- Writable paths, or read-only:

Context:
- Minimum evidence needed to start (fresh context where supported; omit conversation history):

Invariants:
- Behavior and files that must not change:

Deliverable:
- Report, implementation, test evidence, or runtime evidence:

Acceptance criteria:
1.
2.

Declared ledger task IDs, when the local ledger is used:
- Short IDs only; never include prompts, source, logs, personal data, credentials, or secrets:

Stop conditions:
- Ambiguous requirement with materially different outcomes
- Required write outside ownership
- New dependency, migration, or public contract change
- Security, privacy, billing, or destructive decision
- Two failed attempts without new evidence
```

## Contract quality check

A good contract can complete without the child choosing project architecture. It names model intent, scope, ownership, proof, and stopping conditions.

Failure must still be useful: the child returns a precise blocker and evidence instead of expanding into the repository.
