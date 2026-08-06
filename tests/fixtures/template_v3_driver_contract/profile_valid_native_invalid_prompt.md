---
type: coding_prompt
framework_profile: "0.1-rc.1"
---

# Coding Prompt: Profile Valid, Native Invalid

Synthetic M001 fixture. The leading block is profile-valid OKF concept
frontmatter. The fenced workflow block below parses as YAML but carries no
`milestone` and no `slice`, so it supplies no native routing identity and fails
the native prompt schema.

Workflow metadata:

```yaml
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

- a profile pass coexists with a native failure, and grants nothing
