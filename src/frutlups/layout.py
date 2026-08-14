"""Layout/profile configuration for frutlups (M017-S01).

frutlups historically hardcoded the legacy artifact-first template shape:
workspace folders, prompt directories, roadmap globs, report suffixes, and prompt
section headings. This module externalizes those assumptions into a typed
:class:`LayoutProfile` so the package can operate against the v2 template layout
(see ``docs/config_files/v2/frutlups.layout.yaml``) without hardcoding one
template generation.

Design posture (see ``docs/config_files/frutlups_layout_config_usage.md``):

- Config files are *rails, not autonomy*. They make the artifact rails explicit;
  they do not add a runner or any provider/network behavior.
- The built-in default profile is **v2-oriented**. When a project is recognizably
  the legacy/root template (no config, no ``PROJECT_STATE.md``), a narrow
  **legacy compatibility fallback** profile is selected so existing projects and
  the current test suite keep working unchanged.
- The schema is permissive for forward evolution: unknown keys are ignored, but an
  unsupported ``schema_version`` is reported as a diagnostic.
- Paths frutlups will *write to* must be repo-relative and must not escape the
  template root; absolute paths and ``..`` escapes are rejected.

Config files are read exclusively through the private bounded YAML boundary
:mod:`frutlups._yaml` (M002-S03) and then checked against a private,
layout-specific schema: exactly one root mapping, string keys at every mapping
level, and no anchors/aliases, merge keys, explicit tags, or flow collections.
The exported ``parse_simple_yaml`` name is retained as a documented
compatibility wrapper over the same boundary and schema (M002-S05); the
pre-M002 custom line-oriented parser is deleted, and no custom-parser
fallback remains reachable anywhere in the package.

This module otherwise depends only on the standard library. ``frutlups._yaml``
imports no ``frutlups`` modules, so importing it here keeps ``artifacts`` and
``project`` free of an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath

from frutlups._yaml import (
    YamlBoundaryError,
    YamlDocument,
    load_yaml_bytes,
    load_yaml_path,
)

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"frutlups_layout_config_v0"})
"""Layout-config ``schema_version`` strings this frutlups understands."""

DEFAULT_CONFIG_FILENAME = "frutlups.layout.yaml"
"""Conventional config filename discovered at the project root."""


class ProfileSource(StrEnum):
    """How the active layout profile was selected."""

    EXPLICIT_CONFIG = "explicit_config"  # --layout-config <path>
    PROJECT_CONFIG = "project_config"  # frutlups.layout.yaml at the root
    V2_STATE_DEFAULT = "v2_state_default"  # PROJECT_STATE.md present, no config
    LEGACY_FALLBACK = "legacy_fallback"  # narrow compatibility for the old template


class LayoutDiagnosticSeverity(StrEnum):
    """Severity for a layout/profile diagnostic."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class LayoutDiagnostic:
    """A typed observation about layout-config loading or profile conformance."""

    code: str
    severity: LayoutDiagnosticSeverity
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class LayoutModeField:
    """A controlled mode field declared by a v2 ``PROJECT_STATE.md`` contract."""

    key: str
    label: str
    allowed_values: tuple[str, ...]
    default: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "allowed_values": list(self.allowed_values),
            "default": self.default,
        }


@dataclass(frozen=True)
class AutomationBoundaryPolicy:
    """Runner automation boundary (advisory; frutlups never auto-executes)."""

    runner_implemented: bool = False
    boundary_doc: str = ""
    may_consume: tuple[str, ...] = ()
    must_stop_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "runner_implemented": self.runner_implemented,
            "boundary_doc": self.boundary_doc,
            "may_consume": list(self.may_consume),
            "must_stop_on": list(self.must_stop_on),
        }


@dataclass(frozen=True)
class GitPolicy:
    """Git commit policy. Defaults are safe: a runner may not commit."""

    default: str = ""
    commit_boundary: str = ""
    policy_doc: str = ""
    default_committer_role: str = ""
    coder_may_commit_by_default: bool = False
    architect_reviewer_may_commit_at_boundary: bool = False
    runner_may_commit: bool = False
    runner_may_commit_when_explicitly_authorized: bool = False
    runner_may_report_commit_ready: bool = False
    commit_ready_requires: tuple[str, ...] = ()
    before_commit_requires: tuple[str, ...] = ()
    auto_commit_requires_explicit_configuration: bool = True
    must_not_bypass_stop_conditions_to_commit: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "default": self.default,
            "commit_boundary": self.commit_boundary,
            "policy_doc": self.policy_doc,
            "default_committer_role": self.default_committer_role,
            "coder_may_commit_by_default": self.coder_may_commit_by_default,
            "architect_reviewer_may_commit_at_boundary": (
                self.architect_reviewer_may_commit_at_boundary
            ),
            "runner_may_commit": self.runner_may_commit,
            "runner_may_commit_when_explicitly_authorized": (
                self.runner_may_commit_when_explicitly_authorized
            ),
            "runner_may_report_commit_ready": self.runner_may_report_commit_ready,
            "commit_ready_requires": list(self.commit_ready_requires),
            "before_commit_requires": list(self.before_commit_requires),
            "auto_commit_requires_explicit_configuration": (
                self.auto_commit_requires_explicit_configuration
            ),
            "must_not_bypass_stop_conditions_to_commit": (
                self.must_not_bypass_stop_conditions_to_commit
            ),
        }


@dataclass(frozen=True)
class PullRequestPolicy:
    """Pull-request policy. Defaults are safe: a runner may not open PRs."""

    default: str = ""
    suggested_boundary: str = ""
    policy_doc: str = ""
    human_may_request_any_time: bool = False
    runner_may_open_pull_request: bool = False
    runner_may_report_pull_request_ready: bool = False
    open_pull_request_requires_explicit_authorization: bool = True
    must_not_bypass_stop_conditions_to_open_pull_request: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "default": self.default,
            "suggested_boundary": self.suggested_boundary,
            "policy_doc": self.policy_doc,
            "human_may_request_any_time": self.human_may_request_any_time,
            "runner_may_open_pull_request": self.runner_may_open_pull_request,
            "runner_may_report_pull_request_ready": self.runner_may_report_pull_request_ready,
            "open_pull_request_requires_explicit_authorization": (
                self.open_pull_request_requires_explicit_authorization
            ),
            "must_not_bypass_stop_conditions_to_open_pull_request": (
                self.must_not_bypass_stop_conditions_to_open_pull_request
            ),
        }


