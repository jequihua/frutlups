# Historical deadline recognition

The owned controller accepts `historical_assessment=(project_relative_path,
raw_sha256)` at `Loop._iteration_owned` and `_preflight._owned_admission`.
Public `run` and `preflight` expose the same raw pair as
`--historical-assessment PATH --historical-assessment-sha SHA256` (both or
neither); `loop.run`, `loop.preflight`, `loop.prepare` and `Loop` take the same
tuple as `historical_assessment`. Each passes it unchanged to this owned validation.
The trusted launcher independently selects the canonical project and the exact
human-reviewed, owner-adopted bytes. A digest is an integrity pin, not a signature
or proof of permission. The input is one tuple of two strings; `None` preserves
the existing strict route without assessment discovery or reads. Each invocation,
restart and canonical clone requires an explicit selection again. There is no
environment/configuration fallback or caller-supplied context parameter.

Generic append remains refused. The selection adds no receipt, config or
lifecycle fields, template parser, dependency or execution permission; the
public flags only carry the raw pair ([public_execution.md](public_execution.md)).

## Exact artifact and hash domains

The independent artifact schema is `frutlups.historical-eligibility/1`. Its four
top-level keys are `schema`, `ledger_prefix`, `assessment`, and `entries`.
`ledger_prefix` is `{bytes, raw_sha256}` for the initial bytes of the canonical
`05_governance/ledger.jsonl`. `assessment` is `{path, raw_sha256}` for the immutable
human-readable basis. Every entry has exactly `resolution`, `target`,
`cap_seconds`, and `authority`. The target contains exactly `prompt`, `coded`,
`verified`, `reviewed`, and `accepted`, each an occurrence `{line, raw_sha256}`.
The resolution is also an occurrence. Authority references are native `{path,
sha}` values, in their resolution's order. Instructions are derived from the
coded event's `notes_path` and `notes_sha`; an `instructions` key is invalid.

Assessment, basis, prefix and complete occurrence lines use raw SHA-256. A line's
hash includes its original terminator. Native references retain native evidence
hashing: CRLF becomes LF only when the entire file contains no NUL; lone CR and
binary bytes remain unchanged. Fresh checks bind the initially read raw reference
bytes as well, so an otherwise normalization-equivalent replacement cannot
silently replace an owned context.

| Resource | Bound |
| --- | --- |
| Assessment JSON | 32,768 raw bytes; UTF-8 without BOM |
| Entries | 1–8, increasing accepted occurrence order |
| Authority references per entry | 1–4, unique, in native order |
| Project-relative path | 1–512 UTF-8 bytes; safe regular files and ancestors |
| Pinned ledger prefix | 1–1,048,576 bytes, ending in LF, at most 4,096 raw lines |
| Occurrence line | Integer 1–4,096, within the prefix |
| Basis, authority, coded instructions | At most 1,048,576 bytes per file |
| JSON container nesting | At most six, checked before recursive decoding |
| Historical cap | Integer 1–2,147,483,647 |

Duplicate, missing and unknown keys, non-finite values, booleans/fractions in
integer fields, trailing JSON data, path aliases, ignored inputs and unrecorded
selection/basis bytes refuse. Existing envelope, manifest, report and receipt
bounds still apply. Reads are bounded before decoding. There is one assessment
parser; the existing native evidence readers retain their responsibilities.

## Applicability and ownership

The reader validates every entry, including entries unused by this invocation.
The order must be resolution < prompt < coded < verified < reviewed < accepted.
All target events must share the exact slice and round. The resolution must be
human/architect authority for that exact slice and the latest same-scope
resolution before the selected prompt. The target must be the same latest
preceding chain selected by holistic support at the exact accepted occurrence.
No duplicate acceptance, verified occurrence, receipt path or receipt digest is
eligible. The target must have a successful implemented/verified/pass chain and
must satisfy frozen timeout < recorded elapsed <= assessed cap.

Only the historical frozen-timeout comparison consults the immutable context.
Command, runtime, manifest, report, result, witness and current-product checks
remain in force. New rounds and acceptances beyond the prefix cannot borrow an
old entry. Active verification, reservation, settlement, grant/retry limits and
unresolved current processes receive no exception or usage refund.

For explicit selection, the existing common-directory writer ownership spans
the read-only selection stage and any subsequent admission/consumer work. The
private loop also validates on no-intent history before ordinary done,
slice/milestone/grant-boundary and needs-specification stops. Selection inspection
does not run the checker, recover commits, launch providers, reserve attempts or
write lifecycle/evidence/product files. Existing ownership bookkeeping remains.

The refusal order is STOP; configured Git/canonical authority, ownership and
native history; unresolved invocation; full assessment validation; then the
ordinary stop or existing work boundary. Checker deadlines remain at the checker
boundary, and grant/scope/jobs/time predicates remain at dispatch. An exhausted
open grant does not prevent approved pending recovery or read-only validation
before an ordinary stop. Invalid
assessment alone raises a validation error with `historical assessment` in a
scrubbed diagnostic; the loop reports the existing `internal` stop. Safety and
integrity refusals retain their classifications and do not claim assessment
success. `require_idle` is not hoisted: it may resolve completed markers, and a
genuinely approved pending commit remains recoverable after valid admission.

The shared stage loads once in an owned interval. Root/topology, authority,
selection, direct references and prefix are checked again at checker/recovery,
reload, historical consumption and seat/effect boundaries. The context is dropped
when the loop interval ends. Endpoint checks do not claim surveillance of hostile
same-user transient replacements that are restored between observations.
Freshness checks enforce identities, not the admission clock. A profiled seat or
verification that completes after that clock expires still persists and settles
under its own unchanged operation deadline. Real operation overruns, unresolved
invocations, STOP and identity drift retain their existing handling.

## Human judgement and qualification limits

Human assessment and owner adoption decide permission meaning and consumed
actions. Runtime cannot interpret prose, reconstruct cumulative budgets or prove
unknown/off-ledger usage. DEC03-I-R4-F-001 remains a carried limitation. The
latest-resolution rule is not a proof that a one-shot action was unused.

The exact adopted archive contains M002-S03 r3 (900 seconds), M004-S01 r4
(1,200 seconds), and M004-S03 r4 (2,400 seconds). These are fixture data, with no
production slice-ID exception. Q008's failed action and Q013's successful action
remain consumed. Q014 supplies a continuing cap, without an automatic retry;
Q015 supplies the separately reassessed awake action. The interrupted predecessor
remains unqualified, and unknown cumulative usage or process disposition is not
zero, refunded or resolved. The basis's preparation-time proposal wording remains
immutable; later explicit adoption activates the reviewed judgement.

`tests/fixtures/historical_eligibility.tar` is the exact 201-line prefix and 24
source files. Its reader tests cover the three actual entries and five chronology
rows. It is not a complete executable controller history. Separate disposable
native projects exercise the real owned private route and pinned 4632a891 template
checker. Original native artifacts need no T schema or product change.

M005-S01-01 and DEC03-I-R5-F-001/T-001 require independent implementation-review
disposition on the resulting evidence. This documentation closes no finding or
T implementation gate. The architect owns the final full execution; coder checks
are focused. Public/installed/live, final export, minimum-Python, POSIX, native
symlink and sleep/resume qualification remain separate.
