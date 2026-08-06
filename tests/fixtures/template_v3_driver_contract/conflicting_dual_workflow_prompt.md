---
milestone: M003
slice: S01
role: coder
mode: normal implementation
strictness: Level 2
status: ready
---

# Coding Prompt: Conflicting Dual-Region Routing

Synthetic M001 fixture. The leading YAML block carries v2-style routing and no
OKF concept fields, so the legacy compatibility branch applies to it. The fenced
workflow block below carries the same routing field names with different values.

Workflow metadata:

```yaml
milestone: M007
slice: S04
role: coder
mode: normal implementation
strictness: Level 2
status: ready
```

## Current State

Fixture input only. This file is classified, never executed.

## Active Workspaces

- `08_pkg`

## Read First

- this file

## Task

None. This file exists to be classified by the M001 driver contract.

## Non-Goals

- executing any loop step
- asserting any runtime result

## Verification

None. Classification is a document-level decision.

## Self-Report

None.

## Definition Of Done

- the routing conflict is detected and refused, never silently resolved