_DEFAULT_AUTOMATION_BOUNDARY = AutomationBoundaryPolicy()
_DEFAULT_GIT_POLICY = GitPolicy()
_DEFAULT_PULL_REQUEST_POLICY = PullRequestPolicy()


@dataclass(frozen=True)
class LayoutProfile:
    """A typed, provider-neutral description of a project's artifact layout.

    All path fields are repo-relative POSIX-style strings resolved under the
    project (template) root. ``profile_id`` and ``schema_version`` identify the
    profile. The "section" fields are *normalized* heading names (lowercase,
    punctuation-trimmed) used to read coding-prompt metadata.
    """

    schema_version: str
    profile_id: str
    template_root: str

    # Directory contract.
    required_directories: tuple[str, ...]
    roadmap_dir: str
    active_roadmap_glob: str
    development_roadmap_glob: str
    fallback_roadmap_glob: str

    # Prompt contract.
    coding_prompt_dir: str
    review_prompt_dir: str
    coding_template: str
    review_template: str
    self_report_schema: str
    required_coding_prompt_sections: tuple[str, ...]
    required_review_prompt_sections: tuple[str, ...]

    # Coding-prompt metadata parsing.
    roadmap_item_section: str
    self_report_section: str
    required_reading_section: str
    non_goals_section: str
    parse_front_matter: bool

    # Reports contract.
    reviews_dir: str
    self_report_suffix: str
    review_report_suffix: str
    verdict_record_suffix: str
    verdict_values: tuple[str, ...]

    # State contract (v2).
    state_file: str
    mode_fields: tuple[LayoutModeField, ...]
    current_truth_fields: tuple[str, ...]

    # Validation.
    validation_command: str

    # --- M017-S02: v2 semantic roles, report headings, and policies ---
    # These all carry safe defaults so older v1/draft-v2 configs and the legacy
    # profile keep working unchanged.
    profile_status: str = "proposed"
    # Front-matter metadata field names (which keys identify milestone/slice/title).
    front_matter_milestone_field: str = "milestone"
    front_matter_slice_field: str = "slice"
    front_matter_title_field: str = "title"
    # Additional declared semantic-role section names (surfaced; not yet enforced).
    task_section: str = ""
    verification_section: str = ""
    # Self-report required headings declared by the template (surfaced as evidence).
    self_report_required_headings: tuple[str, ...] = ()
    # Automation / git / pull-request policy (advisory; never auto-executed).
    automation_boundary: AutomationBoundaryPolicy = _DEFAULT_AUTOMATION_BOUNDARY
    git_policy: GitPolicy = _DEFAULT_GIT_POLICY
    pull_request_policy: PullRequestPolicy = _DEFAULT_PULL_REQUEST_POLICY
    # Advisory redesign-repo validation command (not the canonical generated cmd).
    validation_command_redesign: str = ""
    # M003-S02 compatibility modes (owner note 008): closed vocabularies with
    # behavior-preserving defaults. ``prompt_numbering`` describes how prompt
    # filename sequences are allocated/analysed ("per_kind_sequence" |
    # "global_flat_sequence"); ``prompt_pairing`` selects how coding and
    # review prompts pair ("same_sequence" | "workflow_metadata");
    # ``reports_discovery`` selects the acceptance-evidence inventory shape
    # under the configured reviews root ("flat" | "recursive_contained").
    prompt_numbering: str = "per_kind_sequence"
    prompt_pairing: str = "same_sequence"
    reports_discovery: str = "flat"
    # M011-S01: optional-lane llloom memory posture. These carry the selected
    # profile's safe repo-relative machine paths for the mode-aware memory
    # observation and deterministic posture routing. Defaults are the
    # v2/template-v3 values; the legacy fallback overrides them with the
    # historical locations. An empty ``llloom_memory_root`` is a deliberate
    # "unsafe configured root -> disable, do not fall back" sentinel. These are
    # intentionally NOT serialized in ``to_dict`` so the layout-profile JSON
    # contract (and the planning-frontier/status shapes that embed it) is
    # unchanged by this correction.
    llloom_memory_root: str = "llloom_memory"
    llloom_posture_file: str = "05_governance/current/memory_posture.md"
    # Prompt-composition inputs intentionally stay out of ``to_dict`` so this
    # correction does not widen the released layout/status JSON shapes.
    context_filename: str = "CONTEXT.md"
    coder_may_create_review_prompt: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_status": self.profile_status,
            "template_root": self.template_root,
            "required_directories": list(self.required_directories),
            "roadmap_dir": self.roadmap_dir,
            "active_roadmap_glob": self.active_roadmap_glob,
            "development_roadmap_glob": self.development_roadmap_glob,
            "fallback_roadmap_glob": self.fallback_roadmap_glob,
            "coding_prompt_dir": self.coding_prompt_dir,
            "review_prompt_dir": self.review_prompt_dir,
            "coding_template": self.coding_template,
            "review_template": self.review_template,
            "self_report_schema": self.self_report_schema,
            "required_coding_prompt_sections": list(self.required_coding_prompt_sections),
            "required_review_prompt_sections": list(self.required_review_prompt_sections),
            "roadmap_item_section": self.roadmap_item_section,
            "self_report_section": self.self_report_section,
            "required_reading_section": self.required_reading_section,
            "non_goals_section": self.non_goals_section,
            "task_section": self.task_section,
            "verification_section": self.verification_section,
            "parse_front_matter": self.parse_front_matter,
            "front_matter_milestone_field": self.front_matter_milestone_field,
            "front_matter_slice_field": self.front_matter_slice_field,
            "front_matter_title_field": self.front_matter_title_field,
            "reviews_dir": self.reviews_dir,
            "self_report_suffix": self.self_report_suffix,
            "review_report_suffix": self.review_report_suffix,
            "verdict_record_suffix": self.verdict_record_suffix,
            "verdict_values": list(self.verdict_values),
            "self_report_required_headings": list(self.self_report_required_headings),
            "state_file": self.state_file,
            "mode_fields": [m.to_dict() for m in self.mode_fields],
            "current_truth_fields": list(self.current_truth_fields),
            "validation_command": self.validation_command,
            "validation_command_redesign": self.validation_command_redesign,
            "prompt_numbering": self.prompt_numbering,
            "prompt_pairing": self.prompt_pairing,
            "reports_discovery": self.reports_discovery,
            "automation_boundary": self.automation_boundary.to_dict(),
            "git_policy": self.git_policy.to_dict(),
            "pull_request_policy": self.pull_request_policy.to_dict(),
        }


