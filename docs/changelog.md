# Release history

These entries describe each release at the time. Historical limitations and pending
work are not current instructions; use the [release guide](release.md) for 0.4.2.

## Release 0.4.2

- **Manual-block continuation.** A resolved, entirely manual round can continue
  without a runner dispatch record for work that never ran. Actual runner history
  still requires its matching policy; authority, integrity, scope and allowance
  refusals remain enforced. Resolution does not grant work or refund usage.
- **Runtime delta.** Only `frutlups/loop.py` and the version literal in
  `frutlups/__init__.py` differ from the frozen 0.4.1 wheel's runtime payload.
- **Qualification.** F recorded eleven installed offline cases and 1,937 source
  tests with zero skips. T added 28 new offline observations, inherited checks
  with their original version attribution, the live exact-pair pipeline and 257
  tests with four declared skips. Holistic completion required a disclosed
  operator handoff; a fresh restart completed without dispatch. See the
  [0.4.2 evidence](release_evidence_0_4_2.md) for identities and limits.

## Release 0.4.1

- **Merger fix.** 0.4.0 stopped when two routed reviewers referred to the same finding
  with different related links. 0.4.1 merges those links into one sorted union when
  source, hash, finding ID and disposition agree. Genuine disposition conflicts and
  ambiguous references still refuse.
- **Version-only change otherwise.** Apart from the version, this is the only change
  from 0.4.0. Only `frutlups/__init__.py` and `frutlups/_loop_evidence.py` differ
  between the two wheels.
- **Qualification.** Qualified as the exact frozen wheel; see the
  [0.4.1 evidence](release_evidence.md#qualification-evidence).

## Release 0.4.0 (superseded)

- 0.4.0 enabled public execution through the owned checker, recovery and
  typed writers.
- Its installed exact-pair offline qualification is in
  [paired qualification](paired_qualification.md).
- It ran the coding and review stages of the live canary before its merger failure
  stopped the run. 0.4.1 then completed that canary.

## Historical releases

The sections below describe each release as it was published. They are history, not
current claims.

### Release 0.3.2

- The conformance fixture and this project's scripts follow template v4.0.2 (D029).
- Milestone status accepts `done`, and `next_slice` scans every active milestone in
  roadmap order, skipping fully accepted milestones (D029).
- Finding summaries may contain literal or escaped pipes (D029).
- A coder job's `cost_usd` is recorded on its `coded` event when reported (D029).

None of these changes were requalified on real seats.

### Release 0.3.1

- The milestone-close commit is implemented and covered by a fake-seat test (D019,
  D027). It includes every artifact path recorded for the milestone (D028).
- Colliding new finding IDs from routed seats are suffixed with the seat name (D024,
  D027).
- Preflight probes every distinct adapter, provider and model combination with one
  read-only job, and keeps streams only on failure (D027).
- Usage rows include holistic jobs, and killed Pi jobs record drained usage in their
  job results (D019, D023, D027).
- The evidence hash and the multi-seat corrective round have end-to-end witnesses
  (D027).

None of these changes were requalified on real seats.

### Release 0.3.0

Version 0.3.0 is incompatible with 0.2.x projects (D002).

The [real-seat qualification](qualification.md) ran on Windows only (N3). It
covered an ordinary slice through acceptance, forced coder timeouts and a forced path
violation (D020).

Pi usage summation (D022) and multi-seat report merging (D024) were fixed after the
canary. They are covered by tests only and were not requalified on real seats
(M004-H3).

The milestone-close commit (D019) remained unimplemented and unexercised. The owner
therefore committed the holistic prompt, report and ledger by hand after each
milestone close (M003-S01-H6, M004-H3). Version 0.3.1 implements the milestone-close
commit (D019, D027, D028).

Claude authentication and capacity, and Pi capacity, are text-defined and were not
exercised by the canary (D016).
