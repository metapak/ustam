---
name: secure-change
description: Applies a focused security review checklist when the user or owner explicitly requests security expertise.
disable-model-invocation: true
---

# Security expertise

Use this skill only when explicitly invoked for the current task.

1. Have the assigned specialist identify assets, trust boundaries, actors, inputs, outputs, and durable side effects in scope. The main session coordinates from its short evidence report.
2. Check authorization at the action boundary, not only in the interface.
3. Validate untrusted input, escape output for its destination, and avoid exposing secrets or personal data.
4. Prefer least privilege, safe defaults, explicit failure, and auditable behavior.
5. Have the assigned specialist test the highest-risk abuse case and return short evidence without secrets or raw logs.
6. Stop for owner direction before changing authentication, authorization, encryption, permissions, billing, or data retention.

This skill supplies guidance only. It does not grant tools, file access, external access, or permission to edit.
