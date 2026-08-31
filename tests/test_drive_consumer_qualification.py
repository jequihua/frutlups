"""Tests for M006-S01: pinned Drive consumer qualification and the adversarial campaign.

The consumer under test is the frozen frutlups-drive 0.6.0 release (repository
github.com/jequihua/frutlups-drive, tag v0.6.0, tag object
c6a9f685f1b13317f34f548426978dfca9cf9885, peeled commit
adf7092f51b2e5cffceba271f9d723f50b0d4028), retained read-only in the
self-contained release-authority fixture bundle. The three declared authority-input
surfaces are byte-pinned below with SHA-256 digests observed at that tag; if the
fixture drifts from the pin, these tests fail rather than qualify other bytes.
The original checkout's git identity transcript is recorded in
``03_experiments/m006_adversarial_campaign_report.md``.

Every campaign input is a literal hand-written value in this module (project
files, proposals, forged documents); no expected value is generated from
frutlups tables or enums. Producer subprocesses bind the explicit current
interpreter with ``PYTHONPATH`` set to the product source tree; temporary
projects live beneath the repository's ignored ``local_state/`` root.

The five named adversarial cases and their causal witnesses:

1. lossy prompt generation        -> consumer refusal ``payload_entry_incoherent``;
2. role-crossing correction       -> seam refusal ``entry_unhealthy`` carrying
                                     ``role_impure``/``role_type_incompatible``;
3. corrective attempt/artifact collision -> seam refusal ``prompt_collision``;
4. contradictory non-pass receipt -> separated receipt (``needs_work`` /
                                     ``achieved`` / ``recode_same_slice``), no completion;
5. false milestone completion     -> last-slice separated receipt routing
                                     ``human_override_required`` with
                                     ``milestone_complete: false``, and consumer
                                     refusal of a forged completion claim.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import frutlups

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
LOCAL_STATE = REPO_ROOT / "local_state"
DRIVE_ROOT = TEST_ROOT / "fixtures" / "release_v0_2_0" / "drive_v0_6_0"
SRC_DIR = Path(frutlups.__file__).resolve().parents[1]

DRIVE_RELEASE = "frutlups-drive 0.6.0"
DRIVE_TAG = "v0.6.0"
DRIVE_TAG_OBJECT = "c6a9f685f1b13317f34f548426978dfca9cf9885"
DRIVE_PEELED_COMMIT = "adf7092f51b2e5cffceba271f9d723f50b0d4028"

# Byte pins for the three declared read-only authority-input surfaces, observed
# at the pinned tag. The manifest digest is the identity declared by the M006
# entry; the two module digests bind the exact consumed bytes to that checkout.
PINNED_SURFACES = {
    "src/frutlups_drive/seam_consumer.py": (
        "421f5343d73e6747e5b6364e4b3d81c51da5b5f8aefa2fd2cd45e4e7a9f173f3"
    ),
    "scripts/verify_frutlups_seam_consumer.py": (
        "680dd30fd952e227d6731709b89b10a310018b701aaf6cb93560e624394bc5c8"
    ),
    "tests/fixtures/drive_seam_v1/manifest.json": (
        "f88336f1d70c6f3fbf05bec19bcbb36e0fbdae0ee412de9e4f0d961f8f839b93"
    ),
}
CONSUMER_MODULE = "src/frutlups_drive/seam_consumer.py"
PRODUCER_MANIFEST = TEST_ROOT / "fixtures" / "drive_seam_v1" / "manifest.json"

sc = None  # the pinned consumer module, loaded by setUpModule after its digest check


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def setUpModule() -> None:
    global sc
    consumer_path = DRIVE_ROOT / CONSUMER_MODULE
    if not consumer_path.is_file():
        raise AssertionError(
            "the pinned Drive release-authority fixture is absent; "
            f"restore {DRIVE_RELEASE} tag {DRIVE_TAG} (commit {DRIVE_PEELED_COMMIT}) bytes"
        )
    observed = _sha256(consumer_path.read_bytes())
    if observed != PINNED_SURFACES[CONSUMER_MODULE]:
        raise AssertionError(
            "the local Drive consumer drifted from the pinned release bytes: "
            f"sha256 {observed}; refusing to qualify unpinned bytes"
        )
    spec = importlib.util.spec_from_file_location("m006_pinned_seam_consumer", consumer_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves cls.__module__ here
    spec.loader.exec_module(module)
    sc = module


# --- literal hand-written campaign project -----------------------------------

LAYOUT_YAML = """\
# Hand-written M006 campaign layout (contract-v1 vocabulary).
roadmaps:
  directory: 03_experiments
