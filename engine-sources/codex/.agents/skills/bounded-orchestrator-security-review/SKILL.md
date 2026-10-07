---
name: bounded-orchestrator-security-review
description: Optional security review expertise pack for Ustam. Use only when the user explicitly selects this pack or asks the orchestrator for security-focused analysis.
---

# Security review expertise pack

Apply this pack alongside `bounded-orchestrator`; it does not replace the base workflow.

## Authority and selection

- This pack is opt-in. Do not activate it merely because a task handles data or authentication.
- It adds a review lens only. It grants no agent, tool, file, credential, permission, exploit, scan, or external-action authority.
- The root owner still decides scope and architecture. One writer per file or scope, independent verification, candidate freezing, finite review, and exact external authorization remain mandatory.
- Never access production data, probe external systems, rotate credentials, change permissions, or disclose a suspected vulnerability without explicit authority.

## Review brief

Define the assets, trust boundaries, entry points, actors, data sensitivity, and concrete abuse cases relevant to the requested change. Inspect existing authentication, authorization, validation, secret handling, logging, dependency, and error-handling patterns before proposing changes.

Prioritize evidence-backed issues with a plausible path and material impact. Separate confirmed findings from hypotheses. Do not inflate severity or claim that a review proves the absence of vulnerabilities.

## Required checks

Check only the areas that apply:

- authentication and session assumptions
- authorization at every protected operation
- input validation, output encoding, injection, and unsafe deserialization
- secrets, personal data, logs, cache, temporary files, and error messages
- network boundaries, redirects, file paths, command execution, and dependency changes
- race conditions, replay, idempotency, rate limits, and denial-of-service exposure

The explorer maps the relevant boundary read-only. A single implementer owns any authorized repair. The verifier reproduces and classifies evidence without editing production code. The reviewer returns findings against the frozen candidate.

## Deliverable

For each finding, state the affected boundary, trigger, impact, evidence, confidence, and smallest defensible remediation. State what was not tested. Keep sensitive reproduction details out of public artifacts and follow the repository security policy.
