# Fixtures

`v4_project/` is the `git archive` export of agentic-project-template-v4 at
tag v4.0.0 (commit a2e646c), unmodified except that `CLAUDE.md` is removed so
no agent session working in this tree imports the fixture's doctrine. It is the
conformance reference: tests copy it to a temporary directory, `git init` there,
and run its `scripts/` next to frutlups on the same files. Do not edit it; when
the template changes, replace the whole directory from a new export and update
this note.

`samples/` holds recorded real seat output (Pi JSONL, Claude Code JSON) used by
the adapter tests. Samples are scrubbed of paths and identifiers before commit.
