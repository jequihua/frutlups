# frutlups Public API Preservation Contract

Status: accepted product surface; future changes require reviewed compatibility evidence.

## Authoritative Product Surface

The public product surface is defined by `08_pkg/pyproject.toml`,
`08_pkg/src/frutlups/__init__.py`, the installed
distribution metadata, the console entry point, and the product's own tests. This document is a
boundary summary, not a second API source.

## Preserved Public Surface

- Distribution and import name `frutlups`, and Python `>=3.11`.
- The `frutlups` console entry point and nine top-level CLI verbs.
- The package `__all__` export set and every resolving public name.
- Documented dataclass and JSON shapes, including default text and JSON behavior.
- `py.typed`, source-package discovery, the optional `dev` extra, and installed wheel/sdist behavior.
- The exported `parse_simple_yaml` name.

The released version and the export and verb counts are re-derived from the installed package
rather than declared as permanent constants here. A version pinned in this document goes stale
at the next release; the distribution metadata is the source of truth.

## Compatibility And Authority

No OKF/profile result may decide native prompt or report validity, pairing, acceptance, frontier,
gate state, execution, or write permission.

The 0.1.2 status contract adds exactly one top-level `memory_mode` sibling. It has
contract id `frutlups.memory_mode`, version `"1"`, and the exact fields
`contract_id`, `contract_version`, `valid`, `mode`, `memory_root`, and
`diagnostics`. It reports the declaration, never backend availability. Missing
declarations map compatibly to valid mode `none`; malformed or ambiguous
declarations return `valid: false`, `mode: null`, and fixed diagnostic codes.
The existing `memory` health block remains separate. `planning_frontier` version
`"1"`, `loop_resume`, the prior eight verbs, and their non-status command shapes
are unchanged. The additive ninth verb, `declare-rework`, writes one append-only
`frutlups.rework_declaration` version-1 JSON artifact only from a complete
planning frontier. Its declaration fields are `contract_id`, `contract_version`,
`declaration_sequence`, `pass_id`, `baseline_prompt_sequence`, and `slice_ids`.

The committed layout config carries two reviewed opt-in closed vocabularies:
`reports.discovery` (`flat` | `recursive_contained`) and `prompts.pairing`
(`same_sequence` | `workflow_metadata`, designed for configs that also declare
`prompts.numbering: global_flat_sequence`). Absent keys preserve the exact
historical behavior; unknown values for the new keys are ERROR diagnostics that
fall back to the default rather than changing pairing or evidence semantics.
See `README.md` for their behavior.

The `make-coding-prompt` verb additionally accepts `--correction-round N`, where
`N` is an integer from 1 through 999. Omission and round 1 preserve the prior
output byte-for-byte. Ordinary rounds 2 through 999 append a terminal
`_round_NNN` marker to the generated self-report stem (and therefore to the
review-report path derived by `make-review-prompt`); workflow-metadata pairing
also emits the bare integer `round` value. An active accepted-slice rework
declaration cannot be combined with an ordinary round 2+ correction.

## References

See `README.md` for the current package and usage surface. The complete numbered
native-preservation invariant ledger is maintained in the development repository.
