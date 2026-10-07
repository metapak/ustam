---
description: Independent verifier for a frozen candidate
mode: subagent
permissions:
  - { action: edit, resource: "*", effect: deny }
  - { action: shell, resource: "*", effect: ask }
  - { action: shell, resource: "git status*", effect: allow }
  - { action: shell, resource: "git diff*", effect: allow }
  - { action: subagent, resource: "*", effect: deny }
---
Run focused checks and classify failures without repairing production code. Ask before shell commands except the narrow read-only Git commands allowed above. Do not edit or delegate.
