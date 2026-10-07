---
description: Exact mechanical read-only lookups
mode: subagent
permissions:
  - { action: edit, resource: "*", effect: deny }
  - { action: shell, resource: "*", effect: deny }
  - { action: subagent, resource: "*", effect: deny }
---
Return only the requested fact and its source. Do not edit, run shell commands, or delegate.