prompts:
  coding_prompt_dir: prompts/for_coding_agent
  filename_pattern: '{sequence:03d}_{slug}.md'
slice_prompt_contract:
  version: 1
  sidecar_suffix: .slices.yaml
  rendered_sections_required:
  - Current State
  - Active Workspaces
  - Read First
  - Memory Posture
  - Task
  - Implementation Discipline
  - OKF Authoring
  - Write Manifest
  - Objective And Closure Proof
  - Non-Goals
  - Verification
  - Seat Conduct
  - Self-Report
  - Definition Of Done
  - Typed Entry
  rendered_sections_conditional:
  - Opening Gates
  - External Repositories
  - Correction Scope Map
  - Candidate Identity
  - Execution Envelope
  rendered_section_order:
  - Current State
  - Active Workspaces
  - Read First
  - Memory Posture
  - Task
  - Implementation Discipline
  - OKF Authoring
  - Write Manifest
  - Opening Gates
  - External Repositories
  - Correction Scope Map
  - Candidate Identity
  - Execution Envelope
  - Objective And Closure Proof
  - Non-Goals
  - Verification
  - Seat Conduct
  - Self-Report
  - Definition Of Done
  - Typed Entry
  entry_status_values:
  - frozen
  - ready
  authored_by_values:
  - architect_reviewer
  - human_owner
  artifact_types:
  - implementation
  - test
  - evidence
  - analysis
  - documentation
  - fixture
  - generated_output
  - config
  - self_report
  - coding_prompt
  - review_prompt
  - review_report
  - verdict_record
  - acceptance_record
  - routing_state
  - framework_doc
  - governance_record
  role_owners:
  - coder
  - reviewer
  - architect_reviewer
  - human_owner
  - runner
  role_type_matrix:
    coder:
    - implementation
    - test
    - evidence
    - analysis
    - documentation
    - fixture
    - generated_output
    - config
    - self_report
    reviewer:
    - review_prompt
    - review_report
    - evidence
    - analysis
    - documentation
    architect_reviewer:
    - implementation
    - test
    - evidence
    - analysis
    - documentation
    - fixture
    - generated_output
    - config
    - coding_prompt
    - review_prompt
    - review_report
    - verdict_record
    - acceptance_record
    - framework_doc
    - governance_record
    human_owner:
    - documentation
    - coding_prompt
    - review_prompt
    - verdict_record
    - acceptance_record
    - governance_record
    - framework_doc
    - config
    runner:
    - coding_prompt
    - review_prompt
    - verdict_record
    - routing_state
    - generated_output
    - evidence
  reserved_path_classification:
    self_report: _self_report.md
    review_report: _review_report.md
    verdict_record: _verdict_record.md
    review_prompt: prompts/for_review_agent/
    coding_prompt: prompts/for_coding_agent/
  retry_policies:
  - create_once
  - create_fresh_per_attempt
  - modify
  - append_only
  attempt_token: '{attempt}'
  gate_kinds:
  - accepted_review
  - owner_note
  - artifact_exists
  - artifact_identity
  - pinned_external_release
  - human_launch_word
  - external_answer
  cleanup_values:
  - retain_until_closure
  - delete_after_evidence
  - quarantine
  result_handling_values:
  - preserve_and_stop
  - preserve_and_continue
  objective_status_values:
  - achieved
  - not_achieved
  - not_applicable
  - indeterminate
  sentinels:
  - TBD
  - <value>
  - <path>
  - <one move>
"""

ROADMAP_MD = """\
# Active Roadmap

### M001: Telemetry window

Status: active

Slices:

- M001-S01: Add the bounded telemetry window summary
- M001-S02: Expose the window digest

### M002: Window replay re-acquisition

Status: planned

Slices:

- M002-S01: Re-run the bounded window replay under owner authority
"""

EVIDENCE_JSON = '{"window": "partial", "rows": 3}\n'
EVIDENCE_SHA256 = "8e2beee8b2f8107c84df3dcc849be4a3f0ecabcad6f90bc41b681b7e0c819f34"

SIDECAR_YAML = (
    """\