@dataclass(frozen=True)
class LoadedLayout:
    """The active :class:`LayoutProfile` plus how it was selected and any issues."""

    profile: LayoutProfile
    source: ProfileSource
    config_path: str
    diagnostics: tuple[LayoutDiagnostic, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile.profile_id,
            "schema_version": self.profile.schema_version,
            "profile_status": self.profile.profile_status,
            "source": self.source.value,
            "config_path": self.config_path,
            "profile": self.profile.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


# ---------------------------------------------------------------------------
# Heading normalization (shared with coding-prompt section parsing)
# ---------------------------------------------------------------------------


def normalize_section(name: str) -> str:
    """Normalize a section heading for tolerant comparison.

    Lowercases, trims surrounding whitespace, strips trailing ``:!?.;,`` and
    collapses interior whitespace. Matches the heading normalization used when
    parsing coding-prompt sections so config-declared names line up with parsed
    headings regardless of case or trailing punctuation.
    """

    text = name.strip().lower().rstrip(":!?.;,")
    return " ".join(text.split())


def _normalize_sections(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_section(n) for n in names)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

_COMMON_SUFFIXES = ("_self_report.md", "_review_report.md", "_verdict_record.md")
_COMMON_VERDICTS = ("pass", "needs_work", "blocked", "override")


def legacy_profile() -> LayoutProfile:
    """The legacy/root artifact-first template profile.

    Mirrors frutlups' historical hardcoded assumptions exactly so existing
    projects (including this repository) and the current test suite keep working
    when no config and no ``PROJECT_STATE.md`` are present.
    """

    return LayoutProfile(
        schema_version="frutlups_layout_config_v0",
        profile_id="artifact_first_template_legacy_root",
        template_root=".",
        required_directories=(
            "00_brief",
            "03_experiments",
            "05_governance",
            "06_infra",
            "08_pkg",
            "prompts",
        ),
        roadmap_dir="03_experiments",
        active_roadmap_glob="active_roadmap*.md",
        development_roadmap_glob="development_roadmap*.md",
        fallback_roadmap_glob="*roadmap*.md",
        coding_prompt_dir="prompts/for_coding_agent",
        review_prompt_dir="prompts/for_review_agent",
        coding_template="",
        review_template="",
        self_report_schema="",
        required_coding_prompt_sections=(
            "Active Roadmap Item",
            "Required Self-Report",
            "Required Reading",
            "Non-Goals",
        ),
        required_review_prompt_sections=(
            "Review Objective",
            "Review Checks",
            "Verdict",
        ),
        roadmap_item_section="active roadmap item",
        self_report_section="required self-report",
        required_reading_section="required reading",
        non_goals_section="non-goals",
        parse_front_matter=False,
        reviews_dir="05_governance/reviews",
        self_report_suffix=_COMMON_SUFFIXES[0],
        review_report_suffix=_COMMON_SUFFIXES[1],
        verdict_record_suffix=_COMMON_SUFFIXES[2],
        verdict_values=_COMMON_VERDICTS,
        state_file="",
        mode_fields=(),
        current_truth_fields=(),
        validation_command="",
        # M011-S01: genuine legacy fallback keeps the historical memory
        # locations so config-less legacy projects behave exactly as before.
        llloom_memory_root="07_app/llloom_memory",
        llloom_posture_file="05_governance/llloom_operating_model.md",
        context_filename="",
        coder_may_create_review_prompt=True,
    )


def v2_default_profile() -> LayoutProfile:
    """The built-in v2 artifact-first template profile (the default).

    Tailored to ``agentic-project-template-v2/08_new_template``: a base directory
    set without the legacy ``08_pkg`` requirement, leading-wildcard roadmap globs,
    v2 coding-prompt section names, front-matter milestone/slice parsing, and a
    ``PROJECT_STATE.md`` state contract with controlled memory/frutlups modes.
    """

    return LayoutProfile(
        schema_version="frutlups_layout_config_v0",
        profile_id="artifact_first_template_v2",
        template_root=".",
        required_directories=(
            "00_brief",
            "03_experiments",
            "05_governance",
            "prompts",
            "questions",
        ),
        roadmap_dir="03_experiments",
        active_roadmap_glob="*active_roadmap*.md",
        development_roadmap_glob="*development_roadmap*.md",
        fallback_roadmap_glob="*roadmap*.md",
        coding_prompt_dir="prompts/for_coding_agent",
        review_prompt_dir="prompts/for_review_agent",
        coding_template="prompts/templates/coding_prompt.md",
        review_template="prompts/templates/review_prompt.md",
        self_report_schema="prompts/templates/self_report.md",
        required_coding_prompt_sections=(
            "Current State",
            "Active Workspaces",
            "Read First",
            "Task",
            "Non-Goals",
            "Verification",
            "Self-Report",
            "Definition Of Done",
        ),
        required_review_prompt_sections=(
            "Review Objective",
            "Read First",
            "Review Checks",
            "Verification",
            "Output",
            "Non-Goals",
            "Definition Of Done",
        ),
        roadmap_item_section="",
        self_report_section="self-report",
        required_reading_section="read first",
        non_goals_section="non-goals",
        task_section="task",
        verification_section="verification",
        parse_front_matter=True,
        reviews_dir="05_governance/reviews",
        self_report_suffix=_COMMON_SUFFIXES[0],
        review_report_suffix=_COMMON_SUFFIXES[1],
        verdict_record_suffix=_COMMON_SUFFIXES[2],
        verdict_values=_COMMON_VERDICTS,
        self_report_required_headings=(
            "Intent",
            "Files Changed",
            "Behavior Implemented",
            "Tests Added Or Updated",
            "Verification Run",
            "Definition Of Done Audit",
            "Non-Goals Confirmed",
            "Memory Used",
            "Memory Update Requested",
            "Known Limits / Follow-Up",
            "Recommended Next Move",
        ),
        state_file="PROJECT_STATE.md",
        mode_fields=(
            LayoutModeField(
                key="memory",
                label="Memory mode",
                allowed_values=("none", "lightweight", "llloom"),
                default="none",
            ),
            LayoutModeField(
                key="frutlups",
                label="Frutlups mode",
                allowed_values=("manual", "semi-manual", "automated driver"),
                default="manual",
            ),
        ),
        current_truth_fields=(
            "Project profile",
            "Active workspaces",
            "Optional inactive workspaces",
            "Current objective",
            "Current loop mode",
            "Current ceremony level",
            "Memory mode",
            "Frutlups mode",
            "Latest accepted review",
            "Next expected action",
            "Validation command",
        ),
        validation_command="python -m unittest discover -s tests",
    )


def default_profile() -> LayoutProfile:
    """The built-in default profile (v2-oriented)."""

    return v2_default_profile()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def is_safe_relative(rel: str) -> bool:
    """Return ``True`` when ``rel`` is a safe repo-relative path.

    Rejects empty strings, absolute paths (POSIX ``/`` or Windows drive/UNC),
    and any path containing a ``..`` segment that could escape the template root.
    """

    if not rel or not rel.strip():
        return False
    text = rel.strip().replace("\\", "/")
    if text.startswith("/"):
        return False
    # Windows drive (``C:\\``) or UNC (``\\\\host``) absolute forms.
    if len(text) >= 2 and text[1] == ":":
        return False
    pure = PurePosixPath(text)
    if pure.is_absolute():
        return False
    return ".." not in pure.parts


def resolve_under_root(root: Path, rel: str) -> Path:
    """Resolve a safe repo-relative path under ``root``.

    Raises :class:`ValueError` when ``rel`` is absolute or escapes ``root``.
    """

    if not is_safe_relative(rel):
        raise ValueError(f"unsafe layout path (absolute or escapes template root): {rel!r}")
    return root / PurePosixPath(rel.strip().replace("\\", "/"))


MEMORY_LANE_PATH_MAX = 200
"""Conservative upper bound for a configured optional-lane memory path.

Memory-lane paths (``optional_lanes.llloom.memory_root`` / ``posture_file``) are
repository-relative and are rendered inside Markdown code spans/fences in
generated prompts and handoffs. There is no reusable path bound in the accepted
layout schema, so M011-S01 (Prompt 044 Gate C) fixes this smallest conservative
bound so generated diagnostics/artifacts stay bounded and single-line. It is far
larger than any real repo-relative memory path yet small enough to keep a code
span on one rendered line.
"""


def normalize_memory_lane_path(raw: object) -> str | None:
    """Validate and normalize a configured optional-lane memory path.

    One shared memory-lane path contract (M011-S01, Prompt 044 Gate C) used for
    both ``optional_lanes.llloom.memory_root`` and ``posture_file``. Returns the
    normalized safe repository-relative path, or ``None`` when the value is
    missing, non-string, structurally active, out of bounds, or otherwise unsafe.
    The rejected value is never echoed by the caller's diagnostics.

    A value is accepted only when, after normalizing surrounding whitespace and
    path separators exactly once, it is a non-empty repository-relative path
    (existing :func:`is_safe_relative` semantics) that is a single logical line
    and free of backticks and ASCII control/DEL characters. Multiline values
    (``\\r``, ``\\n``, Unicode line/paragraph separators, or any value whose
    ``str.splitlines`` yields more than one line), backtick-bearing values, and
    values longer than :data:`MEMORY_LANE_PATH_MAX` are rejected so the selected
    path cannot introduce a fence, heading, or other live Markdown structure into
    any prompt or handoff consumer. Ordinary safe overrides are preserved
    byte-for-byte after the declared strip/separator normalization.
    """

    if not isinstance(raw, str):
        return None
    # Reject multiline before any stripping: explicit CR/LF, Unicode line and
    # paragraph separators, and anything ``splitlines`` treats as multiple lines.
    if "\r" in raw or "\n" in raw or len(raw.splitlines()) > 1:
        return None
    if any(ord(ch) in (0x2028, 0x2029, 0x85, 0x0B, 0x0C) for ch in raw):
        return None
    text = raw.strip().replace("\\", "/")
    if not text:
        return None
    if len(text) > MEMORY_LANE_PATH_MAX:
        return None
    # Reject backticks and ASCII control/DEL: the selected path is rendered
    # inside Markdown code spans/fences.
    if "`" in text or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    if not is_safe_relative(text):
        return None
    return text


# ---------------------------------------------------------------------------
# Public compatibility wrapper over the bounded YAML boundary (M002-S05)
# ---------------------------------------------------------------------------


class LayoutConfigError(Exception):
    """Raised for unreadable, unparseable, or schema-refused layout configs."""


def parse_simple_yaml(text: str) -> dict[str, object]:
    """Parse a layout config mapping from YAML text (compatibility wrapper).

    The historical public name and signature are retained. Since M002-S05 this
    is a thin, documented compatibility wrapper: the supplied string is
    UTF-8-encoded once, passed through the one private bounded YAML boundary
    (:func:`frutlups._yaml.load_yaml_bytes`) exactly once, and checked by the
    same private layout schema :func:`load_config_file` uses. The pre-M002
    line-oriented custom parser is deleted; no custom-parser fallback exists
    anywhere in the package.

    Deliberate differences from the pre-M002 implementation (see
    ``02_analysis/m002_parse_simple_yaml_compatibility_decision.md``):
    accepted layout configurations produce semantically equivalent profiles,
    while previously tolerated non-YAML or unapproved shapes -- duplicate or
    non-string keys, anchors/aliases, merge keys, explicit tags, flow
    collections, multiple documents, non-mapping roots, and malformed YAML --
    now fail closed with :class:`LayoutConfigError`, and scalar/block values
    follow PyYAML ``SafeLoader`` semantics (typed integers/booleans/nulls,
    folded block scalars keep their trailing newline) instead of the old
    pragmatic string coercions. A string that cannot be UTF-8 encoded at all
    (for example one carrying lone surrogates) is refused with the same owned
    error type rather than leaking a codec exception.
    """

    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise _layout_refusal("input text is not UTF-8 encodable") from None
    return _layout_config_from_boundary(lambda: load_yaml_bytes(data))


# ---------------------------------------------------------------------------
# Config -> profile mapping
# ---------------------------------------------------------------------------


def _as_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _get(mapping: object, *keys: str) -> object:
    cur: object = mapping
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def profile_from_config(
    config: dict[str, object],
) -> tuple[LayoutProfile, tuple[LayoutDiagnostic, ...]]:
    """Build a :class:`LayoutProfile` from a parsed config mapping.

    A *base* profile (v2 by default, legacy when the config is recognizably the
    old template) provides every fallback value, so a partial or forward-evolved
    config still yields a usable profile. Unknown keys are ignored. An unsupported
    ``schema_version`` and unsafe write paths are reported as diagnostics (and
    unsafe write dirs fall back to the base).
    """

    base = _base_profile_for_config(config)
    diagnostics: list[LayoutDiagnostic] = []

    schema_version = _as_str(config.get("schema_version"), base.schema_version)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        diagnostics.append(
            LayoutDiagnostic(
                code="unsupported_schema_version",
                severity=LayoutDiagnosticSeverity.WARNING,
                message=(
                    f"layout config schema_version {schema_version!r} is not in the "
                    f"supported set {sorted(SUPPORTED_SCHEMA_VERSIONS)}; "
                    "interpreting on a best-effort basis"
                ),
            )
        )

    profile_id = _as_str(config.get("profile_id"), base.profile_id)
    template_root = _as_str(config.get("template_root"), base.template_root)

    # Directories.
    required_dirs = _as_str_tuple(
        _get(config, "workspace_map", "required_for_base_profile")
    ) or _as_str_tuple(_get(config, "workspace_map", "required_directories"))
    if not required_dirs:
        required_dirs = base.required_directories

    roadmap_dir = _as_str(_get(config, "roadmaps", "directory"), base.roadmap_dir)
    active_glob = _as_str(_get(config, "roadmaps", "active_roadmap_glob"), base.active_roadmap_glob)
    dev_glob = _as_str(
        _get(config, "roadmaps", "development_roadmap_glob"), base.development_roadmap_glob
    )
    fallback_glob = _as_str(
        _get(config, "roadmaps", "fallback_roadmap_glob"), base.fallback_roadmap_glob
    )

    # Prompts. Write dirs are validated for path-escape safety.
    coding_dir = _as_str(_get(config, "prompts", "coding_prompt_dir"), base.coding_prompt_dir)
    review_dir = _as_str(_get(config, "prompts", "review_prompt_dir"), base.review_prompt_dir)
    reviews_dir = _as_str(_get(config, "reports", "reviews_dir"), base.reviews_dir)
    for label, value, fallback in (
        ("prompts.coding_prompt_dir", coding_dir, base.coding_prompt_dir),
        ("prompts.review_prompt_dir", review_dir, base.review_prompt_dir),
        ("reports.reviews_dir", reviews_dir, base.reviews_dir),
    ):
        if not is_safe_relative(value):
            diagnostics.append(
                LayoutDiagnostic(
                    code="unsafe_write_path",
                    severity=LayoutDiagnosticSeverity.ERROR,
                    message=(
                        f"layout config {label}={value!r} is absolute or escapes the "
                        f"template root; falling back to {fallback!r}"
                    ),
                )
            )
            if label.endswith("coding_prompt_dir"):
                coding_dir = fallback
            elif label.endswith("review_prompt_dir"):
                review_dir = fallback
            else:
                reviews_dir = fallback

    coding_template = _as_str(_get(config, "prompts", "coding_template"), base.coding_template)
    review_template = _as_str(_get(config, "prompts", "review_template"), base.review_template)
    self_report_schema = _as_str(
        _get(config, "prompts", "self_report_schema"), base.self_report_schema
    )
    req_coding_sections = (
        _as_str_tuple(_get(config, "prompts", "required_coding_prompt_sections"))
        or base.required_coding_prompt_sections
    )
    req_review_sections = (
        _as_str_tuple(_get(config, "prompts", "required_review_prompt_sections"))
        or base.required_review_prompt_sections
    )

    # Reports.
    self_report_suffix = _as_str(
        _get(config, "reports", "self_report_suffix"), base.self_report_suffix
    )
    review_report_suffix = _as_str(
        _get(config, "reports", "review_report_suffix"), base.review_report_suffix
    )
    verdict_record_suffix = _as_str(
        _get(config, "reports", "verdict_record_suffix"), base.verdict_record_suffix
    )
    verdict_values = _as_str_tuple(_get(config, "reports", "verdict_values")) or base.verdict_values

    # State + modes.
    state_file = _as_str(_get(config, "state", "canonical_file"), base.state_file)
    context_filename = _as_str(
        _get(config, "workspace_map", "context_filename"), base.context_filename
    ).strip()
    if (
        not context_filename
        or context_filename in (".", "..")
        or "/" in context_filename
        or "\\" in context_filename
        or any(ord(char) < 32 for char in context_filename)
    ):
        context_filename = base.context_filename

    raw_coder_review_policy = _get(config, "prompts", "coder_may_create_review_prompt")
    if isinstance(raw_coder_review_policy, bool):
        coder_may_create_review_prompt = raw_coder_review_policy
    elif isinstance(raw_coder_review_policy, str):
        coder_may_create_review_prompt = raw_coder_review_policy.strip().lower() in {
            "always",
            "allowed",
            "true",
            "yes",
        }
    else:
        coder_may_create_review_prompt = base.coder_may_create_review_prompt
    mode_fields = _mode_fields_from_config(_get(config, "state", "mode_fields"))
    if not mode_fields:
        mode_fields = base.mode_fields
    current_truth_fields = (
        _as_str_tuple(_get(config, "state", "current_truth_fields")) or base.current_truth_fields
    )

    validation_command = _as_str(_get(config, "validation", "command"), "")
    validation_command_redesign = _as_str(
        _get(config, "validation", "command_in_redesign_repo_from_root"), ""
    )

    profile_status = _as_str(config.get("profile_status"), base.profile_status)

    # Prompt semantic roles (M017-S02): explicit section_roles + front-matter
    # metadata field names. Fall back to the base profile when absent (older
    # v1/draft-v2 configs), preserving current behavior.
    section_roles = _get(config, "prompts", "section_roles")
    if isinstance(section_roles, dict):
        reading_section = _role_section(
            section_roles.get("required_reading"), base.required_reading_section
        )
        self_report_section = _role_section(
            section_roles.get("self_report"), base.self_report_section
        )
        non_goals_section = _role_section(section_roles.get("non_goals"), base.non_goals_section)
        task_section = _role_section(section_roles.get("task"), base.task_section)
        verification_section = _role_section(
            section_roles.get("verification"), base.verification_section
        )
    else:
        reading_section = base.required_reading_section
        self_report_section = base.self_report_section
        non_goals_section = base.non_goals_section
        task_section = base.task_section
        verification_section = base.verification_section

    metadata = _get(config, "prompts", "metadata")
    if isinstance(metadata, dict):
        parse_front_matter = (
            bool(metadata.get("parse_front_matter"))
            if "parse_front_matter" in metadata
            else base.parse_front_matter
        )
        milestone_field = _as_str(
            metadata.get("milestone_field"), base.front_matter_milestone_field
        )
        slice_field = _as_str(metadata.get("slice_field"), base.front_matter_slice_field)
        title_field = _as_str(metadata.get("title_field"), base.front_matter_title_field)
    else:
        parse_front_matter = base.parse_front_matter
        milestone_field = base.front_matter_milestone_field
        slice_field = base.front_matter_slice_field
        title_field = base.front_matter_title_field

    self_report_required_headings = (
        _as_str_tuple(_get(config, "reports", "self_report_required_headings"))
        or base.self_report_required_headings
    )

    automation_boundary = _automation_boundary_from_config(_get(config, "automation_boundary"))
    git_policy = _git_policy_from_config(_get(config, "git_policy"))
    pull_request_policy = _pull_request_policy_from_config(_get(config, "pull_request_policy"))

    # M003-S02 compatibility modes (owner note 008): closed vocabularies. For
    # the new ``pairing``/``discovery`` keys an unknown declared value is an
    # ERROR diagnostic with a fallback to the behavior-preserving default,
    # because a silently mis-typed mode would change pairing or evidence
    # semantics. ``prompts.numbering`` historically carried free advisory
    # prose in accepted configs, so an unrecognized value there keeps the
    # historical silent-ignore behavior; only the exact
    # ``global_flat_sequence`` token activates the global mode.
    def _mode_value(
        label: str,
        raw: object,
        allowed: tuple[str, ...],
        fallback: str,
        *,
        strict: bool,
    ) -> str:
        if raw is None:
            return fallback
        value = _as_str(raw, "")
        if value in allowed:
            return value
        if strict:
            diagnostics.append(
                LayoutDiagnostic(
                    code="unsupported_layout_mode",
                    severity=LayoutDiagnosticSeverity.ERROR,
                    message=(
                        f"layout config {label}={value!r} is not in the closed "
                        f"vocabulary {sorted(allowed)}; falling back to "
                        f"{fallback!r}"
                    ),
                )
            )
        return fallback

    prompt_numbering = _mode_value(
        "prompts.numbering",
        _get(config, "prompts", "numbering"),
        ("per_kind_sequence", "global_flat_sequence"),
        base.prompt_numbering,
        strict=False,
    )
    prompt_pairing = _mode_value(
        "prompts.pairing",
        _get(config, "prompts", "pairing"),
        ("same_sequence", "workflow_metadata"),
        base.prompt_pairing,
        strict=True,
    )
    reports_discovery = _mode_value(
        "reports.discovery",
        _get(config, "reports", "discovery"),
        ("flat", "recursive_contained"),
        base.reports_discovery,
        strict=True,
    )

    # M011-S01 (Prompt 044 Gate C): optional-lane llloom paths flow through one
    # shared typed contract (:func:`normalize_memory_lane_path`) so a configured
    # value can never carry a line break, fence, heading, backtick, control
    # character, or over-length payload into any prompt/handoff consumer. A
    # missing key keeps the profile-specific default. A present-but-unsafe value
    # is rejected with role-specific failure and an owned, bounded, non-echoing
    # warning:
    #   - an unsafe ``posture_file`` becomes the deterministic disabled posture
    #     sentinel ("") so active composition omits/fails closed rather than
    #     citing the profile default as though the override had been accepted;
    #   - an unsafe ``memory_root`` becomes the disable-without-fallback sentinel
    #     ("") so the memory observation is disabled without adopting another root.
    llloom_posture_file = base.llloom_posture_file
    raw_posture = _get(config, "optional_lanes", "llloom", "posture_file")
    if raw_posture is not None:
        normalized_posture = normalize_memory_lane_path(raw_posture)
        if normalized_posture is not None:
            llloom_posture_file = normalized_posture
        else:
            llloom_posture_file = ""
            diagnostics.append(
                LayoutDiagnostic(
                    code="unsafe_memory_posture_path",
                    severity=LayoutDiagnosticSeverity.WARNING,
                    message=(
                        "optional_lanes.llloom.posture_file is not a safe, bounded, "
                        "single-line repository-relative path; disabling the "
                        "configured posture without using the profile default"
                    ),
                )
            )

    llloom_memory_root = base.llloom_memory_root
    raw_root = _get(config, "optional_lanes", "llloom", "memory_root")
    if raw_root is not None:
        normalized_root = normalize_memory_lane_path(raw_root)
        if normalized_root is not None:
            llloom_memory_root = normalized_root
        else:
            llloom_memory_root = ""
            diagnostics.append(
                LayoutDiagnostic(
                    code="unsafe_memory_root",
                    severity=LayoutDiagnosticSeverity.WARNING,
                    message=(
                        "optional_lanes.llloom.memory_root is not a safe, bounded, "
                        "single-line repository-relative path; disabling the llloom "
                        "memory observation without falling back to another root"
                    ),
                )
            )

    diagnostics.extend(_advisory_path_diagnostics(config))

    profile = LayoutProfile(
        schema_version=schema_version,
        profile_id=profile_id,
        template_root=template_root,
        required_directories=required_dirs,
        roadmap_dir=roadmap_dir,
        active_roadmap_glob=active_glob,
        development_roadmap_glob=dev_glob,
        fallback_roadmap_glob=fallback_glob,
        coding_prompt_dir=coding_dir,
        review_prompt_dir=review_dir,
        coding_template=coding_template,
        review_template=review_template,
        self_report_schema=self_report_schema,
        required_coding_prompt_sections=req_coding_sections,
        required_review_prompt_sections=req_review_sections,
        roadmap_item_section=base.roadmap_item_section,
        self_report_section=self_report_section,
        required_reading_section=reading_section,
        non_goals_section=non_goals_section,
        task_section=task_section,
        verification_section=verification_section,
        parse_front_matter=parse_front_matter,
        front_matter_milestone_field=milestone_field,
        front_matter_slice_field=slice_field,
        front_matter_title_field=title_field,
        reviews_dir=reviews_dir,
        self_report_suffix=self_report_suffix,
        review_report_suffix=review_report_suffix,
        verdict_record_suffix=verdict_record_suffix,
        verdict_values=verdict_values,
        self_report_required_headings=self_report_required_headings,
        state_file=state_file,
        mode_fields=mode_fields,
        current_truth_fields=current_truth_fields,
        validation_command=validation_command,
        validation_command_redesign=validation_command_redesign,
        profile_status=profile_status,
        automation_boundary=automation_boundary,
        git_policy=git_policy,
        pull_request_policy=pull_request_policy,
        prompt_numbering=prompt_numbering,
        prompt_pairing=prompt_pairing,
        reports_discovery=reports_discovery,
        llloom_memory_root=llloom_memory_root,
        llloom_posture_file=llloom_posture_file,
        context_filename=context_filename,
        coder_may_create_review_prompt=coder_may_create_review_prompt,
    )
    return profile, tuple(diagnostics)


def _base_profile_for_config(config: dict[str, object]) -> LayoutProfile:
    """Pick the fallback base profile (v2 default, or legacy for the old template).

    A config is treated as legacy when its declared coding-prompt sections include
    the legacy ``Active Roadmap Item`` marker (and not the v2 ``Read First``
    marker), or when its ``profile_id`` names the legacy/root template. Otherwise
    the v2 default base is used, keeping the default v2-oriented.
    """

    sections = {
        normalize_section(s)
        for s in _as_str_tuple(_get(config, "prompts", "required_coding_prompt_sections"))
    }
    profile_id = _as_str(config.get("profile_id")).lower()
    looks_legacy = ("active roadmap item" in sections and "read first" not in sections) or (
        "legacy" in profile_id
    )
    return legacy_profile() if looks_legacy else v2_default_profile()


def _role_section(value: object, fallback: str) -> str:
    """Normalize a configured semantic-role heading, or keep the fallback."""

    if isinstance(value, str) and value.strip():
        return normalize_section(value)
    return fallback


def _automation_boundary_from_config(value: object) -> AutomationBoundaryPolicy:
    if not isinstance(value, dict):
        return _DEFAULT_AUTOMATION_BOUNDARY
    return AutomationBoundaryPolicy(
        runner_implemented=bool(value.get("runner_implemented", False)),
        boundary_doc=_as_str(value.get("boundary_doc")),
        may_consume=_as_str_tuple(value.get("may_consume")),
        must_stop_on=_as_str_tuple(value.get("must_stop_on")),
    )


def _git_policy_from_config(value: object) -> GitPolicy:
    if not isinstance(value, dict):
        return _DEFAULT_GIT_POLICY
    d = _DEFAULT_GIT_POLICY
    return GitPolicy(
        default=_as_str(value.get("default")),
        commit_boundary=_as_str(value.get("commit_boundary")),
        policy_doc=_as_str(value.get("policy_doc")),
        default_committer_role=_as_str(value.get("default_committer_role")),
        coder_may_commit_by_default=bool(value.get("coder_may_commit_by_default", False)),
        architect_reviewer_may_commit_at_boundary=bool(
            value.get("architect_reviewer_may_commit_at_boundary", False)
        ),
        runner_may_commit=bool(value.get("runner_may_commit", False)),
        runner_may_commit_when_explicitly_authorized=bool(
            value.get("runner_may_commit_when_explicitly_authorized", False)
        ),
        runner_may_report_commit_ready=bool(value.get("runner_may_report_commit_ready", False)),
        commit_ready_requires=_as_str_tuple(value.get("commit_ready_requires")),
        before_commit_requires=_as_str_tuple(value.get("before_commit_requires")),
        auto_commit_requires_explicit_configuration=bool(
            value.get(
                "auto_commit_requires_explicit_configuration",
                d.auto_commit_requires_explicit_configuration,
            )
        ),
        must_not_bypass_stop_conditions_to_commit=bool(
            value.get(
                "must_not_bypass_stop_conditions_to_commit",
                d.must_not_bypass_stop_conditions_to_commit,
            )
        ),
    )


def _pull_request_policy_from_config(value: object) -> PullRequestPolicy:
    if not isinstance(value, dict):
        return _DEFAULT_PULL_REQUEST_POLICY
    d = _DEFAULT_PULL_REQUEST_POLICY
    return PullRequestPolicy(
        default=_as_str(value.get("default")),
        suggested_boundary=_as_str(value.get("suggested_boundary")),
        policy_doc=_as_str(value.get("policy_doc")),
        human_may_request_any_time=bool(value.get("human_may_request_any_time", False)),
        runner_may_open_pull_request=bool(value.get("runner_may_open_pull_request", False)),
        runner_may_report_pull_request_ready=bool(
            value.get("runner_may_report_pull_request_ready", False)
        ),
        open_pull_request_requires_explicit_authorization=bool(
            value.get(
                "open_pull_request_requires_explicit_authorization",
                d.open_pull_request_requires_explicit_authorization,
            )
        ),
        must_not_bypass_stop_conditions_to_open_pull_request=bool(
            value.get(
                "must_not_bypass_stop_conditions_to_open_pull_request",
                d.must_not_bypass_stop_conditions_to_open_pull_request,
            )
        ),
    )


def _advisory_path_diagnostics(config: dict[str, object]) -> list[LayoutDiagnostic]:
    """Surface machine-local/absolute advisory paths as non-blocking diagnostics.

    These fields are advisory documentation, not machine rails: ``null`` and safe
    project-relative paths produce no diagnostic; an absolute or escaping path is
    surfaced as INFO and otherwise ignored (never used as a write path).
    """

    diagnostics: list[LayoutDiagnostic] = []
    checks = (
        ("compatibility.frutlups_source", _get(config, "compatibility", "frutlups_source")),
        ("compatibility.guide", _get(config, "compatibility", "guide")),
        (
            "optional_lanes.llloom.install_source",
            _get(config, "optional_lanes", "llloom", "install_source"),
        ),
    )
    for label, value in checks:
        if isinstance(value, str) and value.strip() and not is_safe_relative(value):
            diagnostics.append(
                LayoutDiagnostic(
                    code="advisory_machine_local_path",
                    severity=LayoutDiagnosticSeverity.INFO,
                    message=(
                        f"advisory field {label} is a machine-local/absolute path; "
                        "ignored as advisory metadata (not a frutlups rail)"
                    ),
                )
            )
    return diagnostics


def _mode_fields_from_config(value: object) -> tuple[LayoutModeField, ...]:
    if not isinstance(value, dict):
        return ()
    fields: list[LayoutModeField] = []
    for key, body in value.items():
        if not isinstance(body, dict):
            continue
        fields.append(
            LayoutModeField(
                key=str(key),
                label=_as_str(body.get("label"), str(key)),
                allowed_values=_as_str_tuple(body.get("allowed_values")),
                default=_as_str(body.get("default"), ""),
            )
        )
    return tuple(fields)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Private layout schema over the bounded YAML boundary (M002-S03)
# ---------------------------------------------------------------------------


def _layout_refusal(reason: str) -> LayoutConfigError:
    """A deterministic, bounded, hostile-echo-free, path-safe schema refusal."""

    return LayoutConfigError(f"layout config refused: {reason}")


def _require_layout_string_keys(value: object) -> None:
    """Reject a non-string mapping key at any mapping level of the value."""

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _layout_refusal("mapping keys must be strings at every level")
            _require_layout_string_keys(item)
    elif isinstance(value, list):
        for item in value:
            _require_layout_string_keys(item)


def _layout_config_from_document(document: YamlDocument) -> dict[str, object]:
    """Apply the private layout schema to a bounded :class:`YamlDocument`.

    The layout schema is deliberately narrower than the boundary: features the
    lower boundary can represent safely -- anchors/aliases, merge keys, explicit
    tags, and flow collections -- are unapproved for layout configs and refused
    here from the boundary's descriptive feature record. The root must be
    exactly one mapping with string keys at every mapping level; unknown
    block-form string-keyed fields stay readable and are ignored by
    :func:`profile_from_config` exactly as before.

    This is a layout-specific schema only. It assigns no OKF or profile result
    and borrows no OKF reason codes. Its refusals are deterministic, bounded,
    and free of source paths and hostile scalar or key text.
    """

    features = document.features
    if features.has_merge_keys:
        raise _layout_refusal("merge keys are not approved for layout configs")
    if features.has_anchors or features.has_aliases:
        raise _layout_refusal("anchors and aliases are not approved for layout configs")
    if features.has_explicit_tags:
        raise _layout_refusal("explicit tags are not approved for layout configs")
    if features.has_flow_collections:
        raise _layout_refusal("flow collections are not approved for layout configs")
    value = document.value
    if not isinstance(value, dict):
        raise _layout_refusal("root must be exactly one mapping")
    _require_layout_string_keys(value)
    return value


def load_config_file(path: Path) -> dict[str, object]:
    """Read and parse a layout config file. Raises :class:`LayoutConfigError`.

    The file is loaded exclusively through the private bounded YAML boundary
    (:func:`frutlups._yaml.load_yaml_path`), which owns the byte-first read,
    strict UTF-8 decoding, document count, resource limits, duplicate
    detection, tag policy, aliases, and construction; the private layout
    schema then refuses unapproved features and non-mapping/non-string-key
    shapes. The ``parse_simple_yaml`` compatibility wrapper is never called
    here, and a missing PyYAML install is an invalid installation
    (``ImportError``), not a runtime mode with a custom-parser fallback.

    A :class:`YamlBoundaryError` is mapped to a :class:`LayoutConfigError`
    whose owned message carries only the stable refusal category: never the
    source path, hostile scalar/key text, PyYAML exception text, or a
    traceback.
    """

    return _layout_config_from_boundary(lambda: load_yaml_path(path))


def _layout_config_from_boundary(load: Callable[[], YamlDocument]) -> dict[str, object]:
    """Run one bounded boundary call and apply the private layout schema.

    The single conversion path shared by :func:`load_config_file` and the
    :func:`parse_simple_yaml` compatibility wrapper (M002-S05): exactly one
    boundary call, then exactly one schema application. A
    :class:`YamlBoundaryError` becomes a :class:`LayoutConfigError` whose
    owned message carries only the stable failure category.
    """

    try:
        document = load()
    except YamlBoundaryError as exc:
        raise _layout_refusal(f"yaml boundary refusal: {exc.category.value}") from None
    return _layout_config_from_document(document)


def load_layout_profile(
    root: Path,
    config_path: Path | str | None = None,
) -> LoadedLayout:
    """Select and load the active layout profile for the project at ``root``.

    Precedence:

    1. an explicit ``config_path`` (``--layout-config``);
    2. a ``frutlups.layout.yaml`` at the project root;
    3. a v2 default profile when ``PROJECT_STATE.md`` is present;
    4. otherwise the legacy compatibility-fallback profile.

    Never raises for the discovery path; a bad explicit/looked-up config is
    reported as a diagnostic and the loader falls back to the default profile.
    """

    diagnostics: list[LayoutDiagnostic] = []

    if config_path is not None:
        cfg_path = Path(config_path)
        try:
            config = load_config_file(cfg_path)
        except LayoutConfigError as exc:
            diagnostics.append(
                LayoutDiagnostic(
                    code="config_unreadable",
                    severity=LayoutDiagnosticSeverity.ERROR,
                    message=str(exc),
                )
            )
            return LoadedLayout(
                profile=default_profile(),
                source=ProfileSource.EXPLICIT_CONFIG,
                config_path=str(cfg_path),
                diagnostics=tuple(diagnostics),
            )
        profile, cfg_diags = profile_from_config(config)
        diagnostics.extend(cfg_diags)
        return LoadedLayout(
            profile=profile,
            source=ProfileSource.EXPLICIT_CONFIG,
            config_path=str(cfg_path),
            diagnostics=tuple(diagnostics),
        )

    project_config = root / DEFAULT_CONFIG_FILENAME
    if project_config.is_file():
        try:
            config = load_config_file(project_config)
            profile, cfg_diags = profile_from_config(config)
            diagnostics.extend(cfg_diags)
            return LoadedLayout(
                profile=profile,
                source=ProfileSource.PROJECT_CONFIG,
                config_path=str(project_config),
                diagnostics=tuple(diagnostics),
            )
        except LayoutConfigError as exc:
            diagnostics.append(
                LayoutDiagnostic(
                    code="config_unreadable",
                    severity=LayoutDiagnosticSeverity.ERROR,
                    message=str(exc),
                )
            )

    # No config: choose by template markers. v2 templates carry PROJECT_STATE.md;
    # the legacy/root template does not, so it gets the compatibility profile.
    if (root / "PROJECT_STATE.md").is_file():
        return LoadedLayout(
            profile=v2_default_profile(),
            source=ProfileSource.V2_STATE_DEFAULT,
            config_path="",
            diagnostics=tuple(diagnostics),
        )

    return LoadedLayout(
        profile=legacy_profile(),
        source=ProfileSource.LEGACY_FALLBACK,
        config_path="",
        diagnostics=tuple(diagnostics),
    )


def with_template_root(profile: LayoutProfile, template_root: str) -> LayoutProfile:
    """Return ``profile`` with its ``template_root`` overridden."""

    return replace(profile, template_root=template_root)
