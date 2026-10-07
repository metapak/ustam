---
name: bounded-orchestrator-ui-design
description: Optional UI design expertise pack for Ustam. Use only when the user explicitly selects this pack or asks the orchestrator for UI or UX design expertise.
---

# UI design expertise pack

Apply this pack alongside `bounded-orchestrator`; it does not replace the base workflow.

## Authority and selection

- This pack is opt-in. Do not activate it merely because a task touches frontend code.
- It adds design guidance only. It grants no agent, tool, file, permission, or external-action authority.
- The root owner still decides scope and architecture. One writer per file or scope, independent verification, candidate freezing, finite review, and exact external authorization remain mandatory.
- Follow the existing product design system and repository conventions before adding a new visual language.

## Design brief

Before implementation, record a short brief with the user goal, primary audience, key action, supported states, constraints, and existing components or tokens to reuse. If evidence is missing, inspect the current interface and design system before choosing a direction.

Avoid generic decoration that is unrelated to the product. Prefer clear hierarchy, intentional spacing, readable type, restrained color, and a small number of consistent interaction patterns. Do not manufacture novelty at the cost of usability.

## Required states and checks

For each changed flow, consider the states that actually apply:

- default, hover, focus, active, disabled, loading, empty, success, warning, and error
- narrow and wide layouts supported by the existing product
- keyboard navigation, visible focus, labels, contrast, reduced motion, and readable zoom
- realistic short and long content, including Turkish characters when the product supports Turkish

The implementer owns only the assigned files. The verifier checks the agreed states and acceptance criteria without repairing production code. The reviewer reports findings against the frozen candidate and does not redesign it.

## Deliverable

Report the chosen direction, reused system elements, changed states, focused checks, and any accessibility or platform limitation that remains. Do not claim user research, accessibility compliance, or design-system compatibility unless it was actually verified.