slice_prompt_contract_version: 1
roadmap: active_roadmap.md
slices:
- slice: M001-S01
  title: Add the bounded telemetry window summary
  milestone: M001
  authored_by: architect_reviewer
  status: ready
  dispatch_authority: 05_governance/human_owner_notes/002_m001_s01_dispatch.md
  strictness: Level 3
  mode: normal implementation
  live: false
  corrective: false
  task: Implement the telemetry window summary writer and cover it with tests.
  active_workspaces:
  - 08_pkg
  read_first:
  - PROJECT_STATE.md
  writes:
  - path: 08_pkg/src/telemetry/window_summary.py
    artifact_type: implementation
    role_owner: coder
    retry_policy: modify
  - path: 05_governance/reviews/m001_s01_window_summary_self_report.md
    artifact_type: self_report
    role_owner: coder
    retry_policy: create_once
  non_goals:
  - No telemetry schema change
  verification:
  - python -m unittest discover -s 08_pkg/tests
  opening_gates: none
  external_inputs: none
  candidate_identity: none
  correction: none
  execution_envelope: none
  objective:
    success_criteria:
    - The summary reports one row per admitted window
    closure_proof:
    - A passing focused test run cited in the self-report
  definition_of_done:
  - Window summary implemented and tested
- slice: M001-S02
  title: Expose the window digest
  milestone: M001
  authored_by: architect_reviewer
  status: frozen
  strictness: Level 3
  mode: normal implementation
  live: false
  corrective: false
  task: Expose a read-only digest command over the accepted window summary.
  active_workspaces:
  - 08_pkg
  read_first:
  - PROJECT_STATE.md
  writes:
  - path: 08_pkg/src/telemetry/window_digest.py
    artifact_type: implementation
    role_owner: coder
    retry_policy: create_once
  - path: 05_governance/reviews/m001_s02_window_digest_self_report.md
    artifact_type: self_report
    role_owner: coder
    retry_policy: create_once
  non_goals:
  - No summary schema change
  verification:
  - python -m unittest discover -s 08_pkg/tests
  opening_gates: none
  external_inputs: none
  candidate_identity: none
  correction: none
  execution_envelope: none
  objective:
    success_criteria:
    - The digest reports one row per admitted window
    closure_proof:
    - A passing focused test run cited in the self-report
  definition_of_done:
  - Digest command implemented and tested
- slice: M002-S01
  title: Re-run the bounded window replay under owner authority
  milestone: M002
  authored_by: architect_reviewer
  status: ready
  dispatch_authority: 05_governance/human_owner_notes/003_m002_s01_authority.md
  attempt: '001'
  strictness: Level 4
  mode: corrective repair
  live: false
  corrective: true
  task: Run exactly one bounded window replay and record the joined window. Preserve partial stop evidence verbatim.
  active_workspaces:
  - 01_data
  - 05_governance
  read_first:
  - PROJECT_STATE.md
  - 05_governance/human_owner_notes/003_m002_s01_authority.md
  writes:
  - path: 01_data/evidence/m002_s01_attempt_{attempt}/joined_window.json
    artifact_type: evidence
    role_owner: coder
    retry_policy: create_fresh_per_attempt
  - path: 05_governance/reviews/m002_s01_attempt_{attempt}_self_report.md
    artifact_type: self_report
    role_owner: coder
    retry_policy: create_fresh_per_attempt
  non_goals:
  - No second replay under this authority
  verification:
  - python scripts/acquire_window.py --preflight
  opening_gates:
  - kind: owner_note
    reference: 05_governance/human_owner_notes/003_m002_s01_authority.md
  external_inputs: none
  candidate_identity: none
  correction:
    findings:
    - id: CW1-F1
      violated_invariant: a no-window project carries no window row
      prior_disposition: open
      authority_action: owner note 003 authorizes exactly one bounded replay
      coder_obligation: re-acquire the window under the frozen envelope and cite the rows
      closure_proof: three admitted rows per arm in joined_window.json
    prior_evidence:
    - path: 01_data/evidence/m002_s01_attempt_001/partial_window.json
      sha256: """
    + EVIDENCE_SHA256
    + """
    controlling_ruling: 05_governance/human_owner_notes/003_m002_s01_authority.md
    closure_proof:
    - Three admitted rows per arm, or a preserved partial stop with the exact exception
    claims_withdrawn: none
    evidence_invalidated: none
    minimum_rerun_set:
    - the three-case window replay
  execution_envelope: none
  objective:
    success_criteria:
    - Three exact window rows per arm are admitted
    closure_proof:
    - joined_window.json with three rows per arm
  definition_of_done:
  - Joined window written at the resolved evidence path
  - Attempt-qualified self-report written
