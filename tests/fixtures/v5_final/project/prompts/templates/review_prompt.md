# Review prompt: {{slice_id}} — {{title}} (round {{round}})

## Objective and acceptance

{{objective}}

{{acceptance}}

## Non-goals

{{non_goals}}

## Read first

{{read_first}}

## Implementation boundary

Allowed prefixes: {{allowed_prefixes}}

Forbidden: {{forbidden}} and all other paths. These describe the coder's scope;
review remains product-read-only. Read the evidence artifacts named below using
your file-reading tools. Their categories describe volume, not authority.

## Declared verification

Focused:

{{focused}}

Full:

{{full}}

## Advisory notes

{{notes}}

## Changed files

{{diff_manifest}}

## Code diff

{{diff_evidence}}

## Coder notes

{{coder_notes}}

## Verification receipt

{{receipt}}

## Prior findings

{{prior_findings}}

## Memory

{{memory}}

## Output

Autonomous agent roles return the complete report for the runner to save. In manual
mode, write only `{{report_path}}` when that tool is granted, or return it for
the architect to save.

Use this exact contract:

```markdown
# Review: {{slice_id}} round {{round}}

## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |

## Closure Decision
Objective status: achieved | not_achieved | indeterminate
Objective evidence: one sentence tied to acceptance and the receipt

## Verdict
Verdict: pass|needs_work|blocked - next: one move
```

Return the report as the plain text of your final message, in exactly this shape, not enclosed in a code fence. Use one allowed value on each choice line. Each P0-P2 summary identifies obligation, location, conditions, evidence or argument, and material consequence. Distinguish an observed defect from a missing demonstration; present maintainability costs count, hypothetical flexibility and preferences do not. Pass requires adequate evidence for every mandatory frozen-contract requirement and zero open P0-P2. Otherwise use needs_work or blocked. Record objective outcome independently: a predeclared trial may pass execution/reporting with a negative or indeterminate result; a missing required CLI cannot pass.
{{finding_id_rule}}

{{finding_updates}}
