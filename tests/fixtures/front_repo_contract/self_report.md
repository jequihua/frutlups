# Coder Self-Report

This file is the canonical self-report schema for the template. Keep these
headings exactly. Other surfaces reference this file rather than defining their
own schema. The coding prompt template points here. The coder initialization
prompt (`002`) carries an onboarding copy of the skeleton that must remain
identical to this file; the `test_self_report_schema_single_source` scaffold test
enforces that agreement.

When a slice opted any output artifact into the OKF profile (see
`docs/template_framework/okf_authoring_and_migration.md`), record each opted-in
path, its assigned registry `type`, and the read-only profile-check result under the
existing headings below (for example within Files Changed and Verification Run). Add
`type: self_report` frontmatter to this report only when its own exact path was
opted in.

Intent:

Files Changed:

Behavior Implemented:

Tests Added Or Updated:

Verification Run:

Record commands and results as dated/run-specific evidence. Distinguish files
owned by this slice from complete shared-worktree state; do not present a
worktree snapshot, active prompt number, or next action as continuing truth.

Definition Of Done Audit:

Non-Goals Confirmed:

Memory Used:

Memory Update Requested:

Known Limits / Follow-Up:

When touched code shows material out-of-scope complexity accretion, name one
evidence-backed simplification candidate and treat it explicitly as unapproved
follow-up, not authorized work.

Recommended Next Move:

Reference `PROJECT_STATE.md` or the prompt/review index. Avoid copying a current
prompt number when the identity may change during corrective review.