"""
)

PROMPT_M001_S01 = """\
# Coding Prompt M001-S01: Add the bounded telemetry window summary

Hand-written rendered prompt bytes for the M006 campaign payload case.
"""

PROMPT_M002_S01_A001 = """\
# Coding Prompt M002-S01: attempt 001

## Typed Entry

```yaml
slice: "M002-S01"
attempt: "001"
```
"""

OWNER_NOTE_003 = """\
# Owner note 003

One bounded replay of the M002-S01 window acquisition is authorized.
"""

REPORT_PASS_ACHIEVED = """\
## Closure Decision

Objective status: achieved
Objective evidence: the focused window suite passes and is cited

## Verdict

Verdict: pass - next: advance the frontier
"""

REPORT_NEEDS_WORK_ACHIEVED = """\
## Closure Decision

Objective status: achieved
Objective evidence: the focused window suite passes and is cited

## Verdict

Verdict: needs_work - next: recode the window summary edge case
"""

REPORT_PASS_NOT_ACHIEVED_LAST = """\
## Closure Decision

Objective status: not_achieved
Objective evidence: the digest objective is unmet; the run is preserved

## Verdict

Verdict: pass - next: route the completion decision to the human owner
"""

PROJECT_FILES = {
    "frutlups.layout.yaml": LAYOUT_YAML,
    "03_experiments/active_roadmap.md": ROADMAP_MD,
    "03_experiments/active_roadmap.slices.yaml": SIDECAR_YAML,
    "05_governance/human_owner_notes/003_m002_s01_authority.md": OWNER_NOTE_003,
    "01_data/evidence/m002_s01_attempt_001/partial_window.json": EVIDENCE_JSON,
    "prompts/for_coding_agent/001_m001_s01_window_summary.md": PROMPT_M001_S01,
    "prompts/for_coding_agent/005_m002_s01_attempt_001.md": PROMPT_M002_S01_A001,
    "05_governance/reviews/m001_s01_review_report.md": REPORT_PASS_ACHIEVED,
    "05_governance/reviews/m001_s01_needs_work_report.md": REPORT_NEEDS_WORK_ACHIEVED,
    "05_governance/reviews/m001_s02_last_slice_report.md": REPORT_PASS_NOT_ACHIEVED_LAST,
}

SIDECAR = "03_experiments/active_roadmap.slices.yaml"
CORRECTIVE_PROMPT_TEMPLATE = "prompts/for_coding_agent/005_m002_s01_attempt_{attempt}.md"

# The corrective entry template for the campaign proposals. With
# ``role_crossing`` a third write appears: a coder-owned ``review_report``,
# violating exactly one row of the layout role/type matrix.
CORRECTIVE_TEMPLATE_JSON = json.dumps(
    {
        "slice": "M002-S01",
        "title": "Re-run the bounded window replay under owner authority",
        "milestone": "M002",
        "authored_by": "architect_reviewer",
        "status": "ready",
        "dispatch_authority": "05_governance/human_owner_notes/003_m002_s01_authority.md",
        "strictness": "Level 4",
        "mode": "corrective repair",
        "live": False,
        "corrective": True,
        "task": "Run exactly one bounded window replay and record the joined window. Preserve partial stop evidence verbatim.",
        "active_workspaces": ["01_data", "05_governance"],
        "read_first": [
            "PROJECT_STATE.md",
            "05_governance/human_owner_notes/003_m002_s01_authority.md",
        ],
        "writes": [
            {
                "path": "01_data/evidence/m002_s01_attempt_{attempt}/joined_window.json",
                "artifact_type": "evidence",
                "role_owner": "coder",
                "retry_policy": "create_fresh_per_attempt",
            },
            {
                "path": "05_governance/reviews/m002_s01_attempt_{attempt}_self_report.md",
                "artifact_type": "self_report",
                "role_owner": "coder",
                "retry_policy": "create_fresh_per_attempt",
            },
        ],
        "non_goals": ["No second replay under this authority"],
        "verification": ["python scripts/acquire_window.py --preflight"],
        "opening_gates": [
            {
                "kind": "owner_note",
                "reference": "05_governance/human_owner_notes/003_m002_s01_authority.md",
            }
        ],
        "external_inputs": "none",
        "candidate_identity": "none",
        "correction": {
            "findings": [
                {
                    "id": "CW1-F1",
                    "violated_invariant": "a no-window project carries no window row",
                    "prior_disposition": "open",
                    "authority_action": "owner note 003 authorizes exactly one bounded replay",
                    "coder_obligation": "re-acquire the window under the frozen envelope and cite the rows",
                    "closure_proof": "three admitted rows per arm in joined_window.json",
                }
            ],
            "prior_evidence": [
                {
                    "path": "01_data/evidence/m002_s01_attempt_001/partial_window.json",
                    "sha256": EVIDENCE_SHA256,
                }
            ],
            "controlling_ruling": "05_governance/human_owner_notes/003_m002_s01_authority.md",
            "closure_proof": [
                "Three admitted rows per arm, or a preserved partial stop with the exact exception"
            ],
            "claims_withdrawn": "none",
            "evidence_invalidated": "none",
            "minimum_rerun_set": ["the three-case window replay"],
        },
        "execution_envelope": "none",
        "objective": {
            "success_criteria": ["Three exact window rows per arm are admitted"],
            "closure_proof": ["joined_window.json with three rows per arm"],
        },
        "definition_of_done": [
            "Joined window written at the resolved evidence path",
            "Attempt-qualified self-report written",
        ],
    }
)

ROLE_CROSSING_WRITE = {
    "path": "05_governance/reviews/m002_s01_attempt_{attempt}_review_report.md",
    "artifact_type": "review_report",
    "role_owner": "coder",
    "retry_policy": "create_fresh_per_attempt",
}


def _corrective_template(*, role_crossing: bool = False) -> dict:
    template = json.loads(CORRECTIVE_TEMPLATE_JSON)
    if role_crossing:
        template["writes"].append(dict(ROLE_CROSSING_WRITE))
    return template


# Case 1: a forged drive-payload document whose lossless entry carrier drifted
# from the resolved payload (the ``title`` was rendered lossily). Every other
# field is coherent, so the entry mirror is the one exercised defense.
CASE1_TRUE_TITLE = "Add the bounded telemetry window summary"
CASE1_LOSSY_TITLE = "Add the boundless telemetry window summary"
CASE1_WRITES = [
    {
        "path": "08_pkg/src/telemetry/window_summary.py",
        "artifact_type": "implementation",
        "role_owner": "coder",
        "retry_policy": "modify",
    },
    {
        "path": "05_governance/reviews/m001_s01_window_summary_self_report.md",
        "artifact_type": "self_report",
        "role_owner": "coder",
        "retry_policy": "create_once",
    },
]


def _case1_document(*, entry_title: str) -> dict:
    return {
        "schema": "frutlups.drive_payload.v1",
        "version": 1,
        "payload": {
            "schema": "frutlups.slice_prompt_payload.v1",
            "contract_version": 1,
            "slice": "M001-S01",
            "milestone": "M001",
            "title": CASE1_TRUE_TITLE,
            "status": "ready",
            "authored_by": "architect_reviewer",
            "dispatch_authority": "05_governance/human_owner_notes/002_m001_s01_dispatch.md",
            "attempt": None,
            "live": False,
            "corrective": False,
            "writes": CASE1_WRITES,
            "execution_envelope": None,
            "entry": {
                "slice": "M001-S01",
                "title": entry_title,
                "milestone": "M001",
                "authored_by": "architect_reviewer",
                "status": "ready",
                "dispatch_authority": "05_governance/human_owner_notes/002_m001_s01_dispatch.md",
                "live": False,
                "corrective": False,
                "writes": CASE1_WRITES,
                "task": "Implement the telemetry window summary writer and cover it with tests.",
            },
        },
        "adoption": {
            "slice": "M001-S01",
            "attempt": None,
            "prompt_path": "prompts/for_coding_agent/001_m001_s01_window_summary.md",
            "prompt_sha256": "0" * 64,
            "self_report_path": "05_governance/reviews/m001_s01_window_summary_self_report.md",
            "evidence_paths": [],
            "prior_evidence": [],
        },
    }


# Case 5 (consumer defense): a forged frontier claiming completion for a
# receipt combination outside the frozen two-case completion rule.
CASE5_FORGED_RECEIPT = {
    "verdict": "pass",
    "objective_status": "not_achieved",
    "route": "milestone_complete",
}


def _case5_forged_frontier() -> dict:
    return {
        "schema": "frutlups.frontier.v2",
        "version": 2,
        "milestone": "M001",
        "slice": "M001-S02",
        "step": "complete_milestone",
        "outcome": "milestone_complete",
        "route": "milestone_complete",
        "reason": "forged completion claim",
        "milestone_complete": True,
        "receipt": dict(CASE5_FORGED_RECEIPT),
        "receipt_sha256": _sha256(_canonical(CASE5_FORGED_RECEIPT)),
    }


# --- harness -----------------------------------------------------------------


def _consumer_env() -> dict[str, str]:
    """Explicit bindings only: no ambient PATH or PYTHONPATH reaches the child."""

    env = {"PYTHONPATH": str(SRC_DIR), "PYTHONDONTWRITEBYTECODE": "1"}
    for name in ("SYSTEMROOT", "APPDATA", "USERPROFILE", "HOME"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


class CampaignProjectCase(unittest.TestCase):
    """One hand-written governed project plus the pinned consumer per test."""

    def setUp(self) -> None:
        LOCAL_STATE.mkdir(exist_ok=True)
        tmp = TemporaryDirectory(prefix="m006_campaign_", dir=LOCAL_STATE)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "project"
        self.root.mkdir()
        for rel, content in PROJECT_FILES.items():
            target = self.root.joinpath(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        self.consumer = sc.FrutlupsSeamConsumer(
            python_executable=Path(sys.executable),
            project_root=self.root,
            env=_consumer_env(),
        )

    def _proposal(self, *, role_crossing: bool = False) -> bytes:
        return sc.build_corrective_publication_proposal(
            slice_id="M002-S01",
            sidecar_path=SIDECAR,
            prompt_path=CORRECTIVE_PROMPT_TEMPLATE,
            entry_template=_corrective_template(role_crossing=role_crossing),
        )


class PinnedConsumerIdentityTests(unittest.TestCase):
    """The consumed local checkout carries exactly the pinned release surfaces."""

    def test_every_declared_authority_surface_matches_its_pinned_digest(self) -> None:
        for relative, expected in PINNED_SURFACES.items():
            with self.subTest(surface=relative):
                target = DRIVE_ROOT.joinpath(*relative.split("/"))
                self.assertTrue(target.is_file(), f"pinned surface absent: {relative}")
                self.assertEqual(_sha256(target.read_bytes()), expected)

    def test_drive_fixture_manifest_equals_the_frozen_producer_manifest(self) -> None:
        drive_manifest = DRIVE_ROOT / "tests" / "fixtures" / "drive_seam_v1" / "manifest.json"
        self.assertEqual(drive_manifest.read_bytes(), PRODUCER_MANIFEST.read_bytes())


class ConsumerSeamQualificationTests(CampaignProjectCase):
    """The pinned consumer accepts the versioned payload and frontier seam."""

    def test_versioned_payload_is_admitted_typed_and_lossless(self) -> None:
        response = self.consumer.drive_payload(
            sidecar_path=SIDECAR,
            slice_id="M001-S01",
            prompt_path="prompts/for_coding_agent/001_m001_s01_window_summary.md",
        )
        self.assertIsInstance(response, sc.DrivePayload)
        self.assertEqual(response.schema, "frutlups.drive_payload.v1")
        self.assertEqual(response.version, 1)
        payload = response.payload
        self.assertEqual(payload.schema, "frutlups.slice_prompt_payload.v1")
        self.assertEqual(payload.contract_version, 1)
        self.assertEqual(payload.slice, "M001-S01")
        self.assertEqual(payload.title, "Add the bounded telemetry window summary")
        self.assertIsNone(payload.attempt)
        self.assertFalse(payload.live)
        self.assertFalse(payload.corrective)
        self.assertIsNone(payload.execution_envelope)
        # Losslessness: the entry carrier mirrors the resolved payload facts.
        entry = payload.entry
        self.assertEqual(entry["title"], payload.title)
        self.assertEqual(entry["writes"], list(payload.writes))
        self.assertEqual(entry["task"], "Implement the telemetry window summary writer and cover it with tests.")
        adoption = response.adoption
        self.assertEqual(adoption.slice, "M001-S01")
        self.assertIsNone(adoption.attempt)
        self.assertEqual(
            adoption.prompt_sha256, _sha256(PROMPT_M001_S01.encode("utf-8"))
        )
        self.assertEqual(
            adoption.self_report_path,
            "05_governance/reviews/m001_s01_window_summary_self_report.md",
        )
        self.assertEqual(adoption.evidence_paths, ())
        self.assertEqual(adoption.prior_evidence, ())

    def test_frontier_v2_is_admitted_with_a_separated_receipt(self) -> None:
        response = self.consumer.drive_frontier(
            sidecar_path=SIDECAR,
            slice_id="M001-S01",
            review_report_path="05_governance/reviews/m001_s01_review_report.md",
        )
        self.assertIsInstance(response, sc.Frontier)
        self.assertEqual(response.schema, "frutlups.frontier.v2")
        self.assertEqual(response.version, 2)
        self.assertEqual(response.milestone, "M001")
        self.assertEqual(response.slice, "M001-S01")
        self.assertEqual(response.route, "advance_to_next_slice")
        self.assertEqual(response.outcome, response.route)
        self.assertEqual(response.step, "advance_slice")
        self.assertEqual(response.reason, "accepted_achieved_advances")
        self.assertFalse(response.milestone_complete)
        receipt = response.receipt
        self.assertEqual(receipt.verdict, "pass")
        self.assertEqual(receipt.objective_status, "achieved")
        self.assertEqual(receipt.route, "advance_to_next_slice")
        self.assertEqual(
            response.receipt_sha256,
            _sha256(
                _canonical(
                    {
                        "verdict": "pass",
                        "objective_status": "achieved",
                        "route": "advance_to_next_slice",
                    }
                )
            ),
        )

    def test_corrective_dry_run_validates_with_an_identity_rich_receipt(self) -> None:
        proposal = self._proposal()
        response = self.consumer.corrective_publish(proposal, dry_run=True)
        self.assertIsInstance(response, sc.CorrectiveReceipt)
        self.assertEqual(response.mode, "dry_run")
        self.assertEqual(response.outcome, "validated")
        self.assertEqual(response.slice, "M002-S01")
        self.assertEqual(response.attempt, "002")
        self.assertEqual(response.proposal_sha256, _sha256(proposal))
        self.assertEqual(response.transaction_id, "cp." + _sha256(proposal))
        self.assertEqual(
            response.rendered_prompt.path,
            "prompts/for_coding_agent/005_m002_s01_attempt_002.md",
        )
        self.assertEqual(response.sidecar_entry.path, SIDECAR)
        self.assertEqual(response.refusal_codes, ())
        self.assertEqual(response.before, response.after)
        self.assertEqual(
            set(response.before),
            {
                SIDECAR,
                SIDECAR + ".publish-tmp",
                SIDECAR + ".rollback-tmp",
                "prompts/for_coding_agent/005_m002_s01_attempt_002.md",
            },
        )


class AdversarialCampaignTests(CampaignProjectCase):
    """The five named autonomous failure cases, each with one causal witness."""

    def test_case_1_lossy_prompt_generation_is_refused_by_the_consumer(self) -> None:
        forged = sc.canonical_json_bytes(
            _case1_document(entry_title=CASE1_LOSSY_TITLE), final_lf=True
        )
        with self.assertRaises(sc.SeamAdmissionFailure) as raised:
            sc.admit_seam_response(exit_code=0, stdout=forged)
        self.assertEqual(raised.exception.code, "payload_entry_incoherent")
        self.assertEqual(
            raised.exception.message, "the lossless entry differs from the resolved payload"
        )
        # Control: the identical document with a faithful carrier is admitted.
        faithful = sc.canonical_json_bytes(
            _case1_document(entry_title=CASE1_TRUE_TITLE), final_lf=True
        )
        control = sc.admit_seam_response(exit_code=0, stdout=faithful)
        self.assertIsInstance(control, sc.DrivePayload)
        self.assertEqual(control.payload.entry["title"], CASE1_TRUE_TITLE)

    def test_case_2_role_crossing_correction_is_refused_end_to_end(self) -> None:
        response = self.consumer.corrective_publish(
            self._proposal(role_crossing=True), dry_run=True
        )
        self.assertIsInstance(response, sc.DriveSeamRefusal)
        self.assertEqual(response.verb, "corrective-publish")
        self.assertEqual(response.code, "entry_unhealthy")
        self.assertIn("role_type_incompatible", response.detail)
        self.assertIn("role_impure: manifest row is not role-pure", response.detail)
        # Causal control: the same proposal without the crossing write validates.
        control = self.consumer.corrective_publish(self._proposal(), dry_run=True)
        self.assertIsInstance(control, sc.CorrectiveReceipt)
        self.assertEqual(control.outcome, "validated")

    def test_case_3_corrective_attempt_artifact_collision_is_refused(self) -> None:
        colliding = (
            self.root / "prompts" / "for_coding_agent" / "005_m002_s01_attempt_002.md"
        )
        colliding.write_text("# Pre-existing prompt at attempt 002\n", encoding="utf-8")
        proposal = self._proposal()
        response = self.consumer.corrective_publish(proposal, dry_run=True)
        self.assertIsInstance(response, sc.DriveSeamRefusal)
        self.assertEqual(response.verb, "corrective-publish")
        self.assertEqual(response.code, "prompt_collision")
        self.assertEqual(
            response.detail,
            "prompt_collision: a prompt already exists at "
            "prompts/for_coding_agent/005_m002_s01_attempt_002.md; "
            "refusing to overwrite accepted history",
        )
        # Causal control: removing the colliding artifact validates the same bytes.
        colliding.unlink()
        control = self.consumer.corrective_publish(proposal, dry_run=True)
        self.assertIsInstance(control, sc.CorrectiveReceipt)
        self.assertEqual(control.outcome, "validated")
        self.assertEqual(control.attempt, "002")

    def test_case_4_contradictory_non_pass_receipt_stays_separated(self) -> None:
        response = self.consumer.drive_frontier(
            sidecar_path=SIDECAR,
            slice_id="M001-S01",
            review_report_path="05_governance/reviews/m001_s01_needs_work_report.md",
        )
        self.assertIsInstance(response, sc.Frontier)
        self.assertEqual(response.route, "recode_same_slice")
        self.assertEqual(response.outcome, "recode_same_slice")
        self.assertEqual(response.step, "recode_slice")
        self.assertEqual(response.reason, "needs_work_recodes_same_slice")
        self.assertFalse(response.milestone_complete)
        receipt = response.receipt
        self.assertEqual(receipt.verdict, "needs_work")
        self.assertEqual(receipt.objective_status, "achieved")
        self.assertEqual(receipt.route, "recode_same_slice")

    def test_case_5_false_milestone_completion_cannot_complete(self) -> None:
        # End to end: the last slice of M001 with an accepted pass verdict but
        # unachieved objective must route to the human gate, never complete.
        response = self.consumer.drive_frontier(
            sidecar_path=SIDECAR,
            slice_id="M001-S02",
            review_report_path="05_governance/reviews/m001_s02_last_slice_report.md",
        )
        self.assertIsInstance(response, sc.Frontier)
        self.assertEqual(response.slice, "M001-S02")
        self.assertEqual(response.route, "human_override_required")
        self.assertEqual(response.step, "human_gate")
        self.assertEqual(response.reason, "accepted_not_achieved_requires_human_routing")
        self.assertFalse(response.milestone_complete)
        receipt = response.receipt
        self.assertEqual(receipt.verdict, "pass")
        self.assertEqual(receipt.objective_status, "not_achieved")
        self.assertEqual(receipt.route, "human_override_required")
        # Consumer defense: a forged document claiming completion outside the
        # frozen two-case rule is refused before adoption.
        forged = sc.canonical_json_bytes(_case5_forged_frontier(), final_lf=True)
        with self.assertRaises(sc.SeamAdmissionFailure) as raised:
            sc.admit_seam_response(exit_code=0, stdout=forged)
        self.assertEqual(raised.exception.code, "frontier_receipt_route_incoherent")


if __name__ == "__main__":
    unittest.main()
