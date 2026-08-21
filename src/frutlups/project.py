"""Project discovery and status reporting."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field as dataclass_field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath

from frutlups._yaml import YamlBoundaryError, YamlDocument, load_yaml_bytes
from frutlups._scaffold import (
    ScaffoldSlot,
    _closing_fence,
    _FENCE_OPEN,
    _is_indented_code,
    _live_markdown_events,
    render_configured_scaffold,
)
from frutlups.artifacts import REQUIRED_DIRECTORIES, TemplatePaths
from frutlups.exceptions import ProjectNotFoundError
from frutlups.layout import (
    LayoutDiagnostic,
    LayoutDiagnosticSeverity,
    LayoutProfile,
    LoadedLayout,
    ProfileSource,
    is_safe_relative,
    legacy_profile,
    load_layout_profile,
    normalize_section,
    resolve_under_root,
)
from frutlups.memory import (
    DisabledMemoryBackend,
    MemoryCommandRunner,
    MemoryStatus,
    build_memory_prompt_snippet,
    detect_memory,
    observe_llloom_memory_root,
)
from frutlups.prompt_template import (
    MAX_PROMPT_SEQUENCE,
    CodingPromptPreview,
    CodingPromptRenderResult,
    CodingPromptTemplate,
    _is_within,
    preview_coding_prompt,
    render_coding_prompt,
)
from frutlups.prompts import (
    PromptArtifact,
    PromptHealth,
    PromptKind,
    compute_prompt_health,
    inventory_prompts,
)
from frutlups.review_prompt_template import (
    ReviewPromptEvidenceCommand,
    ReviewPromptEvidenceResult,
    ReviewPromptPreview,
    ReviewPromptRenderResult,
    ReviewPromptTemplate,
    derive_review_prompt_evidence,
    preview_review_prompt,
    render_review_prompt,
)
from frutlups.review_report import (
    ReviewReportVerdictParseCommand,
    ReviewReportVerdictParseResult,
    ReviewVerdict,
    default_review_report_schema,
    parse_review_report_verdict,
    review_report_format_contract,
)
from frutlups.rework import (
    MAX_REWORK_DECLARATIONS,
    MAX_REWORK_PASS_ID,
    MAX_REWORK_PROMPT_SEQUENCE,
    MAX_REWORK_SLICES,
    REWORK_DECLARATION_COUNT_EXHAUSTED,
    ReworkDeclaration,
    ReworkDeclarationDiagnostic,
    ReworkDeclarationPlan,
    declaration_path,
    load_rework_declarations,
    render_rework_declaration,
)
from frutlups.self_report import (
    SelfReportLocationCommand,
    SelfReportValidationCommand,
    SelfReportValidationResult,
    self_report_format_contract,
    self_report_schema_for_profile,
    validate_expected_self_report,
)
from frutlups.state import (
    Diagnostic,
    DiagnosticSeverity,
    MilestoneStatus,
    NextActionCommand,
    NextActionDecision,
    NextActionKind,
    RoadmapMilestone,
    RoadmapSlice,
    SliceKind,
    classify_slice_kind,
    compute_next_action_from_verdict,
    next_actionable_milestone,
    next_actionable_slice,
    parse_milestones,
    parse_slices,
)

_FRONTIER_ELIGIBLE_STATUSES: frozenset[MilestoneStatus] = frozenset(
    {
        MilestoneStatus.NEEDS_REVIEW,
        MilestoneStatus.ACTIVE,
        MilestoneStatus.PLANNED,
    }
)


SLICE_REVIEW_REPORT_RE = re.compile(
    r"^(?P<milestone>m\d+)_(?P<slice>s\d+)_.*_review_report\.md$",
    re.IGNORECASE,
)

_PROMPT_BACKTICK_RE = re.compile(r"`([^`]+)`")
_PROMPT_MILESTONE_RE = re.compile(
    r"Active\s+roadmap\s+milestone\s*:.*?`([^`]+)`",
    re.IGNORECASE | re.DOTALL,
)
_PROMPT_SLICE_RE = re.compile(
    r"Detailed\s+roadmap\s+slice\s*:.*?`([^`]+)`",
    re.IGNORECASE | re.DOTALL,
)
_PROMPT_SELF_REPORT_PATH_RE = re.compile(
    r"`([^`]*_self_report\.md)`",
    re.IGNORECASE,
)
_CORRECTION_ROUND_STEM_RE = re.compile(
    r"_round_(?P<round>\d{3})$", re.IGNORECASE
)
_MAX_WORKFLOW_ROUND = 999


@dataclass(frozen=True)
class PromptInventory:
    """Count prompt files in known prompt folders."""

    coding_count: int
    review_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "coding_count": self.coding_count,
            "review_count": self.review_count,
        }


@dataclass(frozen=True)
class ProjectLayout:
    """Discovered project layout, including the active layout profile."""

    root: Path
    paths: TemplatePaths
    loaded: LoadedLayout

    @property
    def profile(self) -> LayoutProfile:
        return self.loaded.profile

    @classmethod
    def discover(
        cls,
        start: Path | str = ".",
        layout_config: Path | str | None = None,
    ) -> ProjectLayout:
        discovered_root = find_project_root(Path(start))
        loaded = load_layout_profile(discovered_root, config_path=layout_config)
        effective_root, loaded = _apply_template_root(discovered_root, loaded)
        return cls(
            root=effective_root,
            paths=TemplatePaths(effective_root, profile=loaded.profile),
            loaded=loaded,
        )


MEMORY_MODE_CONTRACT_ID = "frutlups.memory_mode"
MEMORY_MODE_CONTRACT_VERSION = "1"
MEMORY_MODE_SUPPORTED_VERSIONS = (MEMORY_MODE_CONTRACT_VERSION,)
_MEMORY_MODE_VALUES = frozenset({"none", "lightweight", "llloom"})


@dataclass(frozen=True)
class MemoryModeStatus:
    """Versioned declaration fact for external read-only runners.

    ``mode`` is the selected project's declaration, not a backend-availability
    inference. ``memory_root`` is the safe repository-relative reference declared
    for ``llloom`` mode; it is never an absolute resolved machine path. A malformed
    declaration has ``valid=False`` and ``mode=None`` so consumers can refuse
    without guessing. Missing declarations preserve compatibility as valid
    ``none``.
    """

    contract_id: str
    contract_version: str
    valid: bool
    mode: str | None
    memory_root: str | None
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "valid": self.valid,
            "mode": self.mode,
            "memory_root": self.memory_root,
            "diagnostics": list(self.diagnostics),
        }


def _default_memory_mode_status() -> MemoryModeStatus:
    """Compatibility default for direct legacy ``ProjectStatus`` construction."""

    return MemoryModeStatus(
        contract_id=MEMORY_MODE_CONTRACT_ID,
        contract_version=MEMORY_MODE_CONTRACT_VERSION,
        valid=True,
        mode="none",
        memory_root=None,
        diagnostics=(),
    )


@dataclass(frozen=True)
class ProjectStatus:
    """Read-only status for a frutlups-style project."""

    root: Path
    missing_required_directories: tuple[str, ...]
    active_roadmap: Path | None
    detailed_roadmap: Path | None
    milestones: tuple[RoadmapMilestone, ...]
    next_milestone: RoadmapMilestone | None
    slices: tuple[RoadmapSlice, ...]
    accepted_slice_ids: tuple[str, ...]
    next_slice: RoadmapSlice | None
    prompts: PromptInventory
    prompt_artifacts: tuple[PromptArtifact, ...]
    prompt_health: PromptHealth
    memory: MemoryStatus
    diagnostics: tuple[Diagnostic, ...]
    layout: LoadedLayout | None = None
    memory_mode: MemoryModeStatus = dataclass_field(
        default_factory=_default_memory_mode_status
    )

    @property
    def ok(self) -> bool:
        return not self.missing_required_directories

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "ok": self.ok,
            "missing_required_directories": list(self.missing_required_directories),
            "active_roadmap": str(self.active_roadmap) if self.active_roadmap else None,
            "detailed_roadmap": (str(self.detailed_roadmap) if self.detailed_roadmap else None),
            "milestones": [milestone.to_dict() for milestone in self.milestones],
            "next_milestone": (
                self.next_milestone.to_dict() if self.next_milestone is not None else None
            ),
            "slices": [slc.to_dict() for slc in self.slices],
            "accepted_slice_ids": list(self.accepted_slice_ids),
            "next_slice": (self.next_slice.to_dict() if self.next_slice is not None else None),
            "prompts": self.prompts.to_dict(),
            "prompt_artifacts": [artifact.to_dict() for artifact in self.prompt_artifacts],
            "prompt_health": self.prompt_health.to_dict(),
            "memory": self.memory.to_dict(),
            "memory_mode": self.memory_mode.to_dict(),
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "layout": self.layout.to_dict() if self.layout is not None else None,
        }


@dataclass(frozen=True)
class LoopFrontier:
    """Read-only artifact-inferred frontier for the next loop iteration."""

    root: Path
    active_roadmap: Path | None
    detailed_roadmap: Path | None
    authored_next_milestone: RoadmapMilestone | None
    authored_next_slice: RoadmapSlice | None
    inferred_milestone: RoadmapMilestone | None
    inferred_slice: RoadmapSlice | None
    accepted_slice_ids: tuple[str, ...]
    prompt_health: PromptHealth
    memory: MemoryStatus
    diagnostics: tuple[Diagnostic, ...]
    action: str

    @property
    def slice_kind(self) -> SliceKind:
        """Work classification derived from the inferred frontier slice."""
        if self.inferred_slice is not None:
            return classify_slice_kind(self.inferred_slice.milestone_id)
        return SliceKind.NORMAL

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "active_roadmap": (
                str(self.active_roadmap) if self.active_roadmap is not None else None
            ),
            "detailed_roadmap": (
                str(self.detailed_roadmap) if self.detailed_roadmap is not None else None
            ),
            "authored_next_milestone": (
                self.authored_next_milestone.to_dict()
                if self.authored_next_milestone is not None
                else None
            ),
            "authored_next_slice": (
                self.authored_next_slice.to_dict() if self.authored_next_slice is not None else None
            ),
            "inferred_milestone": (
                self.inferred_milestone.to_dict() if self.inferred_milestone is not None else None
            ),
            "inferred_slice": (
                self.inferred_slice.to_dict() if self.inferred_slice is not None else None
            ),
            "accepted_slice_ids": list(self.accepted_slice_ids),
            "prompt_health": self.prompt_health.to_dict(),
            "memory": self.memory.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "action": self.action,
            "slice_kind": self.slice_kind.value,
        }


@dataclass(frozen=True)
class CodingPromptPlan:
    """Read-only plan for writing a coding prompt for the current frontier."""

    frontier: LoopFrontier
    sequence: int | None
    slug: str
    valid: bool
    errors: tuple[str, ...]
    template: CodingPromptTemplate | None
    render: CodingPromptRenderResult | None
    preview: CodingPromptPreview | None
    coding_prompt_dir: str = "prompts/for_coding_agent"

    def to_dict(self) -> dict[str, object]:
        return {
            "frontier": self.frontier.to_dict(),
            "sequence": self.sequence,
            "slug": self.slug,
            "valid": self.valid,
            "errors": list(self.errors),
            "template": (self.template.to_dict() if self.template is not None else None),
            "render": (self.render.to_dict() if self.render is not None else None),
            "preview": (self.preview.to_dict() if self.preview is not None else None),
            "coding_prompt_dir": self.coding_prompt_dir,
        }


@dataclass(frozen=True)
class CodingPromptMeta:
    """Parsed or derived metadata from a coding prompt artifact file.

    Used by :func:`build_review_prompt_plan` to construct the
    :class:`~frutlups.review_prompt_template.ReviewPromptTemplate` for the
    matching review prompt. ``valid`` is ``True`` iff ``errors`` is empty.
    """

    sequence: int
    milestone_id: str
    slice_id: str
    title: str
    slug: str
    required_reading: tuple[str, ...]
    coding_prompt_path: str
    self_report_path: str
    review_output_path: str
    non_goals: tuple[str, ...]
    errors: tuple[str, ...]
    valid: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "milestone_id": self.milestone_id,
            "slice_id": self.slice_id,
            "title": self.title,
            "slug": self.slug,
            "required_reading": list(self.required_reading),
            "coding_prompt_path": self.coding_prompt_path,
            "self_report_path": self.self_report_path,
            "review_output_path": self.review_output_path,
            "non_goals": list(self.non_goals),
            "errors": list(self.errors),
            "valid": self.valid,
        }


@dataclass(frozen=True)
class ReviewPromptPlan:
    """Read-only plan for writing a review prompt for an in-flight coding prompt.

    All fields that depend on later pipeline stages are ``None`` when an
    earlier stage fails.  ``valid`` is ``True`` iff ``errors`` is empty
    and the plan can be handed to
    :class:`~frutlups.review_prompt_template.ReviewPromptWriteCommand`.
    ``to_dict()`` emits only plain Python values so it is safe to pass
    directly to :func:`json.dumps`.
    """

    frontier: LoopFrontier
    sequence: int | None
    slug: str
    valid: bool
    errors: tuple[str, ...]
    selected_coding_prompt: PromptArtifact | None
    coding_prompt_meta: CodingPromptMeta | None
    self_report: SelfReportValidationResult | None
    evidence: ReviewPromptEvidenceResult | None
    template: ReviewPromptTemplate | None
    render: ReviewPromptRenderResult | None
    preview: ReviewPromptPreview | None
    review_prompt_dir: str = "prompts/for_review_agent"

    def to_dict(self) -> dict[str, object]:
        return {
            "frontier": self.frontier.to_dict(),
            "sequence": self.sequence,
            "slug": self.slug,
            "valid": self.valid,
            "errors": list(self.errors),
            "review_prompt_dir": self.review_prompt_dir,
            "selected_coding_prompt": (
                self.selected_coding_prompt.to_dict()
                if self.selected_coding_prompt is not None
                else None
            ),
            "coding_prompt_meta": (
                self.coding_prompt_meta.to_dict() if self.coding_prompt_meta is not None else None
            ),
            "self_report": (
                {"validation": self.self_report.to_dict()} if self.self_report is not None else None
            ),
            "evidence": (self.evidence.to_dict() if self.evidence is not None else None),
            "template": (self.template.to_dict() if self.template is not None else None),
            "render": (self.render.to_dict() if self.render is not None else None),
            "preview": (self.preview.to_dict() if self.preview is not None else None),
        }


def find_project_root(start: Path) -> Path:
    """Find the nearest parent that looks like the artifact-first template."""

    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if _looks_like_project_root(candidate):
            return candidate

    raise ProjectNotFoundError(
        f"Could not find a frutlups project root above {start!s}. "
        f"Expected directories include: {', '.join(REQUIRED_DIRECTORIES)}."
    )


def _apply_template_root(
    discovered_root: Path,
    loaded: LoadedLayout,
) -> tuple[Path, LoadedLayout]:
    """Resolve the effective template root from ``profile.template_root``.

    The base directory is the config file's parent when a config was loaded
    (project ``frutlups.layout.yaml`` or an explicit ``--layout-config``), else
    the discovered project root. ``template_root: "."`` keeps the base directory
    (the normal v2 GitHub-template case). A safe non-dot relative value (for
    wrapper/redesign repos or local development layouts) resolves the effective
    root under ``base / template_root``. An unsafe value (absolute or ``..``
    escape) is rejected with a diagnostic and the base directory is used.
    """

    template_root = loaded.profile.template_root or "."
    base_dir = Path(loaded.config_path).parent if loaded.config_path else discovered_root

    if template_root in ("", "."):
        return base_dir, loaded

    if not is_safe_relative(template_root):
        diag = LayoutDiagnostic(
            code="unsafe_template_root",
            severity=LayoutDiagnosticSeverity.ERROR,
            message=(
                f"layout config template_root={template_root!r} is absolute or escapes "
                "the config directory; using the config directory as the template root"
            ),
        )
        loaded = replace(loaded, diagnostics=loaded.diagnostics + (diag,))
        return base_dir, loaded

    effective_root = base_dir / PurePosixPath(template_root)
    return effective_root, loaded


_LAYOUT_SEVERITY_MAP: dict[LayoutDiagnosticSeverity, DiagnosticSeverity] = {
    LayoutDiagnosticSeverity.ERROR: DiagnosticSeverity.ERROR,
    LayoutDiagnosticSeverity.WARNING: DiagnosticSeverity.WARNING,
    LayoutDiagnosticSeverity.INFO: DiagnosticSeverity.INFO,
}


def _layout_status_diagnostics(
    layout: ProjectLayout, state_obs: _StateObservation
) -> list[Diagnostic]:
    """Surface layout/profile config issues and v2 state checks as diagnostics.

    Includes config-load diagnostics (schema-version and unsafe-path warnings),
    missing required directories under the selected profile, and v2
    ``PROJECT_STATE.md`` presence and controlled-mode-field violations.

    ``state_obs`` is the single selected read of the profile's state file
    (M011-S01): this function consults it rather than reading the file again, so
    diagnostics and memory selection share one file read and one parse.
    """

    diagnostics: list[Diagnostic] = []
    loaded = layout.loaded
    profile = loaded.profile

    for diag in loaded.diagnostics:
        diagnostics.append(
            Diagnostic(
                code=f"layout_{diag.code}",
                severity=_LAYOUT_SEVERITY_MAP.get(diag.severity, DiagnosticSeverity.WARNING),
                message=diag.message,
            )
        )

    # The auto-detected legacy compatibility fallback must stay behavior-compatible
    # with pre-profile frutlups: do not emit new directory/state diagnostics for it.
    # (Missing required directories remain reported via missing_required_directories.)
    if loaded.source == ProfileSource.LEGACY_FALLBACK:
        return diagnostics

    for name in layout.paths.required_missing:
        diagnostics.append(
            Diagnostic(
                code="layout_missing_required_directory",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    f"required directory {name!r} for profile {profile.profile_id!r} is missing"
                ),
            )
        )

    if profile.state_file:
        if not state_obs.present:
            diagnostics.append(
                Diagnostic(
                    code="layout_state_file_missing",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"profile {profile.profile_id!r} expects state file "
                        f"{profile.state_file!r} but it is missing"
                    ),
                )
            )
        elif not state_obs.readable:
            diagnostics.append(
                Diagnostic(
                    code="layout_state_file_unreadable",
                    severity=DiagnosticSeverity.WARNING,
                    message=f"could not read state file {profile.state_file!r}: {state_obs.error}",
                )
            )
        else:
            diagnostics.extend(_state_mode_diagnostics(state_obs, profile))

    return diagnostics


# ---------------------------------------------------------------------------
# M002-S04: selected-layout mutation safety (one private typed policy)
# ---------------------------------------------------------------------------


def _layout_mutation_blockers(layout: LoadedLayout | None) -> tuple[LayoutDiagnostic, ...]:
    """The error-severity selected-layout diagnostics that block mutation.

    The typed severity on the already selected :class:`LoadedLayout` is the
    only policy input: mutation is blocked if and only if at least one
    selected-layout diagnostic has ``LayoutDiagnosticSeverity.ERROR``.
    Warning- and info-only layouts never block, and unrelated roadmap,
    prompt-health, memory, journal, or native-artifact diagnostics (which
    live on :class:`ProjectStatus`, not on the selected layout) are ignored
    by construction. The configuration is never reparsed here and no raw
    YAML, fallback profile id, or message substring is consulted.
    """

    if layout is None:
        return ()
    return tuple(
        diag for diag in layout.diagnostics if diag.severity == LayoutDiagnosticSeverity.ERROR
    )


def _layout_blocker_codes(blockers: tuple[LayoutDiagnostic, ...]) -> str:
    """The stable, sorted, distinct diagnostic codes of a blocker set."""

    return ", ".join(sorted({diag.code for diag in blockers}))


def _layout_mutation_refusal_message(blockers: tuple[LayoutDiagnostic, ...]) -> str:
    """The named, deterministic, bounded mutation refusal text (M002-S04).

    Owned wording plus stable diagnostic codes only: never a config path,
    hostile YAML content, PyYAML exception text, or a traceback.
    """

    return (
        "layout mutation refused: selected layout has error-severity diagnostics "
        f"({_layout_blocker_codes(blockers)}); refusing before any write; "
        "read-only fallback orientation remains available"
    )


def _layout_fallback_label_message(blockers: tuple[LayoutDiagnostic, ...]) -> str:
    """The clearly labeled read-only fallback observation text (M002-S04).

    Owned wording plus stable diagnostic codes only, like the refusal text.
    """

    return (
        "layout fallback active: read-only orientation only, mutation not "
        f"authorized (error-severity layout diagnostics: {_layout_blocker_codes(blockers)})"
    )


@dataclass(frozen=True)
class _StateObservation:
    """One selected read+parse of the profile's state file (M011-S01).

    Built at most once per composition and shared between the state-mode
    diagnostics and the mode-aware memory selection so a single invocation reads
    and parses ``PROJECT_STATE.md`` exactly once. ``configured`` is True when the
    selected profile declares a ``state_file``; ``present``/``readable`` describe
    that file; ``values`` maps ``lower(label) -> value`` for the controlled mode
    lines; ``error`` carries a bounded OS-error string for the unreadable case.
    A profile with no state contract (legacy fallback) yields
    ``configured=False`` and consults no file.
    """

    configured: bool
    present: bool
    readable: bool
    values: dict[str, str]
    counts: dict[str, int]
    error: str = ""


_STATE_FILE_MAX_BYTES = 262_144
_STATE_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
)


class _StateReadError(Exception):
    """Owned internal refusal from the bounded state-file reader."""


def _unreadable_state_observation(message: str) -> _StateObservation:
    """One owned failure value for a configured state path."""

    return _StateObservation(
        configured=True,
        present=True,
        readable=False,
        values={},
        counts={},
        error=message,
    )


def _read_state_file_once(path: Path) -> str:
    """Open one regular file once and return at most the bounded UTF-8 text."""

    try:
        before = os.stat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _STATE_FILE_MAX_BYTES:
            raise _StateReadError
        descriptor = os.open(path, _STATE_OPEN_FLAGS)
    except (OSError, ValueError):
        raise _StateReadError from None

    handle = None
    try:
        opened = os.fstat(descriptor)
        if (
            stat.S_ISREG(opened.st_mode)
            and opened.st_size <= _STATE_FILE_MAX_BYTES
            and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)
        ):
            handle = os.fdopen(descriptor, "rb")
    except (OSError, ValueError):
        pass
    finally:
        if handle is None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if handle is None:
        raise _StateReadError

    try:
        with handle:
            data = handle.read(_STATE_FILE_MAX_BYTES + 1)
    except (OSError, ValueError):
        raise _StateReadError from None
    if len(data) > _STATE_FILE_MAX_BYTES:
        raise _StateReadError
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _StateReadError from None


def _observe_state(root: Path, profile: LayoutProfile) -> _StateObservation:
    """Read one bounded, contained, regular UTF-8 state-file snapshot."""

    if not profile.state_file:
        return _StateObservation(
            configured=False, present=False, readable=False, values={}, counts={}
        )
    try:
        state_path = resolve_under_root(root, profile.state_file)
    except ValueError:
        return _unreadable_state_observation("state file path is unsafe")

    try:
        exists = state_path.exists()
        link_like = state_path.is_symlink()
    except (OSError, RuntimeError):
        return _unreadable_state_observation("state file could not be observed")
    if not exists and not link_like:
        return _StateObservation(
            configured=True, present=False, readable=False, values={}, counts={}
        )
    if not exists:
        return _unreadable_state_observation("state file target is unavailable")

    try:
        root_real = root.resolve(strict=True)
        state_real = state_path.resolve(strict=True)
        state_real.relative_to(root_real)
    except (OSError, RuntimeError, ValueError):
        return _unreadable_state_observation("state file could not be opened safely")

    try:
        text = _read_state_file_once(state_real)
    except _StateReadError:
        return _unreadable_state_observation("state file could not be read safely")

    values, counts = _parse_state_mode_entries(text)
    return _StateObservation(
        configured=True,
        present=True,
        readable=True,
        values=values,
        counts=counts,
    )


def _state_mode_diagnostics(
    state_obs: _StateObservation, profile: LayoutProfile
) -> list[Diagnostic]:
    """Check controlled mode fields in a v2 PROJECT_STATE.md against the profile.

    Consumes the single selected :class:`_StateObservation` (M011-S01); it does
    not read the state file again.
    """

    diagnostics: list[Diagnostic] = []
    values = state_obs.values
    for mode in profile.mode_fields:
        label = mode.label.lower()
        if state_obs.counts.get(label, 0) > 1:
            diagnostics.append(
                Diagnostic(
                    code="layout_state_mode_duplicate",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"state file repeats controlled mode field {mode.label!r}; "
                        "the declaration is ambiguous"
                    ),
                )
            )
            continue
        if label not in values:
            diagnostics.append(
                Diagnostic(
                    code="layout_state_mode_missing",
                    severity=DiagnosticSeverity.WARNING,
                    message=(f"state file is missing controlled mode field {mode.label!r}"),
                )
            )
            continue
        value = values[label]
        if mode.allowed_values and value not in mode.allowed_values:
            diagnostics.append(
                Diagnostic(
                    code="layout_state_mode_invalid",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"state field {mode.label!r} value {value!r} is not one of "
                        f"{list(mode.allowed_values)}"
                    ),
                )
            )
    return diagnostics


def _parse_state_mode_values(text: str) -> dict[str, str]:
    """Extract ``Label: value`` and ``Label:`` + ``- value`` pairs from state text.

    Keys are lowercased labels. Supports both inline (``Memory mode: none``) and
    the v2 list form (``Memory mode:`` on one line, ``- none`` on the next).
    """

    values, _counts = _parse_state_mode_entries(text)
    return values


def _parse_state_mode_entries(text: str) -> tuple[dict[str, str], dict[str, int]]:
    """Return normalized values and occurrence counts from one state snapshot."""

    values: dict[str, str] = {}
    counts: dict[str, int] = {}
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        label, _, rest = stripped.partition(":")
        label = label.strip().lower().lstrip("-* ").strip()
        counts[label] = counts.get(label, 0) + 1
        rest = rest.strip()
        if rest:
            values[label] = rest
            continue
        # Value may follow as the next bullet line.
        for follow in lines[idx + 1 :]:
            fstrip = follow.strip()
            if not fstrip:
                continue
            if fstrip.startswith(("-", "*")):
                values[label] = fstrip.lstrip("-* ").strip()
            break
    return values, counts


def _invalid_memory_mode_status(code: str) -> MemoryModeStatus:
    """Return one bounded fail-closed declaration observation."""

    return MemoryModeStatus(
        contract_id=MEMORY_MODE_CONTRACT_ID,
        contract_version=MEMORY_MODE_CONTRACT_VERSION,
        valid=False,
        mode=None,
        memory_root=None,
        diagnostics=(code,),
    )


def _memory_mode_status(
    loaded: LoadedLayout,
    state_obs: _StateObservation,
) -> MemoryModeStatus:
    """Derive the external declaration contract from one selected snapshot."""

    if any(diag.code == "config_unreadable" for diag in loaded.diagnostics):
        return _invalid_memory_mode_status("layout_unreadable")

    profile = loaded.profile
    memory_field = next((field for field in profile.mode_fields if field.key == "memory"), None)
    mode: str | None
    if loaded.source == ProfileSource.LEGACY_FALLBACK or memory_field is None:
        mode = "none"
    elif state_obs.error:
        return _invalid_memory_mode_status("state_unreadable")
    elif not state_obs.present:
        mode = "none"
    else:
        label = memory_field.label.lower()
        count = state_obs.counts.get(label, 0)
        if count == 0:
            mode = "none"
        elif count != 1:
            return _invalid_memory_mode_status("duplicate_memory_mode")
        else:
            mode = state_obs.values.get(label)
            if (
                mode not in _MEMORY_MODE_VALUES
                or (memory_field.allowed_values and mode not in memory_field.allowed_values)
            ):
                return _invalid_memory_mode_status("invalid_memory_mode")

    memory_root: str | None = None
    if mode == "llloom":
        if not profile.llloom_memory_root or any(
            diag.code == "unsafe_memory_root" for diag in loaded.diagnostics
        ):
            return _invalid_memory_mode_status("invalid_memory_root")
        memory_root = profile.llloom_memory_root

    return MemoryModeStatus(
        contract_id=MEMORY_MODE_CONTRACT_ID,
        contract_version=MEMORY_MODE_CONTRACT_VERSION,
        valid=True,
        mode=mode,
        memory_root=memory_root,
        diagnostics=(),
    )


def build_memory_mode_status(
    start: Path | str = ".",
    layout_config: Path | str | None = None,
) -> MemoryModeStatus:
    """Read the versioned memory declaration without probing a memory backend."""

    layout = ProjectLayout.discover(start, layout_config=layout_config)
    observation = _observe_state(layout.root, layout.loaded.profile)
    return _memory_mode_status(layout.loaded, observation)


# ---------------------------------------------------------------------------
# M011-S01: mode-aware selected-snapshot memory observation (D1)
# ---------------------------------------------------------------------------

_LIGHTWEIGHT_MEMORY_MESSAGE = (
    "lightweight project-managed memory posture; no llloom runner"
)
_LLLOOM_UNAVAILABLE_DIAGNOSTIC = (
    "configured llloom memory root is unavailable; memory disabled"
)


def _select_memory_status(
    root: Path,
    loaded: LoadedLayout | None,
    state_obs: _StateObservation,
    runner: MemoryCommandRunner | None,
) -> MemoryStatus:
    """Return the memory observation for the selected layout and state (M011-S01).

    Authority order, per the M011-S01 mode-aware Option B+ decision:

    1. the already-selected state mode decides whether memory is active;
    2. the already-selected typed layout supplies the safe repo-relative root
       and posture artifacts;
    3. filesystem observation only *confirms availability* for an already-active
       ``llloom`` mode — it never activates a mode by itself; and
    4. posture markdown is never parsed as a second state store.

    Genuine legacy fallback (no state contract) preserves the historical
    :func:`detect_memory` root sniff. The compatibility wrapper is never called
    blindly for a selected v2/template-v3 profile.
    """

    profile = loaded.profile if loaded is not None else legacy_profile()
    source = loaded.source if loaded is not None else None

    if source == ProfileSource.LEGACY_FALLBACK or not profile.state_file:
        # Genuine legacy fallback / no declared state contract: preserve the
        # historical direct-wrapper root-sniff compatibility behavior.
        return detect_memory(root, runner=runner)

    memory_field = next((m for m in profile.mode_fields if m.key == "memory"), None)
    if memory_field is None:
        return DisabledMemoryBackend().status()
    allowed = memory_field.allowed_values
    label = memory_field.label.lower()
    if not state_obs.readable or state_obs.counts.get(label, 0) != 1:
        return DisabledMemoryBackend().status()
    mode = state_obs.values.get(label)

    if mode is None or (allowed and mode not in allowed):
        # Missing or invalid controlled memory mode: no backend execution. The
        # state warning is preserved separately via _state_mode_diagnostics; the
        # memory block is a deterministic disabled observation (byte-identical to
        # the historical disabled block, so default output is unchanged).
        return DisabledMemoryBackend().status()

    if mode == "none":
        # Disabled; ignore all memory directories; runner unreachable.
        return DisabledMemoryBackend().status()

    if mode == "lightweight":
        # Active project-managed lightweight posture; no llloom runner call.
        return MemoryStatus(
            enabled=True,
            backend="lightweight",
            root=None,
            message=_LIGHTWEIGHT_MEMORY_MESSAGE,
            diagnostics=(),
        )

    if mode == "llloom":
        return _select_llloom_memory_status(root, profile, runner)

    return DisabledMemoryBackend().status()


def _select_llloom_memory_status(
    root: Path,
    profile: LayoutProfile,
    runner: MemoryCommandRunner | None,
) -> MemoryStatus:
    """Observe the selected ``llloom`` memory root, confirming safe containment.

    Uses the existing path-safety/containment primitives. An absolute, escaping,
    empty-after-normalization, unresolvable, non-directory, or resolved
    link/junction-escaping root disables the observation with one bounded owned
    diagnostic and leaves the runner unreachable; it never silently falls back to
    a different root. The diagnostic never echoes the configured value.
    """

    unavailable = MemoryStatus(
        enabled=False,
        backend="llloom",
        root=None,
        message="",
        diagnostics=(_LLLOOM_UNAVAILABLE_DIAGNOSTIC,),
    )

    rel = profile.llloom_memory_root
    if not rel:
        # Empty sentinel: an explicitly-configured unsafe root was normalized to
        # "disable without fallback" at profile-load time.
        return unavailable

    try:
        resolved = resolve_under_root(root, rel)
    except ValueError:
        return unavailable

    # M011-S01 (Prompt 044 Gate B): contain every documented data-induced
    # resolution failure at this boundary. ``Path.resolve`` and ``Path.is_dir``
    # can raise ``OSError`` (e.g. a broken configured target) or ``RuntimeError``
    # (e.g. an infinite symlink loop); both must fail closed to the owned
    # unavailable observation with the runner unreachable. The except set is the
    # documented resolver-failure domain only — a broad ``except Exception`` is
    # deliberately avoided so genuine programming errors still surface.
    try:
        real = resolved.resolve()
        root_real = root.resolve()
        is_dir = resolved.is_dir()
    except (OSError, RuntimeError):
        return unavailable

    if not is_dir:
        return unavailable

    # Reject a resolved symlink/junction that escapes the project root. A
    # non-containment is a ``ValueError`` from ``relative_to`` (a control-flow
    # signal here, not an error condition).
    try:
        real.relative_to(root_real)
    except ValueError:
        return unavailable

    return observe_llloom_memory_root(resolved, runner)


def build_status(
    start: Path | str = ".",
    memory_runner: MemoryCommandRunner | None = None,
    layout_config: Path | str | None = None,
) -> ProjectStatus:
    """Build read-only project status from repository artifacts.

    ``memory_runner`` is passed to :func:`detect_memory` and allows tests to
    inject a fake runner so they do not require ``llloom`` to be installed.
    When ``None`` (the default), ``detect_memory`` uses a live subprocess runner.

    ``layout_config`` selects an explicit layout config file (``--layout-config``);
    when ``None`` the profile is auto-detected (project ``frutlups.layout.yaml``,
    else a v2 default when ``PROJECT_STATE.md`` is present, else the legacy
    compatibility fallback).

    Performs exactly one acceptance-evidence scan (M003, Prompt 031); the
    private composition :func:`_build_status_with_evidence` additionally
    returns that selected snapshot so composite consumers never scan twice.
    """

    status, _evidence = _build_status_with_evidence(
        start, memory_runner=memory_runner, layout_config=layout_config
    )
    return status


def _build_status_with_evidence(
    start: Path | str = ".",
    memory_runner: MemoryCommandRunner | None = None,
    layout_config: Path | str | None = None,
) -> "tuple[ProjectStatus, _AcceptanceEvidence]":
    """One selected status plus the exact acceptance evidence it used.

    Private single-selection seam (Prompt 031): the layout is selected once
    and ``_collect_acceptance_evidence`` runs exactly once; the returned
    :class:`ProjectStatus` derives ``accepted_slice_ids`` and ``next_slice``
    from that same private snapshot, which composite callers thread into
    resume, gate, planning-frontier, orchestrator, and verdict-record
    planning so one emitted response never combines two evidence snapshots.
    Before return, the exact private evidence object is enriched once with its
    immutable rework-resolution result. No public dataclass field, JSON key,
    mutable cache, or global is added.
    """

    layout = ProjectLayout.discover(start, layout_config=layout_config)
    paths = layout.paths
    # M011-S01: one selected read+parse of the state file, shared between the
    # state-mode diagnostics and the mode-aware memory selection below.
    state_profile = layout.loaded.profile if layout.loaded is not None else legacy_profile()
    state_obs = _observe_state(layout.root, state_profile)
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_layout_status_diagnostics(layout, state_obs))

    active_candidates = paths.active_roadmaps
    active_roadmap = _select_active_roadmap(active_candidates)
    if active_roadmap is None:
        diagnostics.append(
            Diagnostic(
                code="no_active_roadmap",
                severity=DiagnosticSeverity.ERROR,
                message=(
                    "No active roadmap found under 03_experiments/ matching active_roadmap*.md."
                ),
            )
        )
    elif len(active_candidates) > 1:
        diagnostics.append(
            Diagnostic(
                code="multiple_active_roadmaps",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "Multiple active roadmap candidates found in "
                    f"03_experiments/ ({_format_names(active_candidates)}). "
                    f"Using {active_roadmap.name}."
                ),
            )
        )

    detailed_candidates = paths.detailed_roadmaps
    detailed_roadmap = _select_detailed_roadmap(detailed_candidates)
    if detailed_roadmap is None:
        diagnostics.append(
            Diagnostic(
                code="no_detailed_roadmap",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "No detailed roadmap found under 03_experiments/ matching "
                    "development_roadmap*.md."
                ),
            )
        )
    elif len(detailed_candidates) > 1:
        diagnostics.append(
            Diagnostic(
                code="multiple_detailed_roadmaps",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "Multiple detailed roadmap candidates found in "
                    f"03_experiments/ ({_format_names(detailed_candidates)}). "
                    f"Using {detailed_roadmap.name}."
                ),
            )
        )

    milestones = parse_milestones(active_roadmap) if active_roadmap is not None else ()
    if active_roadmap is not None and not milestones:
        diagnostics.append(
            Diagnostic(
                code="no_milestones_parsed",
                severity=DiagnosticSeverity.ERROR,
                message=(f"Active roadmap {active_roadmap.name} contains no parseable milestones."),
            )
        )

    for milestone in milestones:
        if milestone.status == MilestoneStatus.UNKNOWN:
            diagnostics.append(
                Diagnostic(
                    code="unknown_milestone_status",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"Milestone {milestone.milestone_id} has an unknown "
                        "status in the active roadmap."
                    ),
                )
            )

    next_milestone = next_actionable_milestone(milestones)
    slices = parse_slices(detailed_roadmap) if detailed_roadmap is not None else ()
    # M003-S05: the selected profile's typed evidence is the single runtime
    # authority for accepted slice IDs — no legacy parallel scan. Prompt 031:
    # this is the one acceptance scan of the composition.
    selected_profile = (
        layout.loaded.profile if layout.loaded is not None else legacy_profile()
    )
    evidence = _collect_acceptance_evidence(layout.root, selected_profile)
    accepted_slice_ids = evidence.accepted_slice_ids
    prompt_artifacts = inventory_prompts(paths.prompts)
    rework = _resolve_rework(
        layout.root,
        slices,
        accepted_slice_ids,
        prompt_artifacts,
        selected_profile,
        evidence,
    )
    _finalize_rework_resolution(evidence, rework)
    for code, message in rework.diagnostics:
        diagnostics.append(
            Diagnostic(
                code=code,
                severity=DiagnosticSeverity.ERROR,
                message=message,
            )
        )
    next_slice: RoadmapSlice | None = None
    if rework.pending_slice is not None:
        next_slice = rework.pending_slice
        next_milestone = next(
            (
                milestone
                for milestone in milestones
                if milestone.milestone_id.upper() == next_slice.milestone_id.upper()
            ),
            None,
        )
    elif not rework.diagnostics and next_milestone is not None and detailed_roadmap is not None:
        target = next_milestone.milestone_id.upper()
        milestone_slices = [slc for slc in slices if slc.milestone_id.upper() == target]
        if not milestone_slices:
            diagnostics.append(
                Diagnostic(
                    code="next_milestone_has_no_slices",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"Next milestone {next_milestone.milestone_id} has no "
                        f"slices in {detailed_roadmap.name}."
                    ),
                )
            )
        else:
            next_slice = next_actionable_slice(
                slices, next_milestone.milestone_id, accepted_slice_ids
            )
            if next_slice is None:
                diagnostics.append(
                    Diagnostic(
                        code="next_slice_unavailable_all_accepted",
                        severity=DiagnosticSeverity.INFO,
                        message=(
                            "All slices for next milestone "
                            f"{next_milestone.milestone_id} appear accepted; "
                            "no next slice can be inferred."
                        ),
                    )
                )

    health_profile = layout.loaded.profile if layout.loaded is not None else None
    status = ProjectStatus(
        root=layout.root,
        missing_required_directories=paths.required_missing,
        active_roadmap=active_roadmap,
        detailed_roadmap=detailed_roadmap,
        milestones=milestones,
        next_milestone=next_milestone,
        slices=slices,
        accepted_slice_ids=accepted_slice_ids,
        next_slice=next_slice,
        prompts=_inventory_prompts(paths),
        prompt_artifacts=prompt_artifacts,
        prompt_health=compute_prompt_health(
            prompt_artifacts,
            numbering=(
                health_profile.prompt_numbering
                if health_profile is not None
                else "per_kind_sequence"
            ),
            pairing=(
                health_profile.prompt_pairing
                if health_profile is not None
                else "same_sequence"
            ),
        ),
        memory=_select_memory_status(
            layout.root, layout.loaded, state_obs, memory_runner
        ),
        memory_mode=_memory_mode_status(layout.loaded, state_obs),
        diagnostics=tuple(diagnostics),
        layout=layout.loaded,
    )
    return status, evidence


def _build_frontier_from_status(status: ProjectStatus) -> LoopFrontier:
    """Build a LoopFrontier from an already-built ProjectStatus."""

    diagnostics: list[Diagnostic] = list(status.diagnostics)
    inferred_milestone: RoadmapMilestone | None = None
    inferred_slice: RoadmapSlice | None = None
    action: str

    if status.next_slice is not None:
        inferred_milestone = status.next_milestone
        inferred_slice = status.next_slice
        action = f"next slice: {inferred_slice.slice_id} - {inferred_slice.title}"
    else:
        found = False
        for milestone in status.milestones:
            if milestone.status not in _FRONTIER_ELIGIBLE_STATUSES:
                continue
            candidate = next_actionable_slice(
                status.slices, milestone.milestone_id, status.accepted_slice_ids
            )
            if candidate is not None:
                inferred_milestone = milestone
                inferred_slice = candidate
                action = f"inferred next: {inferred_slice.slice_id} (authored milestone exhausted)"
                found = True
                break
        if not found:
            action = (
                "no frontier slice found; all roadmap slices may be accepted or roadmap is empty"
            )
            diagnostics.append(
                Diagnostic(
                    code="no_frontier_slice",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        "No unaccepted slice found in any needs_review,"
                        " active, or planned milestone."
                    ),
                )
            )

    return LoopFrontier(
        root=status.root,
        active_roadmap=status.active_roadmap,
        detailed_roadmap=status.detailed_roadmap,
        authored_next_milestone=status.next_milestone,
        authored_next_slice=status.next_slice,
        inferred_milestone=inferred_milestone,
        inferred_slice=inferred_slice,
        accepted_slice_ids=status.accepted_slice_ids,
        prompt_health=status.prompt_health,
        memory=status.memory,
        diagnostics=tuple(diagnostics),
        action=action,
    )


def build_next_frontier(
    start: Path | str = ".",
    layout_config: Path | str | None = None,
) -> LoopFrontier:
    """Build the artifact-inferred loop frontier from project state.

    Prefers the authored next slice from status. If the authored next
    milestone is exhausted, searches milestones in active-roadmap order for
    the first unaccepted slice in any needs_review, active, or planned
    milestone. Milestones with status completed, blocked, or unknown are
    ignored for automatic inference. Never raises for exhausted-roadmap states.
    """

    return _build_frontier_from_status(build_status(start, layout_config=layout_config))


def _derive_slug(slice_id: str, title: str) -> str:
    """Derive a deterministic slug from a slice ID and title.

    Pattern: ``frutlups_<milestone lower>_<slice lower>_<sanitized title>``
    where the title is lowercased and non-alphanumeric runs are replaced
    with underscores. Never raises.
    """

    parts = slice_id.replace("-", "_").lower()
    sanitized = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"frutlups_{parts}_{sanitized}"


def _next_prompt_sequence(prompt_artifacts: tuple[PromptArtifact, ...]) -> int:
    """Return the next available sequence after all known prompt sequences."""

    max_seq = 0
    for artifact in prompt_artifacts:
        if artifact.sequence is not None and artifact.sequence > max_seq:
            max_seq = artifact.sequence
    return max_seq + 1


@dataclass(frozen=True)
class _RoadmapPromptFields:
    """Milestone-authored content used only while composing prompt drafts."""

    objective: str = ""
    active_workspaces: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()


_ROADMAP_PLAIN_FIELD_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][^:\n]{0,80}):\s*(?P<value>.*)$"
)
_ROADMAP_BOLD_FIELD_RE = re.compile(
    r"^\s*\*\*(?P<label>[^*\n]{1,80}?)[.:]\*\*\s*(?P<value>.*)$"
)


def _roadmap_prompt_field_name(label: str) -> str:
    """Map the supported roadmap labels to their prompt-composition roles."""

    normalized = " ".join(label.strip().lower().replace("-", " ").split())
    compact = normalized.replace(" ", "")
    if normalized.startswith("objective"):
        return "objective"
    if normalized == "active workspaces":
        return "active_workspaces"
    if normalized == "non goals":
        return "non_goals"
    if compact.startswith("verification/") or compact.startswith("tests/"):
        return "verification"
    return ""


def _is_roadmap_field_boundary(label: str) -> bool:
    """Whether a plain ``Label:`` line is a roadmap field, not prose."""

    normalized = " ".join(label.strip().lower().replace("-", " ").split())
    return normalized in {
        "status",
        "disposition",
        "slices",
        "implementation package",
        "objective",
        "expected artifacts",
        "active workspaces",
        "non goals",
        "verification/evidence",
        "review strictness",
        "likely coding prompt",
        "done when",
        "closure evidence",
        "authority checkpoints",
        "opening gates",
        "prerequisites",
        "owned surfaces",
        "artifacts",
        "tests / evidence",
        "rollback",
        "strictness",
        "decision gates",
        "prompt route",
    }


def _roadmap_field_items(lines: list[str]) -> tuple[str, ...]:
    """Turn one authored prose/list field into ordered, wrapped items."""

    items: list[str] = []
    current: list[str] = []
    current_is_bullet = False

    def flush() -> None:
        nonlocal current, current_is_bullet
        value = " ".join(part.strip() for part in current if part.strip()).strip()
        if value:
            items.append(value)
        current = []
        current_is_bullet = False

    for line in lines:
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if bullet:
            flush()
            current = [bullet.group(1)]
            current_is_bullet = True
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if current_is_bullet and line.startswith((" ", "\t")):
            current.append(stripped)
            continue
        if current_is_bullet:
            flush()
        current.append(stripped)
    flush()
    return tuple(items)


def _parse_roadmap_prompt_fields(path: Path, milestone_id: str) -> _RoadmapPromptFields:
    """Read the named milestone's authored Objective/Non-goals/evidence fields.

    Both template-v3's plain ``Field:`` grammar and the development roadmap's
    historical ``**Field.**`` grammar are accepted. The helper is private so
    the released roadmap dataclass and status JSON shapes remain unchanged.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return _RoadmapPromptFields()

    wanted = milestone_id.upper()
    in_milestone = False
    current_field = ""
    collected: dict[str, list[str]] = {
        "objective": [],
        "active_workspaces": [],
        "non_goals": [],
        "verification": [],
    }

    for line in lines:
        milestone = re.match(r"^###\s+(M\d+):", line, re.IGNORECASE)
        if milestone:
            if in_milestone:
                break
            in_milestone = milestone.group(1).upper() == wanted
            current_field = ""
            continue
        if not in_milestone:
            continue

        bold_match = _ROADMAP_BOLD_FIELD_RE.match(line)
        plain_match = _ROADMAP_PLAIN_FIELD_RE.match(line)
        field_match = bold_match or plain_match
        if field_match:
            label = field_match.group("label")
            if bold_match is not None or _is_roadmap_field_boundary(label):
                current_field = _roadmap_prompt_field_name(label)
                inline = field_match.group("value").strip()
                if current_field and inline:
                    collected[current_field].append(inline)
                continue
        if current_field:
            collected[current_field].append(line)

    objective_items = _roadmap_field_items(collected["objective"])
    workspace_items = _roadmap_field_items(collected["active_workspaces"])
    workspaces: list[str] = []
    for item in workspace_items:
        for match in _PROMPT_BACKTICK_RE.finditer(item):
            candidate = match.group(1).strip().rstrip("/")
            if candidate and is_safe_relative(candidate) and candidate not in workspaces:
                workspaces.append(candidate)

    return _RoadmapPromptFields(
        objective="\n\n".join(objective_items),
        active_workspaces=tuple(workspaces),
        non_goals=_roadmap_field_items(collected["non_goals"]),
        verification=_roadmap_field_items(collected["verification"]),
    )


def _frontier_prompt_fields(status: ProjectStatus, milestone_id: str) -> _RoadmapPromptFields:
    """Merge the active roadmap's fields with detailed-roadmap fallbacks."""

    sources: list[Path] = []
    for candidate in (status.active_roadmap, status.detailed_roadmap):
        if candidate is not None and candidate not in sources:
            sources.append(candidate)
    parsed = [_parse_roadmap_prompt_fields(path, milestone_id) for path in sources]

    def first_text(name: str) -> str:
        return next((getattr(value, name) for value in parsed if getattr(value, name)), "")

    def first_tuple(name: str) -> tuple[str, ...]:
        return next((getattr(value, name) for value in parsed if getattr(value, name)), ())

    return _RoadmapPromptFields(
        objective=first_text("objective"),
        active_workspaces=first_tuple("active_workspaces"),
        non_goals=first_tuple("non_goals"),
        verification=first_tuple("verification"),
    )


def _relative_project_path(root: Path, path: Path | None) -> str:
    """Return one safe repository-relative POSIX path, or an empty string."""

    if path is None:
        return ""
    try:
        relative = path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except (OSError, ValueError):
        return ""
    return relative if is_safe_relative(relative) else ""


def _project_required_reading(
    status: ProjectStatus,
    profile: LayoutProfile,
    fields: _RoadmapPromptFields,
) -> tuple[str, ...]:
    """Compose real project readings in stable order for configured prompts."""

    candidates: list[str] = []
    # These are the long-standing prompt-model baselines. Configured projects
    # add their selected state/roadmap/context paths below; legacy rendering is
    # handled separately and remains byte-compatible.
    candidates.extend(("CLAUDE.md", "README.md"))
    if profile.state_file and is_safe_relative(profile.state_file):
        candidates.append(profile.state_file.strip().replace("\\", "/"))
    candidates.extend(
        path
        for path in (
            _relative_project_path(status.root, status.active_roadmap),
            _relative_project_path(status.root, status.detailed_roadmap),
        )
        if path
    )
    if profile.context_filename:
        for workspace in fields.active_workspaces:
            context = f"{workspace}/{profile.context_filename}"
            if is_safe_relative(context) and (status.root / PurePosixPath(context)).is_file():
                candidates.append(context)
    return _dedup_append((), tuple(candidates))


def build_coding_prompt_plan(
    start: Path | str = ".",
    *,
    sequence: int | None = None,
    slug: str | None = None,
    correction_round: int | None = None,
    memory_runner: MemoryCommandRunner | None = None,
    layout_config: Path | str | None = None,
) -> CodingPromptPlan:
    """Build a read-only plan for writing a coding prompt for the frontier.

    Uses ``build_status`` to discover the project and existing prompt
    inventory, then derives the sequence and slug from artifacts when
    not supplied. Returns a ``CodingPromptPlan`` with all fields populated
    for a valid frontier, or an invalid plan with deterministic errors when
    no frontier slice exists, the sequence is out of bounds, or rendering
    fails. Never raises for normal frontier states.
    """

    status, evidence = _build_status_with_evidence(
        start, memory_runner=memory_runner, layout_config=layout_config
    )
    return _build_coding_prompt_plan_from_status(
        status,
        sequence=sequence,
        slug=slug,
        correction_round=correction_round,
        memory_runner=memory_runner,
        evidence=evidence,
    )


def _workflow_metadata_yaml_value(value: str) -> str:
    """One double-quoted YAML scalar; prose titles never break the region."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _with_workflow_metadata_block(
    content: str,
    template: CodingPromptTemplate,
    profile: LayoutProfile,
    correction_round: int | None = None,
) -> str:
    """Inject the canonical fenced workflow-metadata block after the title.

    M003-S02 (owner note 008): the block uses the profile's configured
    routing field names and becomes the first fenced YAML region, so the
    same two-region observation that pairs authored prompts validates the
    generated prompt. Milestone/slice ids come from the already validated
    roadmap spine; the free-form title is emitted as one double-quoted
    scalar.
    """

    round_line = (
        f"round: {correction_round}\n"
        if correction_round is not None and correction_round >= 2
        else ""
    )
    block = (
        "Workflow metadata:\n"
        "\n"
        "```yaml\n"
        f"{profile.front_matter_milestone_field}: {template.milestone_id}\n"
        f"{profile.front_matter_slice_field}: {template.slice_id}\n"
        f"{profile.front_matter_title_field}: "
        f"{_workflow_metadata_yaml_value(template.title)}\n"
        f"{round_line}"
        "role: coder\n"
        "```\n"
    )
    lines = content.splitlines(keepends=True)
    if lines and lines[0].startswith("# "):
        head = lines[0]
        rest = "".join(lines[1:]).lstrip("\n")
        return f"{head}\n{block}\n{rest}"
    return f"{block}\n{content}"


def _build_coding_prompt_plan_from_status(
    status: ProjectStatus,
    *,
    sequence: int | None = None,
    slug: str | None = None,
    correction_round: int | None = None,
    memory_runner: MemoryCommandRunner | None = None,
    evidence: "_AcceptanceEvidence | None" = None,
) -> CodingPromptPlan:
    """Build the coding-prompt plan from an already-built status (M002-S04).

    Private single-selection helper: callers that already hold the
    invocation's :class:`ProjectStatus` (and thus its already selected
    ``LoadedLayout``) reuse it here instead of resolving the layout again.
    """

    frontier = _build_frontier_from_status(status)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    self_report_contract = self_report_format_contract(
        self_report_schema_for_profile(profile)
    )

    if evidence is None:
        evidence = _collect_acceptance_evidence(status.root, profile)
    rework = evidence.rework_resolution
    if rework is None:
        rework = _resolve_rework(
            status.root,
            status.slices,
            status.accepted_slice_ids,
            status.prompt_artifacts,
            profile,
            evidence,
        )

    errors: list[str] = []

    if frontier.inferred_slice is None:
        errors.append("no frontier slice found; cannot build coding prompt")
        return CodingPromptPlan(
            frontier=frontier,
            sequence=sequence,
            slug=slug or "",
            valid=False,
            errors=tuple(errors),
            template=None,
            render=None,
            preview=None,
        )

    inferred_slice = frontier.inferred_slice
    inferred_milestone = frontier.inferred_milestone

    if sequence is None:
        sequence = _next_prompt_sequence(status.prompt_artifacts)

    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        errors.append("sequence must be a positive integer")
    elif sequence > MAX_PROMPT_SEQUENCE:
        errors.append(f"sequence must be at most {MAX_PROMPT_SEQUENCE}")

    valid_correction_round = correction_round is None or (
        isinstance(correction_round, int)
        and not isinstance(correction_round, bool)
        and 1 <= correction_round <= _MAX_WORKFLOW_ROUND
    )
    if not valid_correction_round:
        errors.append(
            f"correction round must be an integer from 1 to {_MAX_WORKFLOW_ROUND}"
        )
    ordinary_correction_round = (
        correction_round
        if valid_correction_round
        and correction_round is not None
        and correction_round >= 2
        else None
    )

    declaration = rework.active_declaration
    if declaration is not None and sequence is not None:
        if sequence <= declaration.baseline_prompt_sequence:
            errors.append(
                "rework coding-prompt sequence must be newer than the declaration baseline"
            )
    if declaration is not None and ordinary_correction_round is not None:
        errors.append("correction rounds cannot be combined with an active rework declaration")

    if slug is None:
        slug = _derive_slug(inferred_slice.slice_id, inferred_slice.title)
        if declaration is not None and sequence is not None:
            slug += (
                f"_rework_{declaration.declaration_sequence:03d}_"
                f"{declaration.pass_id}_{sequence:03d}"
            )

    if not isinstance(slug, str) or not slug.strip():
        errors.append("slug must be a non-empty string")
        slug = slug or ""

    if errors:
        return CodingPromptPlan(
            frontier=frontier,
            sequence=sequence,
            slug=slug,
            valid=False,
            errors=tuple(errors),
            template=None,
            render=None,
            preview=None,
        )

    milestone_id = (
        inferred_milestone.milestone_id
        if inferred_milestone is not None
        else inferred_slice.milestone_id
    )
    slice_id = inferred_slice.slice_id
    title = inferred_slice.title.strip("`").strip()

    parts = slice_id.replace("-", "_").lower()
    sanitized_title = re.sub(r"[^a-z0-9]+", "_", inferred_slice.title.lower()).strip("_")
    evidence_stem = f"{parts}_{sanitized_title}"
    if declaration is not None:
        evidence_stem += (
            f"_rework_{declaration.declaration_sequence:03d}_"
            f"{declaration.pass_id}_{sequence:03d}"
        )
    elif ordinary_correction_round is not None:
        evidence_stem += f"_round_{ordinary_correction_round:03d}"
    self_report_path = f"{profile.reviews_dir}/{evidence_stem}{profile.self_report_suffix}"

    is_memory_update = frontier.slice_kind == SliceKind.MEMORY_UPDATE

    configured_prompt = bool(profile.coding_template)
    prompt_fields = (
        _frontier_prompt_fields(status, milestone_id)
        if configured_prompt
        else _RoadmapPromptFields()
    )
    if configured_prompt:
        role_instructions = "You are the coding agent for this project."
        if prompt_fields.objective:
            role_instructions += (
                "\n\nMilestone objective:\n" + prompt_fields.objective
            )
        if declaration is not None:
            role_instructions += (
                "\n\nThis is accepted-slice rework for pass "
                f"`{declaration.pass_id}`. Re-audit this slice's historical "
                "accepted evidence and produce a fresh implementation, self-report, "
                "independent review, and verdict record."
            )
        required_reading = _project_required_reading(status, profile, prompt_fields)
        if declaration is not None and declaration.path not in required_reading:
            required_reading = required_reading + (declaration.path,)
        scope_paths = prompt_fields.active_workspaces or ("08_pkg/",)
        non_goals = prompt_fields.non_goals or (
            "Do not implement future milestones or unrelated behavior.",
            "Do not mutate active roadmap state.",
        )
        verification_commands = prompt_fields.verification or (
            profile.validation_command or "python -m unittest discover -s tests",
        )
        definition_of_done = list(
            inferred_milestone.done_criteria
            if inferred_milestone is not None and inferred_milestone.done_criteria
            else (
                "All required behavior is implemented and tested.",
                "Existing project verification remains green.",
                "Self-report is written.",
            )
        )
        if profile.coder_may_create_review_prompt:
            definition_of_done.append("Matching review prompt is created.")
    else:
        # The genuine legacy path retains the released 0.1.2 bytes and its
        # historical home-repository assumptions.
        role_instructions = (
            "You are the coding agent for `frutlups`.\n\n"
            "Implement this slice. Keep the package local-first, "
            "artifact-first, provider-neutral, deterministic, and "
            "limited to the standard library plus already declared "
            "runtime dependencies."
        )
        if declaration is not None:
            role_instructions += (
                "\n\nThis is accepted-slice rework for pass "
                f"`{declaration.pass_id}`. Re-audit the historical accepted "
                "evidence and produce a fresh prompt-linked evidence chain."
            )
        required_reading = (
            "CLAUDE.md",
            "README.md",
            "08_pkg/CONTEXT.md",
            "08_pkg/README.md",
            "03_experiments/active_roadmap_frutlups.md",
            "06_infra/architecture.md",
        )
        if declaration is not None:
            required_reading += (declaration.path,)
        scope_paths = ("08_pkg/",)
        non_goals = (
            "Do not implement future milestones or unrelated behavior.",
            "Do not mutate active roadmap state.",
            "Do not add a new dependency without authorization.",
            "Do not add llloom integration beyond existing status detection.",
        )
        definition_of_done = [
            "All required behavior is implemented and tested.",
            "Existing test suite remains green.",
            "Self-report is written.",
            "Matching review prompt is created.",
        ]
        verification_commands = (
            "$env:PYTHONPATH='src'",
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
            "python -m frutlups next ..",
            "python -m compileall -q src",
        )

    template = CodingPromptTemplate(
        sequence=sequence,
        milestone_id=milestone_id,
        slice_id=slice_id,
        slug=slug.strip(),
        title=title,
        role_instructions=role_instructions,
        required_reading=required_reading,
        scope_paths=scope_paths,
        non_goals=non_goals,
        definition_of_done=tuple(definition_of_done),
        verification_commands=verification_commands,
        self_report_path=self_report_path,
        memory_update=is_memory_update,
    )

    if profile.coding_template:
        # M003-S03: a selected profile with a configured template path must
        # render through that scaffold; the hard-coded renderer is never a
        # silent fallback. M011-S01 (D3): the configured path performs no memory
        # query. Instead, when the selected memory mode is lightweight/llloom the
        # selected posture file is routed into Read First exactly once, with
        # deterministic order and exact raw-value de-duplication; no raw memory
        # command output is ever inserted.
        posture_reading = _configured_posture_reading(status, profile)
        if posture_reading:
            template = replace(
                template,
                required_reading=_dedup_append(template.required_reading, posture_reading),
            )
        render = _render_coding_from_scaffold(
            status, profile, template, self_report_contract
        )
    else:
        # Genuine legacy compatibility path: the memory query snippet may remain
        # only here and only under the historical root behavior (M011-S01 D3).
        is_legacy = (
            status.layout is not None
            and status.layout.source == ProfileSource.LEGACY_FALLBACK
        )
        snippet = None
        if is_legacy:
            snippet = build_memory_prompt_snippet(
                root=status.root,
                query=f"{slice_id} {title}",
                runner=memory_runner,
            )
        if not self_report_contract:
            render = CodingPromptRenderResult(
                content="",
                valid=False,
                errors=(_CODING_REPORT_FORMAT_CONTRACT_ERROR,),
            )
        else:
            render = render_coding_prompt(
                template,
                snippet=snippet if (snippet is not None and snippet.has_content) else None,
                posture_path=profile.llloom_posture_file or None,
                self_report_contract=self_report_contract,
            )
    if render.valid and profile.prompt_pairing == "workflow_metadata":
        # M003-S02 (owner note 008): under the configured metadata pairing
        # the generated coding prompt must carry the same validated fenced
        # identity the pairing decision consumes. Template-v3 profiles
        # deliberately do not parse the roadmap-item body, so a generated
        # prompt without a metadata region would be invisible to its own
        # loop and the resume step could never advance past it.
        render = replace(
            render,
            content=_with_workflow_metadata_block(
                render.content, template, profile, correction_round
            ),
        )
    preview = preview_coding_prompt(template, prompt_dir=profile.coding_prompt_dir)
    preview = _reconciled_preview(preview, render)

    if not render.valid:
        return CodingPromptPlan(
            frontier=frontier,
            sequence=sequence,
            slug=slug.strip(),
            valid=False,
            errors=render.errors,
            template=template,
            render=render,
            preview=preview,
            coding_prompt_dir=profile.coding_prompt_dir,
        )

    return CodingPromptPlan(
        frontier=frontier,
        sequence=sequence,
        slug=slug.strip(),
        valid=True,
        errors=(),
        template=template,
        render=render,
        preview=preview,
        coding_prompt_dir=profile.coding_prompt_dir,
    )


def _looks_like_project_root(path: Path) -> bool:
    # Legacy and v2 templates both have 00_brief + prompts; additionally accept an
    # explicit layout config or a v2 PROJECT_STATE.md as a root marker.
    if (path / "00_brief").is_dir() and (path / "prompts").is_dir():
        return True
    if (path / "frutlups.layout.yaml").is_file():
        return True
    return (path / "PROJECT_STATE.md").is_file() and (path / "prompts").is_dir()


def _select_active_roadmap(candidates: tuple[Path, ...]) -> Path | None:
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.name == "active_roadmap_frutlups.md":
            return candidate
    return candidates[0]


def _select_detailed_roadmap(candidates: tuple[Path, ...]) -> Path | None:
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.name == "development_roadmap_frutlups.md":
            return candidate
    return candidates[0]


def _inventory_prompts(paths: TemplatePaths) -> PromptInventory:
    coding_dir = paths.prompts.coding
    review_dir = paths.prompts.review
    return PromptInventory(
        coding_count=_count_markdown_files(coding_dir),
        review_count=_count_markdown_files(review_dir),
    )


def _count_markdown_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for candidate in path.glob("*.md") if candidate.is_file())


def _find_accepted_slice_ids(reviews_dir: Path) -> tuple[str, ...]:
    """Legacy compatibility wrapper: slice IDs whose review report passes.

    Retained only for existing private compatibility tests. No live status,
    frontier, verdict-plan, corrective-review, or resume path uses it: the
    single runtime authority is ``_collect_acceptance_evidence`` over the
    selected profile snapshot (M003-S05). Detection follows the filename
    convention ``m###_s##_*_review_report.md`` (case-insensitive); the slice ID
    is reconstructed as ``M###-S##``. Reports without a clear ``## Verdict``
    section resolving to ``pass`` are ignored.
    """

    if not reviews_dir.is_dir():
        return ()
    accepted: list[str] = []
    for report in sorted(reviews_dir.glob("*_review_report.md")):
        match = SLICE_REVIEW_REPORT_RE.match(report.name)
        if not match:
            continue
        if not _has_pass_verdict(report):
            continue
        slice_id = f"{match.group('milestone').upper()}-{match.group('slice').upper()}"
        if slice_id not in accepted:
            accepted.append(slice_id)
    return tuple(accepted)


def _has_pass_verdict(path: Path) -> bool:
    """Return ``True`` when a review report's verdict resolves to ``pass``.

    Delegates to the canonical review-report verdict parser
    (:func:`parse_review_report_verdict`) so accepted-slice detection honors the
    exact same ``## Verdict`` syntax as ``record-verdict``: the first non-empty,
    non-fence line of the verdict section, after stripping common list prefixes
    (``- ``, ``* ``, ``1. ``) and surrounding inline-code backticks, matched
    case-insensitively. This accepts ``pass``, ``PASS``, ``` `pass` ```, and
    ``` - `pass` ```, while ``needs_work``, ``blocked``, ``override``, malformed
    verdicts, and reports without a verdict section remain unaccepted. Never
    raises (the parser reports missing/unreadable files as invalid results).
    """

    result = parse_review_report_verdict(ReviewReportVerdictParseCommand(path=path))
    return result.valid and result.verdict == ReviewVerdict.PASS


def _format_names(paths: tuple[Path, ...]) -> str:
    return ", ".join(path.name for path in paths)


_REVIEW_OUTPUT_INLINE_RE = re.compile(
    r"(?im)^review output\s*:\s*`([^`]*_review_report\.md)`",
)
_REVIEW_OUTPUT_CONFIGURED_RE = re.compile(
    r"^- Write the review report at `([^`]*_review_report\.md)`\.$",
)
_REVIEW_REPORT_BACKTICK_RE = re.compile(r"`([^`]*_review_report\.md)`")

# A "terminal closure-review report" reviews a verdict-recording closure slice,
# e.g. ``m018_s02_record_096_review_verdict_review_report.md``. Requiring the
# ``_record_<number>_..verdict_review_report.md`` shape keeps this narrow: ordinary
# slice review reports (no ``_record_<digits>_`` segment) never match, and a slice
# merely containing the word "record" (no digits) does not match either.
_TERMINAL_CLOSURE_REVIEW_RE = re.compile(
    r"_record_\d+_[a-z0-9_]*verdict_review_report\.md$",
    re.IGNORECASE,
)


def _is_terminal_closure_review_report(report_name: str) -> bool:
    """Return ``True`` for a review report whose sole purpose is to review a
    verdict-recording closure slice (the self-perpetuating post-roadmap tail).

    Conservative filename match only; callers additionally gate on a completed
    roadmap (no inferred frontier) and *independent* prior acceptance before
    treating such a report as terminal (so ordinary unrecorded pass reports are
    unaffected).
    """

    return bool(_TERMINAL_CLOSURE_REVIEW_RE.search(report_name))


def _is_slice_accepted_by_nonterminal_evidence(
    evidence: "_AcceptanceEvidence", report_re: "re.Pattern[str]", slice_id_upper: str
) -> bool:
    """Return ``True`` when ``slice_id_upper`` has a ``pass`` review report that is
    NOT a terminal closure tail.

    This is the *independent* acceptance evidence the terminal-closure skip
    requires: a terminal-tail report must not be able to certify its own slice as
    accepted and then be skipped. Closure-tail reports are excluded from counting
    as acceptance evidence (a genuine slice acceptance is never a terminal-tail
    report). Never raises. M003-S05: derived from the one selected evidence
    snapshot rather than a second filesystem scan.
    """

    for rel in evidence.pass_reports:
        name = rel.rsplit("/", 1)[-1]
        if _is_terminal_closure_review_report(name):
            continue
        match = report_re.match(name)
        if (
            match
            and f"{match.group('milestone').upper()}-{match.group('slice').upper()}"
            == slice_id_upper
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# M003-S05: typed acceptance evidence (private)
# ---------------------------------------------------------------------------
#
# Decision 5 (02_analysis authority contract §6.5): the canonically parsed
# review report is the sole acceptance authority; a verdict record is only a
# durable receipt. This seam classifies reports and records from one selected
# profile snapshot so a record whose corresponding review report is missing or
# does not parse to ``pass`` is typed, deterministic contradictory state — never
# inferred from filenames, record prose, journal entries, or profile data.

_MAX_DISCOVERY_DEPTH = 12
_MAX_DISCOVERY_ENTRIES = 20_000
_MAX_EVIDENCE_BYTES = 256 * 1024
_MAX_EVIDENCE_LINES = 4000
_MAX_EVIDENCE_DIAGNOSTIC = 240

_SOURCE_HEADING_RE = re.compile(r"^ {0,3}##\s+Source\s*$")
_LIVE_H2_RE = re.compile(r"^ {0,3}##(?!#)(?:\s|$)")


class _RecordContradictionKind(StrEnum):
    """Typed defect classes for verdict-record/review-report disagreement."""

    MISSING_REPORT = "missing_report"
    MISSING_SOURCE = "missing_source"
    UNREADABLE_RECORD = "unreadable_record"
    AMBIGUOUS_SOURCE = "ambiguous_source"
    UNSAFE_CITATION = "unsafe_citation"
    RESOLVED_ESCAPE = "resolved_escape"
    UNPARSEABLE_REPORT = "unparseable_report"
    NON_PASS_REPORT = "non_pass_report"
    DIFFERENT_SLICE = "different_slice"


@dataclass(frozen=True)
class _VerdictRecordContradiction:
    """One typed verdict-record/review-report contradiction (M003-S05).

    Private so M003-S06 can map the typed ``kind`` to its versioned frontier
    outcome without reparsing artifacts or inferring truth from text.
    """

    kind: _RecordContradictionKind
    record_path: str
    report_path: str
    slice_id: str
    diagnostic: str


class _AuthorityDefectKind(StrEnum):
    """Typed fail-closed defect classes for the reviews authority root itself.

    Emitted independently of verdict-record iteration (M003-S05 / Review 029):
    a configured reviews directory that exists but cannot be safely resolved or
    contained, and a suffix-matching report that resolves outside the
    configured reviews directory, are invalid authority state even when no
    verdict record cites them.
    """

    UNRESOLVABLE_AUTHORITY_ROOT = "unresolvable_authority_root"
    ESCAPED_AUTHORITY_ROOT = "escaped_authority_root"
    ESCAPED_AUTHORITY_REPORT = "escaped_authority_report"


@dataclass(frozen=True)
class _AuthorityDefect:
    """One typed acceptance-authority defect (M003-S05).

    ``authority_path`` is always the safe configured repo-relative identity
    (the configured reviews directory, or the configured-relative report
    name); never a resolved external path, exception text, or content byte.
    """

    kind: _AuthorityDefectKind
    authority_path: str
    diagnostic: str


@dataclass(frozen=True)
class _ClosureReceipt:
    """One qualifying generated closure receipt (M003-S06, Prompt 031).

    Emitted only for a contained verdict record that (a) is paired to its
    canonically passing review report by its live ``## Source`` citation with
    no contradiction, and (b) carries the exact generated
    :func:`_render_verdict_record` closure fields: one unambiguous live
    ``## Slice`` section naming the record's own slice and milestone, one
    live ``## Parsed Verdict`` value ``pass``, and one live ``## Next
    Action`` with ``Kind: milestone_complete`` and ``Next slice: none``.
    This is receipt evidence for Decision 6 resolution 3; it never accepts a
    slice (Decision 5: acceptance authority stays with the report).
    """

    record_path: str
    report_path: str
    slice_id: str
    milestone_id: str


@dataclass(frozen=True)
class _AcceptanceEvidence:
    """Typed acceptance evidence from one selected profile snapshot."""

    accepted_slice_ids: tuple[str, ...]
    pass_reports: tuple[str, ...]
    unrecorded_pass_reports: tuple[str, ...]
    contradictions: tuple[_VerdictRecordContradiction, ...]
    authority_defects: tuple[_AuthorityDefect, ...] = ()
    closure_receipts: tuple[_ClosureReceipt, ...] = ()
    rework_declarations: tuple[ReworkDeclaration, ...] = ()
    rework_diagnostics: tuple[ReworkDeclarationDiagnostic, ...] = ()
    rework_resolution: _ReworkResolution | None = dataclass_field(
        default=None, compare=False, repr=False
    )


def _with_rework_declarations(
    root: Path, evidence: _AcceptanceEvidence
) -> _AcceptanceEvidence:
    """Return the evidence with one bounded declaration inventory attached."""

    inventory = load_rework_declarations(root)
    if not inventory.declarations and not inventory.diagnostics:
        return evidence
    return replace(
        evidence,
        rework_declarations=inventory.declarations,
        rework_diagnostics=inventory.diagnostics,
    )


def _slice_artifact_re(suffix: str) -> "re.Pattern[str]":
    """Canonical slice-artifact naming boundary for one configured suffix."""

    return re.compile(
        r"^(?P<milestone>m\d+)_(?P<slice>s\d+)_.*" + re.escape(suffix) + r"$",
        re.IGNORECASE,
    )


def _artifact_correction_round(name: str, suffix: str) -> int:
    """Return a report/record filename's terminal correction round."""

    stem = name[: -len(suffix)]
    match = _CORRECTION_ROUND_STEM_RE.search(stem)
    return int(match.group("round")) if match else 1


def _cap_evidence_diagnostic(message: str) -> str:
    """Individually cap one evidence diagnostic at the accepted 240 bound."""

    if len(message) <= _MAX_EVIDENCE_DIAGNOSTIC:
        return message
    return message[: _MAX_EVIDENCE_DIAGNOSTIC - 3] + "..."


_CLOSURE_SLICE_HEADING_RE = re.compile(r"^ {0,3}##\s+Slice\s*$")
_CLOSURE_VERDICT_HEADING_RE = re.compile(r"^ {0,3}##\s+Parsed Verdict\s*$")
_CLOSURE_ACTION_HEADING_RE = re.compile(r"^ {0,3}##\s+Next Action\s*$")
_CLOSURE_SLICE_ID_RE = re.compile(r"^Slice ID: `(?P<value>[^`]{1,80})`$")
_CLOSURE_MILESTONE_RE = re.compile(r"^Milestone: `(?P<value>[^`]{1,80})`$")
_CLOSURE_VERDICT_RE = re.compile(r"^Verdict: `(?P<value>[^`]{1,80})`$")
_CLOSURE_KIND_RE = re.compile(r"^Kind: `(?P<value>[^`]{1,80})`$")
_CLOSURE_NEXT_SLICE_RE = re.compile(r"^Next slice: (?P<value>none|`[^`]{1,80}`)$")


@dataclass(frozen=True)
class _ClosureFields:
    """Closure fields read from one generated verdict record (private).

    ``valid`` is ``True`` only when the record carries exactly one live
    ``## Slice``, ``## Parsed Verdict``, and ``## Next Action`` section, each
    generated field exactly once, in the exact
    :func:`_render_verdict_record` line shapes. Missing, duplicate,
    malformed, fenced/indented, or non-generated fields make the whole
    closure read invalid; they never disqualify the record as a receipt.
    """

    valid: bool
    slice_id: str = ""
    milestone_id: str = ""
    verdict: str = ""
    kind: str = ""
    next_slice_none: bool = False


def _read_record_evidence(
    record_abs: Path, profile: LayoutProfile
) -> tuple[str, str, _ClosureFields]:
    """One bounded read of a verdict record: Source citation plus closure fields.

    Returns ``(citation, defect, closure)``. ``citation``/``defect`` keep the
    accepted ``## Source`` contract: ``citation`` is the normalized
    repo-relative path of the first live backticked path ending in the
    configured review-report suffix (case-insensitive suffix recognition,
    matching the artifact regex) inside the live ``## Source`` section;
    ``defect`` is ``""`` on success, else ``"no_source"`` /
    ``"unreadable_record"`` / ``"ambiguous_source"`` / ``"unsafe_citation"``.
    ``closure`` carries the Prompt 031 closure-receipt fields from the same
    single read (see :class:`_ClosureFields`); it is advisory receipt data
    and never affects the citation contract. Liveness uses the accepted
    CommonMark boundary shared with the scaffold scanner
    (:data:`_FENCE_OPEN`, :func:`_closing_fence`, :func:`_is_indented_code`).
    Never raises; never echoes record bytes.
    """

    invalid_closure = _ClosureFields(valid=False)
    try:
        raw = record_abs.read_bytes()
    except OSError:
        return "", "unreadable_record", invalid_closure
    if len(raw) > _MAX_EVIDENCE_BYTES:
        return "", "unreadable_record", invalid_closure
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", "unreadable_record", invalid_closure
    lines = text.splitlines()
    if len(lines) > _MAX_EVIDENCE_LINES:
        return "", "unreadable_record", invalid_closure

    reviews_prefix = profile.reviews_dir.rstrip("/") + "/"
    report_suffix_cf = profile.review_report_suffix.casefold()
    in_fence: tuple[str, int] | None = None  # character, opener length
    section = ""  # "", "source", "slice", "verdict", "action", "other"
    heading_counts = {"source": 0, "slice": 0, "verdict": 0, "action": 0}
    citation = ""
    citation_unsafe = False
    closure_values: dict[str, list[str]] = {
        "slice_id": [],
        "milestone_id": [],
        "verdict": [],
        "kind": [],
        "next_slice": [],
    }
    for line in lines:
        if in_fence is not None:
            if _closing_fence(line, in_fence[0], in_fence[1]):
                in_fence = None
            continue
        if _is_indented_code(line):
            continue
        fence_open = _FENCE_OPEN.match(line)
        if fence_open:
            fence_run = fence_open.group(1)
            in_fence = (fence_run[0], len(fence_run))
            continue
        if _SOURCE_HEADING_RE.match(line):
            heading_counts["source"] += 1
            section = "source"
            continue
        if _CLOSURE_SLICE_HEADING_RE.match(line):
            heading_counts["slice"] += 1
            section = "slice"
            continue
        if _CLOSURE_VERDICT_HEADING_RE.match(line):
            heading_counts["verdict"] += 1
            section = "verdict"
            continue
        if _CLOSURE_ACTION_HEADING_RE.match(line):
            heading_counts["action"] += 1
            section = "action"
            continue
        if _LIVE_H2_RE.match(line):
            section = "other"
            continue
        if section == "source" and not citation and not citation_unsafe:
            for span in _PROMPT_BACKTICK_RE.findall(line):
                candidate = span.strip().replace("\\", "/")
                if not candidate.casefold().endswith(report_suffix_cf):
                    continue
                parts = candidate.split("/")
                if (
                    candidate.startswith("/")
                    or re.match(r"^[A-Za-z]:", candidate)
                    or any(part in ("", ".", "..") for part in parts)
                    or not candidate.startswith(reviews_prefix)
                    or (
                        # M003-S02: contained nested citations are legal only
                        # under the configured recursive discovery mode; flat
                        # layouts keep the exact flat citation grammar.
                        profile.reports_discovery != "recursive_contained"
                        and "/" in candidate[len(reviews_prefix):]
                    )
                ):
                    citation_unsafe = True
                else:
                    citation = candidate
                break
        elif section == "slice":
            for key, pattern in (
                ("slice_id", _CLOSURE_SLICE_ID_RE),
                ("milestone_id", _CLOSURE_MILESTONE_RE),
            ):
                match = pattern.match(line)
                if match:
                    closure_values[key].append(match.group("value"))
        elif section == "verdict":
            match = _CLOSURE_VERDICT_RE.match(line)
            if match:
                closure_values["verdict"].append(match.group("value"))
        elif section == "action":
            match = _CLOSURE_KIND_RE.match(line)
            if match:
                closure_values["kind"].append(match.group("value"))
            match = _CLOSURE_NEXT_SLICE_RE.match(line)
            if match:
                closure_values["next_slice"].append(match.group("value"))

    closure = invalid_closure
    if (
        heading_counts["slice"] == 1
        and heading_counts["verdict"] == 1
        and heading_counts["action"] == 1
        and all(len(values) == 1 for values in closure_values.values())
    ):
        closure = _ClosureFields(
            valid=True,
            slice_id=closure_values["slice_id"][0],
            milestone_id=closure_values["milestone_id"][0],
            verdict=closure_values["verdict"][0],
            kind=closure_values["kind"][0],
            next_slice_none=closure_values["next_slice"][0] == "none",
        )

    if heading_counts["source"] > 1:
        return "", "ambiguous_source", closure
    if citation_unsafe:
        return "", "unsafe_citation", closure
    if citation:
        return citation, "", closure
    return "", "no_source", closure


def _read_source_citation(record_abs: Path, profile: LayoutProfile) -> tuple[str, str]:
    """Compatibility wrapper: the accepted ``(citation, defect)`` contract.

    Delegates to :func:`_read_record_evidence`; retained so the accepted
    Source-pairing seam keeps its name and shape for existing private tests.
    """

    citation, defect, _closure = _read_record_evidence(record_abs, profile)
    return citation, defect


def _contradiction_message(
    kind: _RecordContradictionKind, record_rel: str, report_rel: str
) -> str:
    """One stable bounded owned message naming both artifacts, never content."""

    base = f"verdict record {record_rel} contradicts review evidence"
    if kind is _RecordContradictionKind.MISSING_REPORT:
        return f"{base}: corresponding review report {report_rel} is missing"
    if kind is _RecordContradictionKind.MISSING_SOURCE:
        return (
            f"{base}: the record has no live source citation; "
            f"expected review report {report_rel}"
        )
    if kind is _RecordContradictionKind.UNREADABLE_RECORD:
        return (
            f"{base}: the record is unreadable or over the evidence limit; "
            f"expected review report {report_rel}"
        )
    if kind is _RecordContradictionKind.AMBIGUOUS_SOURCE:
        return f"{base}: ambiguous source framing; expected {report_rel}"
    if kind is _RecordContradictionKind.UNSAFE_CITATION:
        return f"{base}: unsafe source citation; expected {report_rel}"
    if kind is _RecordContradictionKind.RESOLVED_ESCAPE:
        return (
            f"{base}: artifact resolves outside the configured reviews "
            f"directory; expected review report {report_rel}"
        )
    if kind is _RecordContradictionKind.UNPARSEABLE_REPORT:
        return (
            f"{base}: corresponding review report {report_rel} "
            "has no parseable verdict"
        )
    if kind is _RecordContradictionKind.NON_PASS_REPORT:
        return (
            f"{base}: corresponding review report {report_rel} "
            "does not parse to pass"
        )
    return (
        f"{base}: corresponding review report {report_rel} is for a different slice"
    )


def _authority_defect_message(kind: _AuthorityDefectKind, authority_rel: str) -> str:
    """One stable bounded owned message per authority-defect class.

    Names only the safe configured repo-relative identity; never exception
    text, absolute paths, resolved external paths, or content bytes.
    """

    if kind is _AuthorityDefectKind.UNRESOLVABLE_AUTHORITY_ROOT:
        return (
            f"acceptance authority defect: configured reviews directory "
            f"{authority_rel} exists but cannot be safely resolved; "
            "acceptance evidence is unavailable"
        )
    if kind is _AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT:
        return (
            f"acceptance authority defect: configured reviews directory "
            f"{authority_rel} resolves outside the project root; external "
            "review authority is refused"
        )
    return (
        f"acceptance authority defect: review report {authority_rel} resolves "
        "outside the configured reviews directory; its verdict is not read"
    )


def _make_authority_defect(
    kind: _AuthorityDefectKind, authority_rel: str
) -> _AuthorityDefect:
    return _AuthorityDefect(
        kind=kind,
        authority_path=authority_rel,
        diagnostic=_cap_evidence_diagnostic(
            _authority_defect_message(kind, authority_rel)
        ),
    )


class _DiscoveryBoundExceeded(Exception):
    """The bounded contained inventory exceeded its depth or entry bound."""


def _contained_review_inventory(
    reviews_dir_abs: Path, reviews_rel: str
) -> list[tuple[Path, str]]:
    """One bounded deterministic recursive inventory of ordinary files.

    M003-S02 (owner note 008): the configured reviews root may contain
    milestone subdirectories. The inventory is no-follow — symlinked
    directories are never descended and symlink file aliases never become
    evidence (deeper reparse aliases are additionally fenced by the
    caller's resolved-containment checks) — collects ordinary files only,
    and orders candidates by canonical repository-relative path. Exceeding
    the depth or entry bound raises :class:`_DiscoveryBoundExceeded`, which
    the caller types as an unresolvable authority root.
    """

    inventory: list[tuple[Path, str]] = []
    examined = 0
    base_depth = len(reviews_dir_abs.parts)
    for dirpath, dirnames, filenames in os.walk(reviews_dir_abs, followlinks=False):
        current = Path(dirpath)
        if len(current.parts) - base_depth >= _MAX_DISCOVERY_DEPTH:
            raise _DiscoveryBoundExceeded
        dirnames.sort()
        for name in sorted(filenames):
            examined += 1
            if examined > _MAX_DISCOVERY_ENTRIES:
                raise _DiscoveryBoundExceeded
            entry = current / name
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
            except OSError:
                continue
            rel = entry.relative_to(reviews_dir_abs).as_posix()
            inventory.append((entry, f"{reviews_rel}/{rel}"))
    inventory.sort(key=lambda pair: pair[1])
    return inventory


def _collect_acceptance_evidence(root: Path, profile: LayoutProfile) -> _AcceptanceEvidence:
    """Classify review reports and verdict records from one selected snapshot.

    Pure, deterministic, repeated-call stable, filesystem read-only, and never
    raising. Uses the selected profile's ``reviews_dir``,
    ``review_report_suffix``, and ``verdict_record_suffix``; the canonical
    report parser is the only verdict parser. Resolved containment is enforced
    before any authority bytes are read: the configured reviews directory must
    resolve within the project root and every scanned record and cited or
    expected report must resolve within it, else the record is typed
    contradictory state without reading external bytes. Independently of
    record iteration (Review 029), an existing reviews directory that cannot
    be resolved, or that resolves outside the project root, and a
    suffix-matching report that resolves outside the reviews directory, are
    each typed ``authority_defects`` fail-closed state; resolution failures
    (``OSError`` and loop ``RuntimeError``) never raise and never echo
    exception text or absolute paths. A file matching the configured
    verdict-record suffix never enters the acceptance-authority report scan
    (Decision 5: a record can never accept). A record is paired to
    the report its live ``## Source`` section cites; a record with no live
    citation is a typed missing-source contradiction (the same-stem path is
    retained only as the safe expected label). The cited report must
    independently parse to ``pass`` for the same slice.
    """

    reviews_dir_abs = root / profile.reviews_dir
    reviews_rel = profile.reviews_dir.rstrip("/")
    report_re = _slice_artifact_re(profile.review_report_suffix)
    record_re = _slice_artifact_re(profile.verdict_record_suffix)
    if not reviews_dir_abs.is_dir():
        return _with_rework_declarations(
            root, _AcceptanceEvidence((), (), (), ())
        )
    authority_defects: list[_AuthorityDefect] = []
    try:
        resolved_root = root.resolve()
        resolved_reviews = reviews_dir_abs.resolve()
    except (OSError, RuntimeError):
        # A configured authority root that exists but cannot be safely
        # resolved is typed fail-closed state, independently of any record
        # (Review 029). RuntimeError covers resolution loops; no exception
        # text or absolute path is echoed.
        return _with_rework_declarations(
            root,
            _AcceptanceEvidence(
                (),
                (),
                (),
                (),
                (
                    _make_authority_defect(
                        _AuthorityDefectKind.UNRESOLVABLE_AUTHORITY_ROOT, reviews_rel
                    ),
                ),
            ),
        )
    reviews_contained = _is_within(resolved_reviews, resolved_root)
    if not reviews_contained:
        # Prompt 031: an escaped configured reviews directory is one typed
        # root-level fail-closed defect. Its children are never enumerated,
        # so no external filename, path, byte, or hostile value can enter
        # evidence identities or diagnostics; only the safe configured
        # repo-relative root identity is reported.
        return _with_rework_declarations(
            root,
            _AcceptanceEvidence(
                (),
                (),
                (),
                (),
                (
                    _make_authority_defect(
                        _AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT, reviews_rel
                    ),
                ),
            ),
        )

    def _resolved_within_reviews(path: Path) -> bool:
        try:
            return _is_within(path.resolve(), resolved_reviews)
        except (OSError, RuntimeError):
            return False

    # One deterministic enumeration of the contained directory, then
    # classification by case-insensitive configured-suffix semantics — the
    # same normalization family as the case-insensitive artifact regex, so
    # host filesystem case behavior cannot reclassify an artifact. A record
    # match wins: a verdict record can never enter the acceptance-authority
    # report scan (Decision 5), including overlapping, reverse-overlapping,
    # identical, and mixed-case suffix/filename combinations. The selected
    # profile's physical suffixes are preserved for generated/expected paths;
    # normalization is classification only.
    try:
        if profile.reports_discovery == "recursive_contained":
            # M003-S02 (owner note 008): one bounded deterministic contained
            # inventory beneath the exact resolved configured reviews root.
            inventory = _contained_review_inventory(reviews_dir_abs, reviews_rel)
        else:
            inventory = sorted(
                (
                    (entry, f"{reviews_rel}/{entry.name}")
                    for entry in reviews_dir_abs.iterdir()
                    if entry.is_file()
                ),
                key=lambda pair: pair[1],
            )
    except (OSError, RuntimeError, _DiscoveryBoundExceeded):
        return _with_rework_declarations(
            root,
            _AcceptanceEvidence(
                (),
                (),
                (),
                (),
                (
                    _make_authority_defect(
                        _AuthorityDefectKind.UNRESOLVABLE_AUTHORITY_ROOT, reviews_rel
                    ),
                ),
            ),
        )
    record_suffix_cf = profile.verdict_record_suffix.casefold()
    report_suffix_cf = profile.review_report_suffix.casefold()
    record_entries: list[tuple[Path, str]] = []
    report_entries: list[tuple[Path, str]] = []
    for entry, entry_rel in inventory:
        name_cf = entry.name.casefold()
        if name_cf.endswith(record_suffix_cf):
            record_entries.append((entry, entry_rel))
        elif name_cf.endswith(report_suffix_cf):
            report_entries.append((entry, entry_rel))

    highest_report_rounds: dict[str, int] = {}
    for report, _ in report_entries:
        match = report_re.match(report.name)
        if not match:
            continue
        slice_id = f"{match.group('milestone').upper()}-{match.group('slice').upper()}"
        round_value = _artifact_correction_round(report.name, profile.review_report_suffix)
        highest_report_rounds[slice_id] = max(
            round_value, highest_report_rounds.get(slice_id, round_value)
        )

    accepted: list[str] = []
    passing_reports: dict[str, str] = {}
    for report, report_entry_rel in report_entries:
        match = report_re.match(report.name)
        if not match:
            continue
        if not _resolved_within_reviews(report):
            # A suffix-matching authority report resolving outside the
            # configured reviews directory is typed fail-closed state even
            # when no record cites it; its verdict bytes are never parsed.
            authority_defects.append(
                _make_authority_defect(
                    _AuthorityDefectKind.ESCAPED_AUTHORITY_REPORT,
                    report_entry_rel,
                )
            )
            continue
        slice_id = f"{match.group('milestone').upper()}-{match.group('slice').upper()}"
        if (
            _artifact_correction_round(report.name, profile.review_report_suffix)
            < highest_report_rounds[slice_id]
        ):
            continue
        if not _has_pass_verdict(report):
            continue
        passing_reports[report_entry_rel] = slice_id
        if slice_id not in accepted:
            accepted.append(slice_id)

    contradictions: list[_VerdictRecordContradiction] = []
    receipted: set[str] = set()
    closure_receipts: list[_ClosureReceipt] = []
    for record, record_rel in record_entries:
        match = record_re.match(record.name)
        if not match:
            continue
        record_slice = f"{match.group('milestone').upper()}-{match.group('slice').upper()}"
        if (
            _artifact_correction_round(record.name, profile.verdict_record_suffix)
            < highest_report_rounds.get(record_slice, 1)
        ):
            continue
        stem = record.name[: -len(profile.verdict_record_suffix)]
        # The same-directory sibling is the safe expected label, so nested
        # records pair beside themselves rather than at the reviews root.
        record_dir_rel = record_rel.rsplit("/", 1)[0]
        expected_rel = f"{record_dir_rel}/{stem}{profile.review_report_suffix}"
        report_rel = expected_rel
        closure = _ClosureFields(valid=False)
        kind: _RecordContradictionKind | None = None
        if not _resolved_within_reviews(record):
            kind = _RecordContradictionKind.RESOLVED_ESCAPE
        if kind is None:
            citation, defect, closure = _read_record_evidence(record, profile)
            if defect == "unreadable_record":
                kind = _RecordContradictionKind.UNREADABLE_RECORD
            elif defect == "ambiguous_source":
                kind = _RecordContradictionKind.AMBIGUOUS_SOURCE
            elif defect == "unsafe_citation":
                kind = _RecordContradictionKind.UNSAFE_CITATION
            elif defect == "no_source":
                kind = _RecordContradictionKind.MISSING_SOURCE
            elif citation:
                report_rel = citation
                cited_match = report_re.match(citation.rsplit("/", 1)[-1])
                if cited_match is None:
                    kind = _RecordContradictionKind.UNSAFE_CITATION
                else:
                    cited_slice = (
                        f"{cited_match.group('milestone').upper()}-"
                        f"{cited_match.group('slice').upper()}"
                    )
                    if cited_slice != record_slice:
                        kind = _RecordContradictionKind.DIFFERENT_SLICE
        if kind is None:
            report_abs = root / report_rel
            if not _resolved_within_reviews(report_abs):
                kind = _RecordContradictionKind.RESOLVED_ESCAPE
            elif not report_abs.is_file():
                kind = _RecordContradictionKind.MISSING_REPORT
            else:
                parse = parse_review_report_verdict(
                    ReviewReportVerdictParseCommand(path=report_abs)
                )
                if not parse.valid or parse.verdict is None:
                    kind = _RecordContradictionKind.UNPARSEABLE_REPORT
                elif parse.verdict != ReviewVerdict.PASS:
                    kind = _RecordContradictionKind.NON_PASS_REPORT
        if kind is not None:
            contradictions.append(
                _VerdictRecordContradiction(
                    kind=kind,
                    record_path=record_rel,
                    report_path=report_rel,
                    slice_id=record_slice,
                    diagnostic=_cap_evidence_diagnostic(
                        _contradiction_message(kind, record_rel, report_rel)
                    ),
                )
            )
        else:
            receipted.add(report_rel)
            if (
                closure.valid
                and closure.verdict == ReviewVerdict.PASS.value
                and closure.kind == NextActionKind.MILESTONE_COMPLETE.value
                and closure.next_slice_none
                and closure.slice_id.upper() == record_slice
            ):
                closure_receipts.append(
                    _ClosureReceipt(
                        record_path=record_rel,
                        report_path=report_rel,
                        slice_id=closure.slice_id,
                        milestone_id=closure.milestone_id,
                    )
                )

    unrecorded = tuple(rel for rel in passing_reports if rel not in receipted)
    return _with_rework_declarations(
        root,
        _AcceptanceEvidence(
            tuple(accepted),
            tuple(passing_reports),
            unrecorded,
            tuple(contradictions),
            tuple(authority_defects),
            tuple(closure_receipts),
        ),
    )


def _review_output_path_from_prompt(path: Path) -> str:
    """Return the review-report path a review prompt declares, or ``""``.

    Supports the legacy generated inline shape in any live placement, the
    configured generated instruction in ``Definition Of Done``, and the
    hand-authored ``Review Output Location`` section. Fenced examples,
    indented code, and configured-shape lookalikes in unrelated sections remain
    inert. Deterministic precedence is legacy inline, configured instruction,
    then historical section. Returns ``""`` for bare review prompts that
    declare no output location. Never raises.
    """

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    inline_path = ""
    configured_path = ""
    historical_path = ""
    section = ""
    for event in _live_markdown_events(content.splitlines()):
        if event.heading is not None:
            kind, level, heading_lines = event.heading
            if kind == "atx":
                raw = heading_lines[0][level:].strip()
            else:
                raw = " ".join(line.strip() for line in heading_lines)
            section = " ".join(raw.lower().rstrip(":!?.;,").split())
            continue
        line = event.line or ""

        if not inline_path:
            match = _REVIEW_OUTPUT_INLINE_RE.search(line)
            if match:
                inline_path = match.group(1).strip()
        if section == "definition of done" and not configured_path:
            match = _REVIEW_OUTPUT_CONFIGURED_RE.fullmatch(line)
            if match:
                configured_path = match.group(1).strip()
        elif section == "review output location" and not historical_path:
            match = _REVIEW_REPORT_BACKTICK_RE.search(line)
            if match:
                historical_path = match.group(1).strip()
    return inline_path or configured_path or historical_path


# ---------------------------------------------------------------------------
# M008-S03: make-review-prompt helpers
# ---------------------------------------------------------------------------


def _sections_from_text(content: str) -> dict[str, str]:
    """Parse ATX-heading sections into a dict mapping normalised heading to body.

    Normalisation strips leading ``#`` markers, lowercases, trims surrounding
    whitespace, strips trailing punctuation ```:!?.;,``, and collapses
    interior whitespace runs to a single space.  When the same normalised
    heading appears more than once the last occurrence wins.  Text before the
    first heading is discarded.
    """
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_body: list[str] = []
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            hash_count = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[hash_count:]
            if 1 <= hash_count <= 6 and rest.startswith(" "):
                if current_heading is not None:
                    sections[current_heading] = "\n".join(current_body).strip()
                raw = rest.strip().lower().rstrip(":!?.;,")
                current_heading = " ".join(raw.split())
                current_body = []
                continue
        if current_heading is not None:
            current_body.append(line)
    if current_heading is not None:
        sections[current_heading] = "\n".join(current_body).strip()
    return sections


def _extract_bullet_backtick_items(body: str) -> tuple[str, ...]:
    """Extract backtick-wrapped values from bullet lines in ``body``."""
    results: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        if re.match(r"^\s*[-*]\s+", line):
            m = _PROMPT_BACKTICK_RE.search(line)
            if m:
                val = m.group(1).strip()
                if val and val not in seen:
                    seen.add(val)
                    results.append(val)
    return tuple(results)


def _extract_bullet_text_items(body: str) -> tuple[str, ...]:
    """Extract plain text from bullet lines in ``body``."""
    results: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            val = m.group(1).strip()
            if val and val not in seen:
                seen.add(val)
                results.append(val)
    return tuple(results)


# ---------------------------------------------------------------------------
# M003-S01: independent workflow-metadata regions (private, bounded, one parse)
# ---------------------------------------------------------------------------

_CONCEPT_FRONTMATTER_KEYS: tuple[str, str] = ("type", "framework_profile")
"""Literal keys that mark a leading block as OKF concept frontmatter.

Concept keys are observations used solely to exclude the leading block from
routing; they grant no validity, acceptance, frontier, gate, safety, or write
authority.
"""

_DUAL_CONFLICT_MESSAGE = (
    "dual workflow routing conflict: {roles} differ between "
    "leading metadata frame and fenced workflow metadata block"
)
"""The bounded dual-region conflict diagnostic (M003-S02, final M001 rule).

Rendered with the conflicting canonical semantic role labels (``milestone``,
``slice``, ``title``, in that order, joined by ``, ``). Both fixed region
locations are named exactly; neither routing value, no file path, no raw
YAML, and no configured physical field name ever enters the message.
"""

_CANONICAL_ROLES: tuple[str, str, str] = ("milestone", "slice", "title")


def _flat_routing(mapping: dict[str, object]) -> dict[str, str]:
    """The deterministic flat routing surface for one region.

    String keys are stripped and lowercased, string values are stripped, and
    all-string normalized-key spellings keep the pinned last-occurrence
    behavior. This is the same normalization the native reader consumes.
    """

    flat: dict[str, str] = {}
    for key, item in mapping.items():
        if isinstance(key, str) and isinstance(item, str):
            flat[key.strip().lower()] = item.strip()
    return flat


def _dual_routing_conflicts(
    leading_flat: dict[str, str],
    fenced_flat: dict[str, str],
    routing_fields: tuple[str, str, str],
) -> tuple[str, ...]:
    """Canonical roles whose configured field differs across the two regions.

    A role is compared only when its configured field is present in both
    normalized mappings, and it conflicts when the two stripped string values
    are not exactly equal — no case-folding, backtick removal, syntax
    normalization, or derived-identity comparison. Roles are returned in
    canonical order ``milestone``, ``slice``, ``title``.
    """

    conflicts: list[str] = []
    for role, field in zip(_CANONICAL_ROLES, routing_fields):
        if (
            field in leading_flat
            and field in fenced_flat
            and leading_flat[field] != fenced_flat[field]
        ):
            conflicts.append(role)
    return tuple(conflicts)


def _leading_frame_region(content: str) -> tuple[str | None, str]:
    """Return ``(region_text, error)`` for the first-line ``---`` frame.

    A leading region exists only when the document's first line is exactly
    the ``---`` opener and a later exact ``---`` line closes it. An opener
    with no closer is an unterminated frame: a bounded owned error, and no
    fallback to any fenced region.
    """

    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return None, ""
    for idx in range(1, len(lines)):
        if lines[idx] == "---":
            return "\n".join(lines[1:idx]) + "\n", ""
    return None, "leading metadata frame is unterminated (no closing --- line)"


def _fenced_workflow_region(content: str) -> tuple[str | None, str]:
    """Return ``(region_text, error)`` for the first fenced YAML/YML block.

    Only the first declared fenced block is observed; later example blocks
    are never scanned for a more convenient answer. A missing closing fence
    is a bounded owned error and suppresses routing.
    """

    in_block = False
    body: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not in_block:
            if stripped.lower() in ("```yaml", "```yml"):
                in_block = True
            continue
        if stripped.startswith("```"):
            return "\n".join(body) + "\n", ""
        body.append(line)
    if in_block:
        return None, "fenced workflow metadata block is unterminated (no closing fence)"
    return None, ""


def _load_workflow_region(text: str, region: str) -> tuple[YamlDocument | None, str]:
    """Load one present region through the bounded boundary exactly once.

    Boundary failures (syntax, duplicates, multiple documents, tags, and
    every resource limit) fail closed with a bounded owned string naming the
    region and the stable category — never hostile YAML text or parser
    exception text. Classification and schema validation happen afterwards
    without reparsing.
    """

    try:
        return load_yaml_bytes(text.encode("utf-8")), ""
    except YamlBoundaryError as exc:
        return None, f"{region} workflow metadata region refused: {exc.category.value}"


def _is_concept_frontmatter(document: YamlDocument) -> bool:
    """Whether a loaded leading document is concept-only frontmatter.

    Classified before any native workflow-schema validation: a top-level
    mapping that contains the exact literal string key ``type`` or
    ``framework_profile``. The keys are matched literally — never trimmed,
    lowercased, aliased, or inferred — and their values and any OKF/profile
    validity are never evaluated. Unrelated boundary-accepted mapping-key
    types (integer, boolean, or other shapes) do not block this
    classification; only when neither literal concept key is present does the
    leading region remain a legacy workflow candidate subject to the complete
    native workflow schema, including its string-key rule. Concept-only
    mappings carry no native routing authority, and their routing-shaped
    values, other key/value shapes, and safe boundary-parsed flow,
    anchor/alias, or merge features cannot invalidate or replace an otherwise
    valid fenced workflow identity.
    """

    value = document.value
    if not isinstance(value, dict):
        return False
    return any(key in value for key in _CONCEPT_FRONTMATTER_KEYS)


def _apply_workflow_schema(
    document: YamlDocument, region: str, routing_fields: tuple[str, str, str]
) -> str:
    """The private native workflow schema over an already loaded document.

    Exactly one mapping with string keys; every original entry whose key
    normalizes (strip/lowercase, the same normalization native routing uses)
    to a configured milestone/slice/title field must have a string value —
    validated per original entry *before* normalized-key collapse, so no
    case, whitespace, or entry-order variant can hide a YAML null, boolean,
    number, sequence, or mapping occurrence. When every matching occurrence
    is a string, the existing deterministic normalized behavior continues
    unchanged; this adds no duplicate policy for multiple string-valued
    spellings. Unknown fields carry no authority. An empty region is an empty
    mapping. Returns a bounded owned error string, or ``""`` when the
    document satisfies the schema.
    """

    features = document.features
    if features.has_merge_keys:
        return f"{region} workflow metadata does not allow merge keys"
    if features.has_anchors or features.has_aliases:
        return f"{region} workflow metadata does not allow anchors or aliases"
    if features.has_explicit_tags:
        return f"{region} workflow metadata does not allow explicit tags"
    if features.has_flow_collections:
        return f"{region} workflow metadata does not allow flow collections"
    value = document.value
    if value is None:
        return ""
    if not isinstance(value, dict):
        return f"{region} workflow metadata region must be a single mapping"
    for key in value:
        if not isinstance(key, str):
            return f"{region} workflow metadata keys must be strings"
    for key, item in value.items():
        if key.strip().lower() in routing_fields and not isinstance(item, str):
            return f"{region} workflow routing values must be strings"
    return ""


def _routing_field_names(profile: LayoutProfile) -> tuple[str, str, str]:
    return (
        profile.front_matter_milestone_field.strip().lower(),
        profile.front_matter_slice_field.strip().lower(),
        profile.front_matter_title_field.strip().lower(),
    )


def _region_has_routing(mapping: dict[str, object], routing_fields: tuple[str, str, str]) -> bool:
    """Whether a parsed region carries a configured milestone or slice key."""

    normalized = {key.strip().lower() for key in mapping}
    return bool(normalized & {routing_fields[0], routing_fields[1]})


def _workflow_selected_mapping(
    content: str, profile: LayoutProfile
) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    """Observe both metadata regions and select the raw routing mapping.

    Identical selection semantics to :func:`_workflow_routing_mapping` (each
    region observed at most once, dual-conflict refusal, leading-wins on a
    conflict-free dual), but returns the selected *raw* mapping so callers
    that need non-routing metadata values (for example the validated
    ``round`` used by workflow-metadata pairing, M003-S02) share the one
    observation instead of re-framing regions.

    Region A (first-line ``---`` frame) and region B (first fenced YAML
    workflow block) are framed and loaded independently through the accepted
    bounded boundary, each at most once (M003-S01). A loaded leading mapping
    is classified as concept-only *before* the native workflow schema when it
    carries a literal ``type`` or ``framework_profile`` key; concept-only
    bytes never route and cannot invalidate an otherwise valid fenced
    identity. Every other present region receives the native workflow schema.

    The final dual-region rule (M003-S02): when a legacy-leading workflow
    region and a fenced workflow region both carry routing fields, each
    canonical role (``milestone``, ``slice``, ``title``) whose configured
    field is present in both normalized mappings is compared by exact
    stripped-string equality. Any difference refuses deterministically with
    the bounded conflict diagnostic and no identity; a conflict-free dual
    case selects the complete normalized leading mapping, never merged with
    or supplemented from the fenced mapping.

    Returns ``(selected_mapping_or_none, errors)``. Any malformed or
    unterminated present region fails closed before comparison: no identity,
    no fallback to the other region. Never raises for constructible input.
    """

    routing_fields = _routing_field_names(profile)
    errors: list[str] = []

    leading_text, leading_error = _leading_frame_region(content)
    fenced_text, fenced_error = _fenced_workflow_region(content)

    leading_map: dict[str, object] | None = None
    leading_is_concept = False
    if leading_error:
        errors.append(leading_error)
    elif leading_text is not None:
        document, region_error = _load_workflow_region(leading_text, "leading")
        if region_error:
            errors.append(region_error)
        elif _is_concept_frontmatter(document):
            leading_is_concept = True
            leading_map = document.value
        else:
            schema_error = _apply_workflow_schema(document, "leading", routing_fields)
            if schema_error:
                errors.append(schema_error)
            else:
                leading_map = document.value if document.value is not None else {}

    fenced_map: dict[str, object] | None = None
    if fenced_error:
        errors.append(fenced_error)
    elif fenced_text is not None:
        document, region_error = _load_workflow_region(fenced_text, "fenced")
        if region_error:
            errors.append(region_error)
        else:
            schema_error = _apply_workflow_schema(document, "fenced", routing_fields)
            if schema_error:
                errors.append(schema_error)
            else:
                fenced_map = document.value if document.value is not None else {}

    if errors:
        return {}, tuple(errors)

    leading_routes = (
        leading_map is not None
        and not leading_is_concept
        and _region_has_routing(leading_map, routing_fields)
    )
    fenced_routes = fenced_map is not None and _region_has_routing(fenced_map, routing_fields)

    if leading_routes and fenced_routes:
        # M003-S02: compare the same normalized surface the reader consumes.
        leading_flat = _flat_routing(leading_map)
        fenced_flat = _flat_routing(fenced_map)
        conflicts = _dual_routing_conflicts(leading_flat, fenced_flat, routing_fields)
        if conflicts:
            return None, (_DUAL_CONFLICT_MESSAGE.format(roles=", ".join(conflicts)),)
        return leading_map, ()

    selected: dict[str, object] | None
    if fenced_routes:
        selected = fenced_map
    elif leading_is_concept:
        selected = fenced_map
    elif leading_map is not None:
        selected = leading_map
    else:
        selected = fenced_map

    return selected, ()


def _workflow_routing_mapping(
    content: str, profile: LayoutProfile
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Observe both metadata regions and select the flat routing mapping.

    Thin normalization wrapper over :func:`_workflow_selected_mapping`; the
    documented region, schema, concept, and dual-conflict semantics live
    there and are unchanged.
    """

    selected, errors = _workflow_selected_mapping(content, profile)
    if errors:
        return {}, errors
    return (_flat_routing(selected) if selected else {}), ()


def _workflow_round_value(mapping: dict[str, object]) -> int | None:
    """Return the validated ``round`` metadata value, or ``None``.

    Accepted only after the region schema validated the mapping: a plain
        integer or all-digit string in ``1..999``. Anything else — booleans,
    other types, zero/negative, or an out-of-range run — is ``None`` (no
    repair, no filename inference).
    """

    for key, value in mapping.items():
        if not isinstance(key, str) or key.strip().lower() != "round":
            continue
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 1 <= value <= _MAX_WORKFLOW_ROUND else None
        if isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
            return number if 1 <= number <= _MAX_WORKFLOW_ROUND else None
        return None
    return None


_PAIRING_SLICE_LINE_RE = re.compile(
    r"^Detailed roadmap slice: `(?P<value>[^`]{1,200})`$", re.MULTILINE
)


@dataclass(frozen=True)
class _ReviewPromptPairing:
    """Validated pairing facts of one review prompt (M003-S02, private).

    ``valid`` is ``False`` for unreadable prompts, malformed or
    dual-conflicting metadata (their existing owned refusals are
    preserved), and prompts without a validated slice identity; such
    prompts never pair and are never repaired from filenames.
    ``coding_refs`` holds the exact backticked repository-relative
    coding-prompt paths the review prompt explicitly references.
    """

    valid: bool
    slice_id: str = ""
    round_value: int | None = None
    coding_refs: tuple[str, ...] = ()


def _review_prompt_pairing_facts(
    artifact: PromptArtifact, root: Path, profile: LayoutProfile
) -> _ReviewPromptPairing:
    """Read one review prompt's validated pairing identity. Never raises.

    Identity comes from the validated workflow-metadata regions when
    present; generated review prompts without metadata regions fall back to
    the exact generated pairing line (``Detailed roadmap slice:``). Round
    metadata is used only after validation. Parity, filename slugs, casing
    repair, and proximity never contribute.
    """

    path = root / profile.review_prompt_dir / artifact.filename
    try:
        raw = path.read_bytes()
    except OSError:
        return _ReviewPromptPairing(valid=False)
    if len(raw) > _MAX_EVIDENCE_BYTES:
        return _ReviewPromptPairing(valid=False)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _ReviewPromptPairing(valid=False)

    slice_id = ""
    round_value: int | None = None
    if profile.parse_front_matter:
        selected, region_errors = _workflow_selected_mapping(content, profile)
        if region_errors:
            return _ReviewPromptPairing(valid=False)
        if selected:
            flat = _flat_routing(selected)
            _milestone, slice_candidate, _title = _meta_from_front_matter(
                flat,
                milestone_field=profile.front_matter_milestone_field,
                slice_field=profile.front_matter_slice_field,
                title_field=profile.front_matter_title_field,
            )
            if slice_candidate:
                slice_id = slice_candidate
                round_value = _workflow_round_value(selected)

    if not slice_id:
        line_match = _PAIRING_SLICE_LINE_RE.search(content)
        if line_match:
            raw_value = line_match.group("value").strip()
            colon = raw_value.find(":")
            slice_id = (raw_value[:colon] if colon > 0 else raw_value).strip()
    if not slice_id:
        return _ReviewPromptPairing(valid=False)

    # Live-line reference extraction through the accepted CommonMark
    # boundary: fenced/indented code never contributes a reference, and
    # backtick spans are matched per live line so block fences cannot
    # mispair with inline path spans.
    prefix = profile.coding_prompt_dir.rstrip("/") + "/"
    refs: list[str] = []
    in_fence: tuple[str, int] | None = None
    for line in content.splitlines():
        if in_fence is not None:
            if _closing_fence(line, in_fence[0], in_fence[1]):
                in_fence = None
            continue
        if _is_indented_code(line):
            continue
        fence_open = _FENCE_OPEN.match(line)
        if fence_open:
            fence_run = fence_open.group(1)
            in_fence = (fence_run[0], len(fence_run))
            continue
        for span in _PROMPT_BACKTICK_RE.findall(line):
            candidate = span.strip().replace("\\", "/")
            if (
                candidate.startswith(prefix)
                and candidate.endswith(".md")
                and candidate not in refs
            ):
                refs.append(candidate)
    return _ReviewPromptPairing(
        valid=True,
        slice_id=slice_id,
        round_value=round_value,
        coding_refs=tuple(refs),
    )


def _select_paired_review_prompt(
    coding_artifact: PromptArtifact,
    coding_slice_id: str,
    prompt_artifacts: tuple[PromptArtifact, ...],
    root: Path,
    profile: LayoutProfile,
) -> tuple[PromptArtifact | None, bool]:
    """Select the review prompt paired to one coding prompt by metadata.

    Returns ``(selected, ambiguous)``. Qualification: a validated review
    prompt whose slice identity equals the coding prompt's slice and whose
    explicit coding-prompt references (when present) include the coding
    prompt's exact path. Disambiguation order: an explicit matching
    reference beats slice-only candidates; validated round metadata may
    then disambiguate (highest validated round). Any remaining multiplicity
    is ambiguous and fails closed — never "latest wins".
    """

    slice_upper = coding_slice_id.upper()
    coding_path = f"{profile.coding_prompt_dir}/{coding_artifact.filename}"
    candidates: list[tuple[PromptArtifact, _ReviewPromptPairing]] = []
    for artifact in prompt_artifacts:
        if artifact.kind != PromptKind.REVIEW:
            continue
        facts = _review_prompt_pairing_facts(artifact, root, profile)
        if not facts.valid or facts.slice_id.upper() != slice_upper:
            continue
        if facts.coding_refs and coding_path not in facts.coding_refs:
            continue
        candidates.append((artifact, facts))
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0][0], False
    explicit = [
        pair for pair in candidates if coding_path in pair[1].coding_refs
    ]
    if len(explicit) == 1:
        return explicit[0][0], False
    if explicit:
        candidates = explicit
    round_values = tuple(pair[1].round_value for pair in candidates)
    if all(value is not None for value in round_values):
        highest = max(value for value in round_values if value is not None)
        top = [pair for pair in candidates if pair[1].round_value == highest]
        if len(top) == 1:
            return top[0][0], False
    return None, True


def _slice_slug(slice_id: str) -> str:
    """Slugify a slice id (``M001-S01`` -> ``m001_s01``) for path derivation."""

    return re.sub(r"[^a-z0-9]+", "_", slice_id.strip().lower()).strip("_")


# ---------------------------------------------------------------------------
# M003-S03: configured scaffold rendering seam (private)
# ---------------------------------------------------------------------------


def _configured_posture_reading(
    status: ProjectStatus, profile: LayoutProfile
) -> tuple[str, ...]:
    """The selected memory-posture Read-First entry for a configured scaffold.

    M011-S01 (D3): returns a single-element tuple with the selected profile's
    posture file when the selected memory mode is ``lightweight`` or ``llloom``
    (signalled by the mode-aware memory backend on the already-composed status),
    and an empty tuple for mode ``none``/missing/invalid. Never queries memory
    and never inserts raw memory command output.
    """

    if status.memory.backend in ("lightweight", "llloom") and profile.llloom_posture_file:
        return (profile.llloom_posture_file,)
    return ()


def _dedup_append(existing: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    """Append ``additions`` not already present by exact raw value, order-preserving."""

    result = list(existing)
    seen = set(existing)
    for item in additions:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return tuple(result)


def _coding_scaffold_slots(
    profile: LayoutProfile,
    template: CodingPromptTemplate,
    self_report_contract: tuple[str, ...] = (),
) -> dict:
    """The exact coding field-to-section mapping for the scaffold renderer.

    Keyed by normalized owned section heading; role-configured names are read
    from the selected profile. Pinned in tests.
    """

    return {
        "active workspaces": ScaffoldSlot(
            "list", tuple(template.scope_paths), label="active workspaces"
        ),
        normalize_section(profile.required_reading_section): ScaffoldSlot(
            "list", tuple(template.required_reading), label="read first"
        ),
        normalize_section(profile.task_section): ScaffoldSlot(
            "prose",
            (
                f"Implement {template.slice_id}: {template.title}.\n\n"
                f"{template.role_instructions}",
            ),
            label="task",
        ),
        normalize_section(profile.non_goals_section): ScaffoldSlot(
            "list", tuple(template.non_goals), label="non-goals"
        ),
        normalize_section(profile.verification_section): ScaffoldSlot(
            "list", tuple(template.verification_commands), label="verification"
        ),
        normalize_section(profile.self_report_section): ScaffoldSlot(
            "path", (template.self_report_path,), label="self-report"
        ),
        "definition of done": ScaffoldSlot(
            "list",
            tuple(template.definition_of_done) + tuple(self_report_contract),
            label="definition of done",
        ),
    }


def _review_read_first_values(template: ReviewPromptTemplate) -> tuple[str, ...]:
    """The complete typed review reading set in deterministic order (M003-S03).

    Built as typed entries first: every ``required_reading`` raw value in
    tuple order, the raw coding-prompt path, the raw self-report path, and
    every raw expected-changed-file value in evidence order. Exact
    first-occurrence de-duplication compares the raw string value; only a
    surviving entry receives its role-specific display form (backticked
    reading entry, labeled coding-prompt path, labeled self-report path, or
    the plain changed-file form). Case, whitespace, punctuation, and slash
    direction are preserved exactly.
    """

    typed_entries: list[tuple[str, str]] = [(entry, "reading") for entry in template.required_reading]
    typed_entries.append((template.coding_prompt_path, "coding_prompt"))
    typed_entries.append((template.self_report_path, "self_report"))
    typed_entries.extend((entry, "changed_file") for entry in template.expected_changed_files)

    seen: set[str] = set()
    values: list[str] = []
    for raw, role in typed_entries:
        if raw in seen:
            continue
        seen.add(raw)
        if role == "reading":
            values.append(f"`{raw}`")
        elif role == "coding_prompt":
            values.append(f"coding prompt under review: `{raw}`")
        elif role == "self_report":
            values.append(f"coder self-report: `{raw}`")
        else:
            values.append(raw)
    return tuple(values)


def _review_scaffold_slots(
    profile: LayoutProfile,
    template: ReviewPromptTemplate,
    review_report_contract: tuple[str, ...] = (),
) -> dict:
    """The exact review field-to-section mapping for the scaffold renderer."""

    return {
        "review objective": ScaffoldSlot(
            "prose",
            (
                f"Review {template.slice_id}: {template.title}.\n\n"
                f"{template.role_instructions}",
            ),
            label="review objective",
        ),
        normalize_section(profile.required_reading_section): ScaffoldSlot(
            "list", _review_read_first_values(template), label="read first"
        ),
        normalize_section(profile.verification_section): ScaffoldSlot(
            "list", tuple(template.verification_commands), label="verification"
        ),
        normalize_section(profile.non_goals_section): ScaffoldSlot(
            "list", tuple(template.non_goals), label="non-goals"
        ),
        "definition of done": ScaffoldSlot(
            "list",
            (
                f"Write the review report at `{template.review_output_path}`.",
            )
            + tuple(review_report_contract),
            label="definition of done",
        ),
    }


_CODING_REPORT_FORMAT_CONTRACT_ERROR = (
    "coding report-format contract defect in package-owned definition of done section"
)
_REVIEW_REPORT_FORMAT_CONTRACT_ERROR = (
    "review report-format contract defect in package-owned definition of done section"
)


def _report_format_contract_present(
    content: str, contract: tuple[str, ...]
) -> bool:
    """Whether every contract line is one exact renderer-tolerated list item."""

    if not contract:
        return False
    lines = {line.strip() for line in content.splitlines()}
    return all(f"- {line}" in lines for line in contract)


def _definition_of_done_missing_error(
    errors: tuple[str, ...], required_sections: tuple[str, ...]
) -> str | None:
    """The exact missing error for the package-owned section, when present."""

    for position, name in enumerate(required_sections, 1):
        if normalize_section(name) == "definition of done":
            missing = f"required section {position} is missing"
            return missing if missing in errors else None
    return None


def _replace_definition_of_done_missing_error(
    errors: tuple[str, ...],
    required_sections: tuple[str, ...],
    replacement: str,
) -> tuple[str, ...]:
    """Replace only the package-owned missing error, preserving its peers."""

    missing = _definition_of_done_missing_error(errors, required_sections)
    if missing is None:
        return errors
    return tuple(replacement if error == missing else error for error in errors)


def _round_trip_identity_errors(
    content: str,
    profile: LayoutProfile,
    milestone_id: str,
    slice_id: str,
    self_report_path: str,
    owner: str,
    *,
    check_self_report: bool,
) -> tuple[str, ...]:
    """Prove a rendered body routes back to its typed identity (M003-S03).

    The accepted M003-S01/S02 reader must find exactly one workflow routing
    region carrying the typed milestone/slice. For coding prompts (which are
    later re-parsed as coding prompts) the self-report path derivation must
    also match the typed path. One bounded owned error or none.
    """

    routing, region_errors = _workflow_routing_mapping(content, profile)
    if region_errors:
        return (
            f"configured {owner} template does not yield exactly one workflow "
            "routing region",
        )
    routed_milestone, routed_slice, _ = _meta_from_front_matter(
        routing,
        milestone_field=profile.front_matter_milestone_field,
        slice_field=profile.front_matter_slice_field,
        title_field=profile.front_matter_title_field,
    )
    if routed_milestone != milestone_id or routed_slice != slice_id:
        return (f"rendered {owner} prompt would not round-trip to its typed identity",)
    if not check_self_report:
        return ()
    sections = _sections_from_text(content)
    sr_body = sections.get(profile.self_report_section, "")
    found = ""
    if sr_body:
        match = _PROMPT_SELF_REPORT_PATH_RE.search(sr_body)
        if match:
            found = match.group(1).strip()
    if found != self_report_path:
        return (f"rendered {owner} prompt would change its self-report path derivation",)
    return ()


def _reconciled_preview(preview, render):
    """Reconcile a plan preview to the configured render outcome (M003-S03).

    A failed configured render means no write of any kind: the returned
    preview keeps its existing shape but reports ``valid=False``,
    ``would_write=False`` (``wrote`` is always ``False``), and carries the
    deterministic render errors. A successful render keeps the historical
    preview untouched. Shared by the coding and review plan builders.
    """

    if render.valid:
        return preview
    return replace(preview, valid=False, would_write=False, errors=tuple(render.errors))


def _render_coding_from_scaffold(
    status: ProjectStatus,
    profile: LayoutProfile,
    template: CodingPromptTemplate,
    self_report_contract: tuple[str, ...] | None = None,
) -> CodingPromptRenderResult:
    """Render the coding prompt through the configured scaffold (M003-S03)."""

    contract = (
        self_report_format_contract(self_report_schema_for_profile(profile))
        if self_report_contract is None
        else self_report_contract
    )
    content, errors = render_configured_scaffold(
        root=status.root,
        template_rel=profile.coding_template,
        required_sections=profile.required_coding_prompt_sections,
        workflow_values=(
            (profile.front_matter_milestone_field, template.milestone_id),
            (profile.front_matter_slice_field, template.slice_id),
        ),
        section_slots=_coding_scaffold_slots(profile, template, contract),
        owner="coding",
    )
    errors = _replace_definition_of_done_missing_error(
        errors,
        profile.required_coding_prompt_sections,
        _CODING_REPORT_FORMAT_CONTRACT_ERROR,
    )
    if not errors:
        errors = _round_trip_identity_errors(
            content,
            profile,
            template.milestone_id,
            template.slice_id,
            template.self_report_path,
            "coding",
            check_self_report=True,
        )
    if not errors and not _report_format_contract_present(content, contract):
        errors = (_CODING_REPORT_FORMAT_CONTRACT_ERROR,)
    return CodingPromptRenderResult(
        content="" if errors else content,
        valid=not errors,
        errors=tuple(errors),
    )


def _render_review_from_scaffold(
    status: ProjectStatus,
    profile: LayoutProfile,
    template: ReviewPromptTemplate,
    review_report_contract: tuple[str, ...] | None = None,
) -> ReviewPromptRenderResult:
    """Render the review prompt through the configured scaffold (M003-S03)."""

    contract = (
        review_report_format_contract()
        if review_report_contract is None
        else review_report_contract
    )
    content, errors = render_configured_scaffold(
        root=status.root,
        template_rel=profile.review_template,
        required_sections=profile.required_review_prompt_sections,
        workflow_values=(
            (profile.front_matter_milestone_field, template.milestone_id),
            (profile.front_matter_slice_field, template.slice_id),
        ),
        section_slots=_review_scaffold_slots(profile, template, contract),
        owner="review",
    )
    errors = _replace_definition_of_done_missing_error(
        errors,
        profile.required_review_prompt_sections,
        _REVIEW_REPORT_FORMAT_CONTRACT_ERROR,
    )
    if not errors:
        errors = _round_trip_identity_errors(
            content,
            profile,
            template.milestone_id,
            template.slice_id,
            template.self_report_path,
            "review",
            check_self_report=False,
        )
    if not errors and not _report_format_contract_present(content, contract):
        errors = (_REVIEW_REPORT_FORMAT_CONTRACT_ERROR,)
    return ReviewPromptRenderResult(
        content="" if errors else content,
        valid=not errors,
        errors=tuple(errors),
    )


def _meta_from_front_matter(
    fm: dict[str, str],
    *,
    milestone_field: str = "milestone",
    slice_field: str = "slice",
    title_field: str = "title",
) -> tuple[str, str, str]:
    """Derive ``(milestone_id, slice_id, title)`` from front-matter fields.

    The front-matter keys that identify milestone/slice/title are configurable
    (``prompts.metadata.*`` in the layout config); they default to the v2 names.
    """

    milestone_id = fm.get(milestone_field.strip().lower(), "").strip().strip("`")
    raw_slice = fm.get(slice_field.strip().lower(), "").strip().strip("`")
    title = fm.get(title_field.strip().lower(), "").strip().strip("`")
    if milestone_id.upper() in ("", "TBD"):
        milestone_id = ""
    if raw_slice.upper() in ("", "TBD"):
        raw_slice = ""
    slice_id = ""
    if raw_slice:
        if "-" in raw_slice:
            slice_id = raw_slice
        elif milestone_id:
            slice_id = f"{milestone_id}-{raw_slice}"
        else:
            slice_id = raw_slice
    return milestone_id, slice_id, title


def _parse_coding_prompt_meta(
    artifact: PromptArtifact,
    project_root: Path,
    profile: LayoutProfile | None = None,
) -> CodingPromptMeta:
    """Parse metadata from a coding prompt artifact file.

    Reads the selected coding prompt file and extracts the fields needed to
    build a :class:`~frutlups.review_prompt_template.ReviewPromptTemplate`.
    Section names, the coding-prompt directory, report suffixes, and whether to
    read YAML front matter are taken from ``profile`` (defaulting to the legacy
    profile). For v2 profiles, milestone/slice come from front matter (with legacy
    body parsing as a fallback) and the self-report path is derived from the
    reviews directory + slice slug + suffix when the prompt only references the
    template schema. Returns a ``CodingPromptMeta`` with ``valid=False`` and
    deterministic errors on any parse failure. Never raises.
    """
    if profile is None:
        profile = legacy_profile()
    errors: list[str] = []
    sequence = artifact.sequence or 0
    filename = artifact.filename

    slug = ""
    if sequence:
        prefix = f"{sequence:03d}_"
        if filename.startswith(prefix) and filename.endswith(".md"):
            slug = filename[len(prefix) : -3]
    if not slug:
        slug = filename[:-3] if filename.endswith(".md") else filename

    coding_prompt_path = f"{profile.coding_prompt_dir}/{filename}"

    target = project_root / profile.coding_prompt_dir / filename
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read coding prompt: {exc}")
        return CodingPromptMeta(
            sequence=sequence,
            milestone_id="",
            slice_id="",
            title="",
            slug=slug,
            required_reading=(),
            coding_prompt_path=coding_prompt_path,
            self_report_path="",
            review_output_path="",
            non_goals=(),
            errors=tuple(errors),
            valid=False,
        )

    sections = _sections_from_text(content)

    milestone_id = ""
    slice_id = ""
    title = ""

    # v2/v3: prefer YAML workflow metadata for milestone/slice, read through the
    # independent two-region observation (M003-S01) with the configured
    # routing field names. Region failures are bounded owned diagnostics.
    region_errors: tuple[str, ...] = ()
    if profile.parse_front_matter:
        routing, region_errors = _workflow_routing_mapping(content, profile)
        errors.extend(region_errors)
        milestone_id, slice_id, title = _meta_from_front_matter(
            routing,
            milestone_field=profile.front_matter_milestone_field,
            slice_field=profile.front_matter_slice_field,
            title_field=profile.front_matter_title_field,
        )

    # Legacy (or v2 fallback): parse the roadmap-item section body. A region
    # error (including a dual-region conflict refusal) must not be repaired
    # from the body fallback (M003-S01); the fallback stays available only to
    # prompts with no region error.
    if (
        (not milestone_id or not slice_id)
        and profile.roadmap_item_section
        and not region_errors
    ):
        roadmap_body = sections.get(profile.roadmap_item_section, "")
        if roadmap_body:
            m = _PROMPT_MILESTONE_RE.search(roadmap_body)
            if m and not milestone_id:
                milestone_id = m.group(1).strip().split(":")[0].strip()
            s = _PROMPT_SLICE_RE.search(roadmap_body)
            if s and not slice_id:
                raw = s.group(1).strip()
                colon_idx = raw.find(":")
                if colon_idx > 0:
                    slice_id = raw[:colon_idx].strip()
                    if not title:
                        title = raw[colon_idx + 1 :].strip().strip("`").strip()
                else:
                    slice_id = raw.strip()

    if not title and slice_id:
        title = slug.replace("_", " ").strip()

    # Region-owned diagnostics are authoritative (M003-S02 correction): when a
    # region failure or conflict refusal already owns the outcome, the generic
    # missing-identity/path messages and the self-report/review derivation are
    # skipped, so a real dual conflict returns exactly its one owned
    # diagnostic and a completely empty identity/path tuple. With no region
    # errors, the historical generic behavior is unchanged.
    self_report_path = ""
    review_output_path = ""
    if not region_errors:
        if not milestone_id:
            errors.append("could not parse milestone_id from coding prompt")
        if not slice_id:
            errors.append("could not parse slice_id from coding prompt")
        if not title:
            errors.append("could not parse title from coding prompt")

        sr_body = sections.get(profile.self_report_section, "")
        if sr_body:
            m2 = _PROMPT_SELF_REPORT_PATH_RE.search(sr_body)
            if m2:
                self_report_path = m2.group(1).strip()
        # v2: when the prompt only references the template schema, derive the
        # path from the reviews directory + slice slug + configured suffix.
        if not self_report_path and profile.parse_front_matter and slice_id:
            self_report_path = (
                f"{profile.reviews_dir}/{_slice_slug(slice_id)}{profile.self_report_suffix}"
            )
        if not self_report_path:
            errors.append("could not parse self_report_path from coding prompt")

        if self_report_path:
            if self_report_path.endswith(profile.self_report_suffix):
                review_output_path = (
                    self_report_path[: -len(profile.self_report_suffix)]
                    + profile.review_report_suffix
                )
            else:
                errors.append(
                    "could not derive review_output_path: self_report_path does not "
                    f"end with {profile.self_report_suffix}"
                )

    required_reading = _extract_bullet_backtick_items(
        sections.get(profile.required_reading_section, "")
    )
    if not required_reading:
        required_reading = ("CLAUDE.md", "README.md")

    non_goals = _extract_bullet_text_items(sections.get(profile.non_goals_section, ""))

    return CodingPromptMeta(
        sequence=sequence,
        milestone_id=milestone_id,
        slice_id=slice_id,
        title=title,
        slug=slug,
        required_reading=required_reading,
        coding_prompt_path=coding_prompt_path,
        self_report_path=self_report_path,
        review_output_path=review_output_path,
        non_goals=non_goals,
        errors=tuple(errors),
        valid=not errors,
    )


@dataclass(frozen=True)
class _ReworkResolution:
    """Resolved append-only rework state for one planning snapshot."""

    active_declaration: ReworkDeclaration | None = None
    pending_slice: RoadmapSlice | None = None
    completed_slice_ids: tuple[str, ...] = ()
    diagnostics: tuple[tuple[str, str], ...] = ()


def _finalize_rework_resolution(
    evidence: _AcceptanceEvidence,
    resolution: _ReworkResolution,
) -> None:
    """Enrich one selected evidence snapshot with one immutable result.

    The raw collector object must be retained by identity, so the single
    internal ``object.__setattr__`` is centralized here. A second finalization
    is a programming error; ordinary consumers receive a frozen field whose
    value is itself frozen and has no mutable container operations.
    """

    if evidence.rework_resolution is not None:
        raise RuntimeError("rework resolution is already finalized")
    object.__setattr__(evidence, "rework_resolution", resolution)


@dataclass(frozen=True)
class _ReworkFreshAcceptance:
    """One declared slice's fresh-evidence result from a single observation."""

    accepted: bool = False
    diagnostic: tuple[str, str] | None = None


_REWORK_REVIEW_OUTPUT_UNRECOGNIZED = (
    "rework_review_output_declaration_unrecognized",
    "paired rework review prompt has no recognizable review-output declaration",
)


def _rework_self_report_valid(
    root: Path,
    profile: LayoutProfile,
    meta: CodingPromptMeta,
) -> bool:
    """Require the prompt-linked rework self-report to satisfy the live schema."""

    if not meta.valid or not meta.self_report_path:
        return False
    stub = CodingPromptTemplate(
        sequence=meta.sequence,
        milestone_id=meta.milestone_id or "UNKNOWN",
        slice_id=meta.slice_id or "UNKNOWN",
        slug=meta.slug or "unknown",
        title=meta.title or "unknown",
        role_instructions="coder",
        required_reading=("CLAUDE.md",),
        scope_paths=("08_pkg/",),
        non_goals=(),
        definition_of_done=("pass tests",),
        verification_commands=("python -m unittest",),
        self_report_path=meta.self_report_path,
    )
    validation = validate_expected_self_report(
        SelfReportValidationCommand(
            location=SelfReportLocationCommand(project_root=root, template=stub),
            schema=self_report_schema_for_profile(profile),
        )
    )
    return validation.valid


def _paired_review_for_rework(
    root: Path,
    profile: LayoutProfile,
    prompt_artifacts: tuple[PromptArtifact, ...],
    coding_artifact: PromptArtifact,
    meta: CodingPromptMeta,
) -> PromptArtifact | None:
    """Return the one independently paired review prompt, or ``None``."""

    if profile.prompt_pairing == "workflow_metadata":
        paired, ambiguous = _select_paired_review_prompt(
            coding_artifact,
            meta.slice_id,
            prompt_artifacts,
            root,
            profile,
        )
        return None if ambiguous else paired
    matches = [
        item
        for item in prompt_artifacts
        if item.kind == PromptKind.REVIEW and item.sequence == coding_artifact.sequence
    ]
    return matches[0] if len(matches) == 1 else None


def _rework_slice_has_fresh_acceptance(
    root: Path,
    profile: LayoutProfile,
    declaration: ReworkDeclaration,
    slice_id: str,
    prompt_artifacts: tuple[PromptArtifact, ...],
    evidence: _AcceptanceEvidence,
    upper_prompt_sequence: int | None = None,
) -> _ReworkFreshAcceptance:
    """Whether one declared slice has a complete post-declaration evidence chain."""

    candidates: list[tuple[int, PromptArtifact, CodingPromptMeta]] = []
    for artifact in prompt_artifacts:
        if artifact.kind != PromptKind.CODING or artifact.sequence is None:
            continue
        if artifact.sequence <= declaration.baseline_prompt_sequence:
            continue
        if upper_prompt_sequence is not None and artifact.sequence > upper_prompt_sequence:
            continue
        meta = _parse_coding_prompt_meta(artifact, root, profile)
        if meta.slice_id.upper() == slice_id.upper():
            candidates.append((artifact.sequence, artifact, meta))
    if not candidates:
        return _ReworkFreshAcceptance()
    _sequence, coding_artifact, meta = max(candidates, key=lambda item: item[0])
    marker = (
        f"_rework_{declaration.declaration_sequence:03d}_"
        f"{declaration.pass_id}_{coding_artifact.sequence:03d}"
    )
    if marker not in meta.self_report_path or marker not in meta.review_output_path:
        return _ReworkFreshAcceptance()
    if not _rework_self_report_valid(root, profile, meta):
        return _ReworkFreshAcceptance()
    paired = _paired_review_for_rework(
        root, profile, prompt_artifacts, coding_artifact, meta
    )
    if paired is None:
        return _ReworkFreshAcceptance()
    paired_path = root / profile.review_prompt_dir / paired.filename
    paired_output = _review_output_path_from_prompt(paired_path)
    if not paired_output:
        return _ReworkFreshAcceptance(
            diagnostic=_REWORK_REVIEW_OUTPUT_UNRECOGNIZED
        )
    if paired_output != meta.review_output_path:
        return _ReworkFreshAcceptance()
    return _ReworkFreshAcceptance(
        accepted=(
            meta.review_output_path in evidence.pass_reports
            and meta.review_output_path not in evidence.unrecorded_pass_reports
        )
    )


def _resolve_rework(
    root: Path,
    slices: tuple[RoadmapSlice, ...],
    accepted_slice_ids: tuple[str, ...],
    prompt_artifacts: tuple[PromptArtifact, ...],
    profile: LayoutProfile,
    evidence: _AcceptanceEvidence,
) -> _ReworkResolution:
    """Select the earliest unresolved declared slice, or a typed defect."""

    if evidence.rework_diagnostics:
        return _ReworkResolution(
            diagnostics=tuple(
                (item.code, item.message) for item in evidence.rework_diagnostics
            )
        )
    if not evidence.rework_declarations:
        return _ReworkResolution()
    roadmap_by_id = {item.slice_id.upper(): item for item in slices}
    roadmap_order = tuple(item.slice_id.upper() for item in slices)
    accepted = {item.upper() for item in accepted_slice_ids}
    completed_all: list[str] = []
    declarations = evidence.rework_declarations
    for index, declaration in enumerate(declarations):
        if declaration.baseline_prompt_sequence >= MAX_REWORK_PROMPT_SEQUENCE:
            return _ReworkResolution(
                diagnostics=(
                    (
                        "rework_declaration_prompt_space_exhausted",
                        "rework declaration cannot produce a fresh prompt after sequence 999",
                    ),
                )
            )
        missing = [item for item in declaration.slice_ids if item not in roadmap_by_id]
        if missing:
            return _ReworkResolution(
                diagnostics=(
                    (
                        "rework_declaration_slice_unknown",
                        "rework declaration names a slice absent from the selected roadmap",
                    ),
                )
            )
        unaccepted = [item for item in declaration.slice_ids if item not in accepted]
        if unaccepted:
            return _ReworkResolution(
                diagnostics=(
                    (
                        "rework_declaration_slice_unaccepted",
                        "rework declaration names a slice without historical accepted evidence",
                    ),
                )
            )
        declared = set(declaration.slice_ids)
        canonical = tuple(item for item in roadmap_order if item in declared)
        if declaration.slice_ids != canonical:
            return _ReworkResolution(
                diagnostics=(
                    (
                        "rework_declaration_order_invalid",
                        "slice_ids must follow the selected detailed-roadmap order",
                    ),
                )
            )
        completed: list[str] = []
        pending: RoadmapSlice | None = None
        upper_prompt_sequence = (
            declarations[index + 1].baseline_prompt_sequence
            if index + 1 < len(declarations)
            else None
        )
        for slice_id in declaration.slice_ids:
            fresh = _rework_slice_has_fresh_acceptance(
                root,
                profile,
                declaration,
                slice_id,
                prompt_artifacts,
                evidence,
                upper_prompt_sequence,
            )
            if fresh.diagnostic is not None:
                return _ReworkResolution(diagnostics=(fresh.diagnostic,))
            if fresh.accepted:
                completed.append(slice_id)
                continue
            pending = roadmap_by_id[slice_id]
            break
        completed_all.extend(completed)
        if pending is not None:
            if index + 1 < len(declarations):
                return _ReworkResolution(
                    diagnostics=(
                        (
                            "rework_declaration_overlap_invalid",
                            "a later rework declaration exists before the prior pass is accepted",
                        ),
                    )
                )
            return _ReworkResolution(
                active_declaration=declaration,
                pending_slice=pending,
                completed_slice_ids=tuple(completed_all),
            )
    return _ReworkResolution(completed_slice_ids=tuple(completed_all))


def _make_invalid_review_plan(
    frontier: LoopFrontier,
    sequence: int | None,
    slug: str,
    errors: list[str],
    selected_coding_prompt: PromptArtifact | None = None,
    coding_prompt_meta: CodingPromptMeta | None = None,
    self_report: SelfReportValidationResult | None = None,
    evidence: ReviewPromptEvidenceResult | None = None,
) -> ReviewPromptPlan:
    return ReviewPromptPlan(
        frontier=frontier,
        sequence=sequence,
        slug=slug,
        valid=False,
        errors=tuple(errors),
        selected_coding_prompt=selected_coding_prompt,
        coding_prompt_meta=coding_prompt_meta,
        self_report=self_report,
        evidence=evidence,
        template=None,
        render=None,
        preview=None,
    )


def build_review_prompt_plan(
    start: Path | str = ".",
    *,
    sequence: int | None = None,
    slug: str | None = None,
    overwrite: bool = False,
    layout_config: Path | str | None = None,
) -> ReviewPromptPlan:
    """Build a read-only plan for writing the matching review prompt.

    Locates the latest unmatched coding prompt (or the explicitly supplied
    ``sequence``), reads and parses its metadata, validates the expected
    self-report through the M005 typed surfaces, derives evidence through
    the M006 bridge, and builds a :class:`ReviewPromptTemplate` ready for
    rendering and writing.  Returns a :class:`ReviewPromptPlan` with all
    downstream fields ``None`` when any earlier stage fails.  Fail-closed:
    deterministic errors rather than exceptions for all documented
    failure cases.  Never writes files.
    """
    status = build_status(start, layout_config=layout_config)
    return _build_review_prompt_plan_from_status(
        status, sequence=sequence, slug=slug, overwrite=overwrite
    )


def _build_review_prompt_plan_from_status(
    status: ProjectStatus,
    *,
    sequence: int | None = None,
    slug: str | None = None,
    overwrite: bool = False,
) -> ReviewPromptPlan:
    """Build the review-prompt plan from an already-built status (M002-S04).

    Private single-selection helper: callers that already hold the
    invocation's :class:`ProjectStatus` (and thus its already selected
    ``LoadedLayout``) reuse it here instead of resolving the layout again.
    """
    frontier = _build_frontier_from_status(status)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    review_contract = review_report_format_contract()

    errors: list[str] = []

    coding_seqs: dict[int, PromptArtifact] = {}
    review_seqs: set[int] = set()
    for artifact in status.prompt_artifacts:
        if artifact.sequence is None:
            continue
        if artifact.kind == PromptKind.CODING:
            coding_seqs[artifact.sequence] = artifact
        elif artifact.kind == PromptKind.REVIEW:
            review_seqs.add(artifact.sequence)

    metadata_pairing = profile.prompt_pairing == "workflow_metadata"

    selected_artifact: PromptArtifact | None = None
    if sequence is not None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            errors.append("--sequence must be a positive integer")
            return _make_invalid_review_plan(frontier, sequence, slug or "", errors)
        if sequence > MAX_PROMPT_SEQUENCE:
            errors.append(f"--sequence must be at most {MAX_PROMPT_SEQUENCE}")
            return _make_invalid_review_plan(frontier, sequence, slug or "", errors)
        if sequence not in coding_seqs:
            errors.append(f"no coding prompt found for sequence {sequence:03d}")
            return _make_invalid_review_plan(frontier, sequence, slug or "", errors)
        selected_artifact = coding_seqs[sequence]
    elif metadata_pairing:
        # M003-S02: select the frontier slice's coding prompt through the
        # same validated-metadata decision loop resume uses (highest
        # sequence for the frontier slice), never by unmatched equal
        # sequence.
        if frontier.inferred_slice is None:
            errors.append("no frontier slice found; cannot select a coding prompt")
            return _make_invalid_review_plan(frontier, None, slug or "", errors)
        frontier_slice_upper = frontier.inferred_slice.slice_id.upper()
        slice_matches: list[tuple[int, PromptArtifact]] = []
        for seq, artifact in coding_seqs.items():
            candidate_meta = _parse_coding_prompt_meta(artifact, frontier.root, profile)
            if candidate_meta.slice_id.upper() == frontier_slice_upper:
                slice_matches.append((seq, artifact))
        if not slice_matches:
            errors.append(
                "no coding prompt with validated metadata found for the "
                f"frontier slice {frontier.inferred_slice.slice_id}"
            )
            return _make_invalid_review_plan(frontier, None, slug or "", errors)
        slice_matches.sort(key=lambda pair: pair[0], reverse=True)
        sequence, selected_artifact = slice_matches[0]
    else:
        unmatched = sorted(seq for seq in coding_seqs if seq not in review_seqs)
        if not unmatched:
            errors.append("no unmatched coding prompt found")
            return _make_invalid_review_plan(frontier, None, slug or "", errors)
        sequence = unmatched[-1]
        selected_artifact = coding_seqs[sequence]

    if not metadata_pairing and sequence in review_seqs and not overwrite:
        errors.append(
            f"review prompt for sequence {sequence:03d} already exists; "
            "pass --overwrite to replace it"
        )
        return _make_invalid_review_plan(frontier, sequence, slug or "", errors, selected_artifact)

    meta = _parse_coding_prompt_meta(selected_artifact, frontier.root, profile)
    if not meta.valid:
        return _make_invalid_review_plan(
            frontier, sequence, slug or "", list(meta.errors), selected_artifact, meta
        )

    coding_sequence = sequence
    if metadata_pairing:
        # Already-paired and ambiguity checks use the one metadata pairing
        # decision; ambiguity fails closed and is never resolved by "latest
        # wins" or --overwrite.
        paired, pairing_ambiguous = _select_paired_review_prompt(
            selected_artifact,
            meta.slice_id,
            status.prompt_artifacts,
            frontier.root,
            profile,
        )
        if pairing_ambiguous:
            errors.append(
                f"ambiguous review-prompt pairing for {meta.slice_id}: "
                "multiple qualifying review prompts already exist"
            )
            return _make_invalid_review_plan(
                frontier, sequence, slug or "", errors, selected_artifact, meta
            )
        if paired is not None:
            errors.append(
                f"coding prompt {selected_artifact.filename} is already "
                f"paired with review prompt {paired.filename}"
            )
            return _make_invalid_review_plan(
                frontier, sequence, slug or "", errors, selected_artifact, meta
            )
        if profile.prompt_numbering == "global_flat_sequence":
            # The new review prompt takes the next global flat sequence.
            sequence = _next_prompt_sequence(status.prompt_artifacts)

    effective_slug = slug.strip() if isinstance(slug, str) and slug.strip() else meta.slug
    if not effective_slug:
        errors.append("could not derive a slug for the review prompt")
        return _make_invalid_review_plan(frontier, sequence, "", errors, selected_artifact, meta)

    coding_template = CodingPromptTemplate(
        sequence=coding_sequence,
        milestone_id=meta.milestone_id,
        slice_id=meta.slice_id,
        slug=meta.slug,
        title=meta.title,
        role_instructions=("You are the coding agent for `frutlups`.\n\nImplement this slice."),
        required_reading=meta.required_reading,
        scope_paths=("08_pkg/",),
        non_goals=meta.non_goals,
        definition_of_done=("All required behavior is implemented.",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path=meta.self_report_path,
    )

    sr_validation = validate_expected_self_report(
        SelfReportValidationCommand(
            location=SelfReportLocationCommand(
                project_root=frontier.root,
                template=coding_template,
            ),
            schema=self_report_schema_for_profile(profile),
        )
    )

    if not sr_validation.valid:
        return _make_invalid_review_plan(
            frontier,
            sequence,
            effective_slug,
            list(sr_validation.errors),
            selected_artifact,
            meta,
            sr_validation,
        )

    evidence = derive_review_prompt_evidence(ReviewPromptEvidenceCommand(validation=sr_validation))

    if evidence.errors:
        return _make_invalid_review_plan(
            frontier,
            sequence,
            effective_slug,
            list(evidence.errors),
            selected_artifact,
            meta,
            sr_validation,
            evidence,
        )

    if profile.review_template:
        review_fields = _frontier_prompt_fields(status, meta.milestone_id)
        reading = list(_project_required_reading(status, profile, review_fields))
    else:
        reading = list(meta.required_reading)
        for baseline in ("CLAUDE.md", "README.md"):
            if baseline not in reading:
                reading.insert(0, baseline)
        if "CLAUDE.md" in reading and "README.md" in reading:
            reading = ["CLAUDE.md", "README.md"] + [
                r for r in reading if r not in ("CLAUDE.md", "README.md")
            ]

    review_role = (
        "You are the reviewer for this project.\n\n"
        "Review the coder's implementation against the coding prompt, "
        "the self-report, and the project framework."
        if profile.review_template
        else (
            "You are the reviewer for `frutlups`.\n\n"
            "Review the coder's implementation against the coding prompt, "
            "the self-report, and the project framework."
        )
    )

    review_template = ReviewPromptTemplate(
        sequence=sequence,
        milestone_id=meta.milestone_id,
        slice_id=meta.slice_id,
        slug=effective_slug,
        title=meta.title,
        role_instructions=review_role,
        required_reading=tuple(reading),
        coding_prompt_path=meta.coding_prompt_path,
        self_report_path=meta.self_report_path,
        review_output_path=meta.review_output_path,
        expected_changed_files=evidence.expected_changed_files,
        verification_commands=evidence.verification_commands,
        severity_guidance=(
            "blocker: correctness failures, missing required behavior, broken "
            "interfaces, invalid self-report, or test regressions",
            "major: incomplete behavior, incorrect error handling, or significant scope violations",
            "minor: documentation gaps, redundant code, or style issues that do not "
            "affect correctness",
            "nit: cosmetic observations that a reviewer may note but should not block acceptance",
        ),
        verdict_choices=tuple(
            verdict.value
            for verdict in default_review_report_schema().allowed_verdicts
        ),
        prior_review_paths=("05_governance/reviews/m008_s02_make_coding_prompt_review_report.md",),
        non_goals=meta.non_goals,
        notes=(),
    )

    if profile.review_template:
        # M003-S03: a selected profile with a configured template path must
        # render through that scaffold; the hard-coded renderer is never a
        # silent fallback. M011-S01 (D3): same selected-posture rule as the
        # configured coding path — no memory query; route the selected posture
        # file into Read First once when mode is lightweight/llloom.
        posture_reading = _configured_posture_reading(status, profile)
        if posture_reading:
            review_template = replace(
                review_template,
                required_reading=_dedup_append(review_template.required_reading, posture_reading),
            )
        render = _render_review_from_scaffold(
            status, profile, review_template, review_contract
        )
    else:
        if not review_contract:
            render = ReviewPromptRenderResult(
                content="",
                valid=False,
                errors=(_REVIEW_REPORT_FORMAT_CONTRACT_ERROR,),
            )
        else:
            render = render_review_prompt(
                review_template,
                posture_path=profile.llloom_posture_file or None,
                review_report_contract=review_contract,
            )
    preview = preview_review_prompt(review_template, prompt_dir=profile.review_prompt_dir)
    preview = _reconciled_preview(preview, render)

    if not render.valid:
        return ReviewPromptPlan(
            frontier=frontier,
            sequence=sequence,
            slug=effective_slug,
            valid=False,
            errors=render.errors,
            selected_coding_prompt=selected_artifact,
            coding_prompt_meta=meta,
            self_report=sr_validation,
            evidence=evidence,
            template=review_template,
            render=render,
            preview=preview,
            review_prompt_dir=profile.review_prompt_dir,
        )

    return ReviewPromptPlan(
        frontier=frontier,
        sequence=sequence,
        slug=effective_slug,
        valid=True,
        errors=(),
        selected_coding_prompt=selected_artifact,
        coding_prompt_meta=meta,
        self_report=sr_validation,
        evidence=evidence,
        template=review_template,
        render=render,
        preview=preview,
        review_prompt_dir=profile.review_prompt_dir,
    )


# ---------------------------------------------------------------------------
# M008-S04: record-verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictRecordPlan:
    """Read-only plan for parsing a review verdict and writing a record.

    ``root`` is the discovered project root. ``review_report_path`` is the
    resolved absolute path string to the review report. ``parse_result`` is
    the verdict parse result or ``None`` when parsing was not reached.
    ``reviewed_slice`` is the matching roadmap slice or ``None`` when the
    slice could not be located. ``next_action`` is the computed next-action
    decision or ``None`` on earlier failure. ``target_path`` is the
    repo-relative path of the sidecar record to be written. ``valid`` is
    ``True`` iff all stages succeeded. ``to_dict()`` returns only plain
    Python values.
    """

    root: Path
    review_report_path: str
    parse_result: ReviewReportVerdictParseResult | None
    reviewed_slice: RoadmapSlice | None
    next_action: NextActionDecision | None
    target_path: str
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "review_report_path": self.review_report_path,
            "parse_result": (
                self.parse_result.to_dict() if self.parse_result is not None else None
            ),
            "reviewed_slice": (
                self.reviewed_slice.to_dict() if self.reviewed_slice is not None else None
            ),
            "next_action": (self.next_action.to_dict() if self.next_action is not None else None),
            "target_path": self.target_path,
            "valid": self.valid,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class VerdictRecordWriteCommand:
    """Command to write the verdict record sidecar."""

    project_root: Path
    plan: VerdictRecordPlan
    overwrite: bool = False


@dataclass(frozen=True)
class VerdictRecordWriteResult:
    """Result of writing the verdict record sidecar."""

    wrote: bool
    target_path: str
    overwrote: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "wrote": self.wrote,
            "target_path": self.target_path,
            "overwrote": self.overwrote,
            "errors": list(self.errors),
        }


def build_verdict_record_plan(
    start: Path | str = ".",
    review_report: str | Path = "",
    *,
    overwrite: bool = False,
    layout_config: Path | str | None = None,
) -> VerdictRecordPlan:
    """Build a read-only plan for recording a review verdict.

    Discovers the project root from ``start``, resolves
    ``review_report`` relative to the current working directory,
    parses the verdict from the report file, infers the reviewed
    slice ID from the report filename, matches it against the
    detailed roadmap, and computes the next-action recommendation.

    Returns a :class:`VerdictRecordPlan` with all downstream fields
    ``None`` when any earlier stage fails. Fail-closed: deterministic
    errors rather than exceptions for all documented failure cases.
    Never writes files.
    """

    try:
        layout: ProjectLayout | None = ProjectLayout.discover(
            start, layout_config=layout_config
        )
        discover_error: Exception | None = None
    except Exception as exc:  # fail-closed: the invalid plan carries the error text
        layout = None
        discover_error = exc
    return _build_verdict_record_plan_from_layout(
        layout, discover_error, review_report, overwrite=overwrite
    )


def _build_verdict_record_plan_from_layout(
    layout: ProjectLayout | None,
    discover_error: Exception | None,
    review_report: str | Path,
    *,
    overwrite: bool = False,
) -> VerdictRecordPlan:
    """Build the verdict-record plan from an already selected layout (M002-S04).

    Private single-selection helper: callers that already hold the
    invocation's discovered :class:`ProjectLayout` (and thus its already
    selected ``LoadedLayout``) reuse it here instead of resolving the layout
    again. ``discover_error`` carries a failed discovery so the historical
    fail-closed invalid plan is preserved exactly.
    """

    rr_path = Path(review_report).resolve()
    rr_path_str = str(rr_path)

    if discover_error is not None:
        return VerdictRecordPlan(
            root=Path(".").resolve(),
            review_report_path=rr_path_str,
            parse_result=None,
            reviewed_slice=None,
            next_action=None,
            target_path="",
            valid=False,
            errors=(str(discover_error),),
        )

    return _build_verdict_record_plan_from_profile(
        layout.root, layout.profile, review_report, overwrite=overwrite
    )


def _build_verdict_record_plan_from_profile(
    root: Path,
    profile: LayoutProfile,
    review_report: str | Path,
    *,
    overwrite: bool = False,
    evidence: "_AcceptanceEvidence | None" = None,
) -> VerdictRecordPlan:
    """Build the verdict-record plan from an already selected root and profile.

    Private single-selection helper (M002-S04): the caller supplies the exact
    root and selected profile of the invocation's already selected layout, so
    no discovery or layout resolution happens here. ``evidence`` threads the
    Prompt 031 one-snapshot input: when supplied, the plan's accepted-ID view
    reuses the invocation's already selected acceptance snapshot instead of
    scanning again.
    """

    rr_path = Path(review_report).resolve()
    rr_path_str = str(rr_path)

    def _invalid(
        root: Path,
        target: str,
        parse_result: ReviewReportVerdictParseResult | None,
        *msgs: str,
    ) -> VerdictRecordPlan:
        return VerdictRecordPlan(
            root=root,
            review_report_path=rr_path_str,
            parse_result=parse_result,
            reviewed_slice=None,
            next_action=None,
            target_path=target,
            valid=False,
            errors=tuple(msgs),
        )

    rr_name = rr_path.name

    # Derive target path from review report filename, using the profile's
    # configured reviews directory and report/verdict suffixes. Under
    # recursive discovery (M003-S02) a nested report keeps its contained
    # subdirectory so the record is written beside the report it receipts.
    if rr_name.endswith(profile.review_report_suffix):
        stem = rr_name[: -len(profile.review_report_suffix)]
        target_dir = profile.reviews_dir.rstrip("/")
        if profile.reports_discovery == "recursive_contained":
            try:
                reviews_abs = (root / profile.reviews_dir).resolve()
                sub = rr_path.relative_to(reviews_abs).parent.as_posix()
            except (OSError, RuntimeError, ValueError):
                sub = "."
            if sub != ".":
                target_dir = f"{target_dir}/{sub}"
        target_path = f"{target_dir}/{stem}{profile.verdict_record_suffix}"
    else:
        return _invalid(
            root,
            "",
            None,
            f"review report filename does not match expected convention"
            f" (*{profile.review_report_suffix}): {rr_name!r}",
        )

    # Infer slice ID from filename, honoring the selected profile's configured
    # report suffix (M003-S05: one selected-profile boundary, no legacy regex).
    rr_match = _slice_artifact_re(profile.review_report_suffix).match(rr_name)
    if not rr_match:
        return _invalid(
            root,
            target_path,
            None,
            f"cannot infer slice ID from review report filename: {rr_name!r}",
        )
    slice_id = f"{rr_match.group('milestone').upper()}-{rr_match.group('slice').upper()}"

    # Check for existing record
    target_abs = root / target_path
    if target_abs.exists() and not overwrite:
        return _invalid(
            root,
            target_path,
            None,
            f"verdict record already exists: {target_path}; pass --overwrite to replace",
        )

    # Parse verdict from the review report file
    parse_result = parse_review_report_verdict(ReviewReportVerdictParseCommand(path=rr_path))
    if not parse_result.valid:
        return VerdictRecordPlan(
            root=root,
            review_report_path=rr_path_str,
            parse_result=parse_result,
            reviewed_slice=None,
            next_action=None,
            target_path=target_path,
            valid=False,
            errors=parse_result.errors,
        )

    # A valid parse always carries a verdict; narrow it explicitly so the
    # type checker (and a malformed parser result) cannot leak ``None`` into
    # NextActionCommand below.
    verdict = parse_result.verdict
    if verdict is None:
        return VerdictRecordPlan(
            root=root,
            review_report_path=rr_path_str,
            parse_result=parse_result,
            reviewed_slice=None,
            next_action=None,
            target_path=target_path,
            valid=False,
            errors=("review report verdict could not be determined",),
        )

    # Load roadmap slices and accepted IDs. ``TemplatePaths`` is a pure path
    # computer, so rebuilding it from the already selected root and profile
    # is not a second layout selection.
    paths = TemplatePaths(root, profile=profile)
    detailed_candidates = paths.detailed_roadmaps
    detailed_roadmap = _select_detailed_roadmap(detailed_candidates)
    slices = parse_slices(detailed_roadmap) if detailed_roadmap is not None else ()
    # M003-S05: accepted IDs come from the selected profile's typed evidence.
    if evidence is None:
        evidence = _collect_acceptance_evidence(root, profile)
    accepted_slice_ids = evidence.accepted_slice_ids

    # Locate the reviewed slice in the roadmap
    reviewed_slice: RoadmapSlice | None = None
    for slc in slices:
        if slc.slice_id.upper() == slice_id.upper():
            reviewed_slice = slc
            break

    if reviewed_slice is None:
        return _invalid(
            root,
            target_path,
            parse_result,
            f"slice {slice_id!r} not found in detailed roadmap",
        )

    # Compute next action
    next_action = compute_next_action_from_verdict(
        NextActionCommand(
            verdict=verdict,
            current_slice=reviewed_slice,
            slices=slices,
            accepted_slice_ids=accepted_slice_ids,
        )
    )

    return VerdictRecordPlan(
        root=root,
        review_report_path=rr_path_str,
        parse_result=parse_result,
        reviewed_slice=reviewed_slice,
        next_action=next_action,
        target_path=target_path,
        valid=True,
        errors=(),
    )


def write_verdict_record(
    command: VerdictRecordWriteCommand,
) -> VerdictRecordWriteResult:
    """Write the verdict record sidecar markdown file.

    Uses ``command.plan`` to derive the content and target path.
    Returns a :class:`VerdictRecordWriteResult`. Never raises.
    """

    plan = command.plan
    if not plan.valid:
        return VerdictRecordWriteResult(
            wrote=False,
            target_path=plan.target_path,
            overwrote=False,
            errors=("plan is not valid; cannot write verdict record",),
        )

    target = command.project_root / plan.target_path
    overwrote = False

    if target.exists() and not command.overwrite:
        return VerdictRecordWriteResult(
            wrote=False,
            target_path=plan.target_path,
            overwrote=False,
            errors=(
                f"verdict record already exists: {plan.target_path}; pass --overwrite to replace",
            ),
        )

    if target.exists():
        overwrote = True

    content = _render_verdict_record(plan)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return VerdictRecordWriteResult(
            wrote=False,
            target_path=plan.target_path,
            overwrote=False,
            errors=(f"could not write verdict record: {exc}",),
        )

    return VerdictRecordWriteResult(
        wrote=True,
        target_path=plan.target_path,
        overwrote=overwrote,
        errors=(),
    )


def _render_verdict_record(plan: VerdictRecordPlan) -> str:
    """Render the verdict record markdown content from a valid plan.

    Only called for a valid plan, where ``reviewed_slice``, ``parse_result``
    (with a parsed ``verdict``), and ``next_action`` are all populated. The
    guard below makes that contract explicit and narrows the optional fields.
    """

    slc = plan.reviewed_slice
    pr = plan.parse_result
    action = plan.next_action
    if slc is None or pr is None or pr.verdict is None or action is None:
        raise ValueError("cannot render verdict record from an incomplete plan")

    try:
        rr_display = str(Path(plan.review_report_path).relative_to(plan.root)).replace("\\", "/")
    except ValueError:
        rr_display = plan.review_report_path

    lines: list[str] = [
        f"# Verdict Record: {slc.slice_id}",
        "",
        "## Source",
        "",
        f"Review report: `{rr_display}`",
        "",
        "## Slice",
        "",
        f"Slice ID: `{slc.slice_id}`",
        f"Title: {slc.title}",
        f"Milestone: `{slc.milestone_id}`",
        "",
        "## Parsed Verdict",
        "",
        f"Verdict: `{pr.verdict.value}`",
        "",
        "## Next Action",
        "",
        f"Kind: `{action.kind.value}`",
    ]
    if action.next_slice_id:
        lines.append(f"Next slice: `{action.next_slice_id}`")
    else:
        lines.append("Next slice: none")
    lines.append(f"Message: {action.message}")

    if action.errors:
        lines += ["", "## Decision Errors", ""]
        for err in action.errors:
            lines.append(f"- {err}")

    lines += [
        "",
        "## Note",
        "",
        "No roadmap mutation occurred. This record is a read-only governance artifact.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# M008-S05: resumable loop status
# ---------------------------------------------------------------------------


class LoopResumeStep(StrEnum):
    """Stable step identifiers for the resumable loop state."""

    NO_FRONTIER = "no_frontier"
    MAKE_CODING_PROMPT = "make_coding_prompt"
    EXECUTE_CODING_PROMPT = "execute_coding_prompt"
    FIX_SELF_REPORT = "fix_self_report"
    MAKE_REVIEW_PROMPT = "make_review_prompt"
    EXECUTE_REVIEW_PROMPT = "execute_review_prompt"
    FIX_REVIEW_REPORT = "fix_review_report"
    RECORD_VERDICT = "record_verdict"
    FRONTIER_RECORDED = "frontier_recorded"


@dataclass(frozen=True)
class LoopResumeStatus:
    """Read-only resumable loop state for the current frontier slice.

    Derived from project artifacts alone so the loop can be resumed
    after any interrupted handoff without chat history.
    ``step`` is a stable :class:`LoopResumeStep` value. ``next_command``
    is the literal command a local user or runner should execute next
    (empty when the next action is manual or unknown). All path fields
    are repo-relative strings; empty string means unknown or not yet
    applicable.
    """

    step: LoopResumeStep
    message: str
    next_command: str
    frontier_slice_id: str
    frontier_slice_title: str
    coding_prompt_path: str
    self_report_path: str
    review_prompt_path: str
    review_report_path: str
    verdict_record_path: str
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step.value,
            "message": self.message,
            "next_command": self.next_command,
            "frontier_slice_id": self.frontier_slice_id,
            "frontier_slice_title": self.frontier_slice_title,
            "coding_prompt_path": self.coding_prompt_path,
            "self_report_path": self.self_report_path,
            "review_prompt_path": self.review_prompt_path,
            "review_report_path": self.review_report_path,
            "verdict_record_path": self.verdict_record_path,
            "diagnostics": list(self.diagnostics),
        }


def _compute_loop_resume(
    root: Path,
    inferred_slice: RoadmapSlice | None,
    prompt_artifacts: tuple[PromptArtifact, ...],
    slices: tuple[RoadmapSlice, ...] = (),
    profile: LayoutProfile | None = None,
    verdict_out: list | None = None,
    evidence_out: list | None = None,
    evidence: "_AcceptanceEvidence | None" = None,
) -> LoopResumeStatus:
    """Compute resumable loop state from project artifacts.

    Pure read-only computation. Never writes files. Never raises. ``profile``
    drives coding-prompt metadata parsing (section names / front matter) so v2
    layouts resolve loop state correctly; it defaults to the legacy profile.

    ``verdict_out`` is a private M003-S04 channel: when a list is supplied and
    the current review report parses to a valid verdict, the typed verdict is
    appended to it exactly once, so callers do not need a second parse.
    ``evidence_out`` is the matching private M003-S06 channel: when a list is
    supplied, the one selected :class:`_AcceptanceEvidence` snapshot this
    computation used is appended to it exactly once, so the planning-frontier
    computation never performs a second authority scan. ``evidence`` is the
    Prompt 031 one-snapshot input: when supplied, this computation uses it
    verbatim and performs no acceptance scan of its own; the same private
    snapshot then governs status, resume, gate, frontier, and runner values
    for the whole composition.

    M003-S05: a typed acceptance-authority defect, then a typed
    verdict-record/review-report contradiction, fails closed as
    ``FIX_REVIEW_REPORT`` ahead of every other step (see
    ``_collect_acceptance_evidence``).
    """

    if profile is None:
        profile = legacy_profile()

    def _status(
        step: LoopResumeStep,
        message: str,
        next_command: str,
        frontier_slice_id: str = "",
        frontier_slice_title: str = "",
        coding_prompt_path: str = "",
        self_report_path: str = "",
        review_prompt_path: str = "",
        review_report_path: str = "",
        verdict_record_path: str = "",
        diagnostics: tuple[str, ...] = (),
    ) -> LoopResumeStatus:
        return LoopResumeStatus(
            step=step,
            message=message,
            next_command=next_command,
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=diagnostics,
        )

    # M003-S05: typed acceptance evidence from the one selected profile
    # snapshot. A canonical slice verdict record whose corresponding review
    # report is missing or does not canonically parse to pass is contradictory
    # durable state. It fails closed ahead of the pass-without-receipt scan,
    # pending corrective reviews, no_frontier, ordinary frontier work, and
    # runner dispatch: it never adds an accepted slice ID and cannot advance,
    # open, complete, or execute the loop. The first contradiction by
    # deterministic repo-relative record path wins; the rest are reported only
    # through bounded deterministic diagnostics.
    if evidence is None:
        evidence = _collect_acceptance_evidence(root, profile)
    if evidence_out is not None:
        evidence_out.append(evidence)
    if evidence.authority_defects:
        # Review 029: a typed acceptance-authority defect (unresolvable or
        # escaped configured reviews directory, or an escaped suffix-matching
        # report) fails closed independently of any verdict record, ahead of
        # every contradiction and every normal step. No external authority is
        # consulted and no normal work can continue.
        first_defect = evidence.authority_defects[0]
        defect_diagnostics = [defect.diagnostic for defect in evidence.authority_defects]
        for extra in evidence.contradictions:
            defect_diagnostics.append(
                _cap_evidence_diagnostic(
                    f"further contradictory verdict record {extra.record_path} "
                    "also present"
                )
            )
        report_path = (
            first_defect.authority_path
            if first_defect.kind is _AuthorityDefectKind.ESCAPED_AUTHORITY_REPORT
            else ""
        )
        return _status(
            LoopResumeStep.FIX_REVIEW_REPORT,
            first_defect.diagnostic,
            "",
            review_report_path=report_path,
            diagnostics=tuple(defect_diagnostics),
        )
    if evidence.contradictions:
        first = evidence.contradictions[0]
        contradiction_diagnostics = [first.diagnostic]
        for extra in evidence.contradictions[1:]:
            contradiction_diagnostics.append(
                _cap_evidence_diagnostic(
                    f"further contradictory verdict record {extra.record_path} "
                    "also present"
                )
            )
        first_title = next(
            (slc.title for slc in slices if slc.slice_id.upper() == first.slice_id),
            "",
        )
        return _status(
            LoopResumeStep.FIX_REVIEW_REPORT,
            first.diagnostic,
            "",
            frontier_slice_id=first.slice_id,
            frontier_slice_title=first_title,
            review_report_path=first.report_path,
            verdict_record_path=first.record_path,
            diagnostics=tuple(contradiction_diagnostics),
        )

    # Resolve declarations only after higher-priority acceptance authority
    # defects and contradictions have failed closed. This preserves the rule
    # that hostile or escaped authority state wins before any ordinary artifact
    # routing reads are attempted.
    rework = evidence.rework_resolution
    if rework is None:
        rework = _resolve_rework(
            root,
            slices,
            evidence.accepted_slice_ids,
            prompt_artifacts,
            profile,
            evidence,
        )
    active_rework = rework.active_declaration

    # Pre-check: scan for pass-verdict review reports that have no verdict record.
    # The accepted-slice scan counts pass reports as accepted and advances the frontier
    # before _compute_loop_resume can require their verdict record. This scan catches
    # that case and surfaces record_verdict for the earliest unrecorded pass report.
    #
    # Terminal closure stop rule (M018-S02 hardening): once the roadmap has no
    # frontier (``inferred_slice is None``), an unrecorded pass report whose sole
    # purpose is to review a verdict-recording closure slice (a
    # ``..._record_<number>_..verdict_review_report.md`` tail) must NOT force another
    # record_verdict cycle, or fully automated execution would never terminate. Such
    # terminal-tail reports are skipped here — but ONLY when the slice was already
    # accepted *independently* of that same report (by a non-terminal review report),
    # so a terminal tail cannot certify its own slice and then be skipped (M018-S02
    # independent-acceptance guard). Ordinary unrecorded pass reports (active work,
    # normal slice acceptance) still surface as record_verdict.
    # M003-S05: iterate the selected evidence's unrecorded passing reports
    # directly — no second legacy-directory scan; slice identity and the
    # receipt output path derive from the configured suffixes.
    report_re = _slice_artifact_re(profile.review_report_suffix)
    reviews_rel = profile.reviews_dir.rstrip("/")
    for report_rel in evidence.unrecorded_pass_reports:
        report_name = report_rel.rsplit("/", 1)[-1]
        m = report_re.match(report_name)
        if not m:
            continue
        rev_slice_id = f"{m.group('milestone').upper()}-{m.group('slice').upper()}"
        # Skip only a terminal closure tail on a completed roadmap whose slice
        # was accepted independently of this report; everything else still
        # surfaces record_verdict.
        if (
            (inferred_slice is None or active_rework is not None)
            and _is_terminal_closure_review_report(report_name)
            and _is_slice_accepted_by_nonterminal_evidence(evidence, report_re, rev_slice_id)
        ):
            continue
        rev_slice_title = next(
            (slc.title for slc in slices if slc.slice_id.upper() == rev_slice_id),
            "",
        )
        stem = report_name[: -len(profile.review_report_suffix)]
        # The receipt is expected beside its report, which preserves nested
        # containment and is identical to the reviews root for flat layouts.
        report_dir_rel = report_rel.rsplit("/", 1)[0] if "/" in report_rel else reviews_rel
        verdict_record_rel = f"{report_dir_rel}/{stem}{profile.verdict_record_suffix}"
        return _status(
            LoopResumeStep.RECORD_VERDICT,
            (
                f"review report for {rev_slice_id} has a pass verdict "
                f"but no verdict record; record it with record-verdict"
            ),
            f"python -m frutlups record-verdict <project> --review-report {report_rel}",
            frontier_slice_id=rev_slice_id,
            frontier_slice_title=rev_slice_title,
            review_report_path=report_rel,
            verdict_record_path=verdict_record_rel,
        )

    if inferred_slice is None:
        # A completed project (no remaining frontier) is the terminal state; do not
        # surface trailing closure-review artifacts here. (The pending-corrective
        # pre-check below only runs while there is still an active frontier, so a
        # genuinely-pending corrective for an accepted slice — like prompt 088 — is
        # still surfaced during active work, but a fully-completed roadmap stays at
        # no_frontier instead of reopening on a bookkeeping review tail.)
        return _status(
            LoopResumeStep.NO_FRONTIER,
            "no frontier slice; all roadmap slices may be accepted or roadmap is empty",
            "",
        )

    # Pre-check (only while a frontier exists): surface a pending review the normal
    # frontier scan would skip. A review prompt that declares a review-report output
    # which does not yet exist is an unreviewed artifact. When its slice is ALREADY
    # accepted (e.g. a corrective review for an accepted slice, such as prompt 088
    # for M017-S02), the frontier scan advances past it; surface the earliest such
    # pending corrective review as execute_review_prompt so it is not silently
    # skipped. Scoped to already-accepted slices so normal in-flight review flow
    # (handled per-slice below) is unchanged; bare review prompts that declare no
    # output location are ignored.
    review_prompt_dir = root / "prompts" / "for_review_agent"
    if review_prompt_dir.is_dir() and active_rework is None:
        accepted_for_review = evidence.accepted_slice_ids
        for review_prompt in sorted(review_prompt_dir.glob("*.md")):
            report_rel = _review_output_path_from_prompt(review_prompt)
            if not report_rel or not report_rel.endswith(profile.review_report_suffix):
                continue
            report_match = report_re.match(report_rel.rsplit("/", 1)[-1])
            if not report_match:
                continue
            rev_slice_id = (
                f"{report_match.group('milestone').upper()}-{report_match.group('slice').upper()}"
            )
            if rev_slice_id not in accepted_for_review:
                continue  # normal in-flight slice; handled by the per-slice logic
            if (root / report_rel).is_file():
                continue  # already reviewed
            rev_slice_title = next(
                (slc.title for slc in slices if slc.slice_id.upper() == rev_slice_id),
                "",
            )
            return _status(
                LoopResumeStep.EXECUTE_REVIEW_PROMPT,
                (
                    f"review prompt {review_prompt.name} is pending review; the "
                    f"reviewer must execute the review and write {report_rel} "
                    f"(corrective review for already-accepted {rev_slice_id})"
                ),
                "",
                frontier_slice_id=rev_slice_id,
                frontier_slice_title=rev_slice_title,
                review_prompt_path=f"prompts/for_review_agent/{review_prompt.name}",
                review_report_path=report_rel,
            )

    slice_id_upper = inferred_slice.slice_id.upper()
    frontier_slice_id = inferred_slice.slice_id
    frontier_slice_title = inferred_slice.title
    diagnostics: list[str] = []

    # Find coding prompts matching the frontier slice by metadata
    coding_metas: list[tuple[PromptArtifact, CodingPromptMeta]] = []
    for artifact in prompt_artifacts:
        if artifact.kind != PromptKind.CODING:
            continue
        if (
            active_rework is not None
            and (
                artifact.sequence is None
                or artifact.sequence <= active_rework.baseline_prompt_sequence
            )
        ):
            continue
        meta = _parse_coding_prompt_meta(artifact, root, profile)
        if meta.slice_id.upper() == slice_id_upper:
            coding_metas.append((artifact, meta))

    # Use the highest-sequence match; warn on duplicates
    coding_metas.sort(key=lambda pair: pair[0].sequence or 0, reverse=True)
    if len(coding_metas) > 1:
        diagnostics.append(
            f"multiple coding prompts found for slice {frontier_slice_id}; "
            "using the highest-sequence one"
        )

    if not coding_metas:
        return _status(
            LoopResumeStep.MAKE_CODING_PROMPT,
            f"no coding prompt found for {frontier_slice_id}; create one",
            "python -m frutlups make-coding-prompt <project>",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            diagnostics=tuple(diagnostics),
        )

    coding_artifact, found_meta = coding_metas[0]

    coding_prompt_path = found_meta.coding_prompt_path
    self_report_path = found_meta.self_report_path
    review_report_path = found_meta.review_output_path

    # Derive verdict record path from the review report path
    verdict_record_path = ""
    if review_report_path and review_report_path.endswith("_review_report.md"):
        rr_name = review_report_path.rsplit("/", 1)[-1]
        rr_dir = review_report_path.rsplit("/", 1)[0] if "/" in review_report_path else ""
        stem = rr_name[: -len("_review_report.md")]
        verdict_record_path = (
            f"{rr_dir}/{stem}_verdict_record.md" if rr_dir else f"{stem}_verdict_record.md"
        )

    # Find the matching review prompt through the one configured pairing
    # decision (M003-S02): validated workflow metadata under
    # ``pairing: workflow_metadata``, else the default equal-sequence rule.
    review_prompt_path = ""
    review_seq = coding_artifact.sequence
    if profile.prompt_pairing == "workflow_metadata":
        if found_meta.slice_id:
            paired, pairing_ambiguous = _select_paired_review_prompt(
                coding_artifact,
                found_meta.slice_id,
                prompt_artifacts,
                root,
                profile,
            )
            if pairing_ambiguous:
                return _status(
                    LoopResumeStep.FIX_REVIEW_REPORT,
                    (
                        f"ambiguous review-prompt pairing for {frontier_slice_id}: "
                        "multiple qualifying review prompts; resolve the "
                        "ambiguity before continuing"
                    ),
                    "",
                    frontier_slice_id=frontier_slice_id,
                    frontier_slice_title=frontier_slice_title,
                    coding_prompt_path=coding_prompt_path,
                    self_report_path=self_report_path,
                    diagnostics=tuple(
                        diagnostics
                        + [
                            "ambiguous review-prompt pairing: multiple "
                            "qualifying candidates and no validated "
                            "disambiguation"
                        ]
                    ),
                )
            if paired is not None:
                review_prompt_path = f"{profile.review_prompt_dir}/{paired.filename}"
    else:
        for artifact in prompt_artifacts:
            if artifact.kind == PromptKind.REVIEW and artifact.sequence == review_seq:
                review_prompt_path = f"prompts/for_review_agent/{artifact.filename}"
                break

    # If metadata is incomplete, we can't locate the self-report deterministically
    if not found_meta.valid or not self_report_path:
        return _status(
            LoopResumeStep.EXECUTE_CODING_PROMPT,
            (
                f"coding prompt for {frontier_slice_id} has incomplete metadata; "
                "execute the coding prompt and write the self-report"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics + list(found_meta.errors)),
        )

    # Check self-report file
    self_report_abs = root / self_report_path
    if not self_report_abs.is_file():
        return _status(
            LoopResumeStep.EXECUTE_CODING_PROMPT,
            (
                f"coding prompt for {frontier_slice_id} exists; "
                f"self-report is missing at {self_report_path}"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics),
        )

    # Validate self-report using existing validator
    stub_template = CodingPromptTemplate(
        sequence=found_meta.sequence,
        milestone_id=found_meta.milestone_id or "UNKNOWN",
        slice_id=found_meta.slice_id or "UNKNOWN",
        slug=found_meta.slug or "unknown",
        title=found_meta.title or "unknown",
        role_instructions="coder",
        required_reading=("CLAUDE.md",),
        scope_paths=("08_pkg/",),
        non_goals=(),
        definition_of_done=("pass tests",),
        verification_commands=("python -m unittest",),
        self_report_path=self_report_path,
    )
    sr_validation = validate_expected_self_report(
        SelfReportValidationCommand(
            location=SelfReportLocationCommand(project_root=root, template=stub_template),
            schema=self_report_schema_for_profile(profile),
        )
    )
    if not sr_validation.valid:
        return _status(
            LoopResumeStep.FIX_SELF_REPORT,
            (
                f"self-report for {frontier_slice_id} at {self_report_path} "
                "is invalid; fix it before generating the review prompt"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics + list(sr_validation.errors)),
        )

    # Check for matching review prompt
    if not review_prompt_path:
        if profile.prompt_pairing == "workflow_metadata":
            hint = "python -m frutlups make-review-prompt <project>"
        else:
            seq_str = f"{review_seq:03d}" if isinstance(review_seq, int) else "???"
            hint = f"python -m frutlups make-review-prompt <project> --sequence {seq_str}"
        return _status(
            LoopResumeStep.MAKE_REVIEW_PROMPT,
            (
                f"self-report for {frontier_slice_id} is valid; "
                f"no matching review prompt yet; run make-review-prompt"
            ),
            hint,
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path="",
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics),
        )

    # Check review report
    review_report_abs = root / review_report_path if review_report_path else None
    if review_report_abs is None or not review_report_abs.is_file():
        return _status(
            LoopResumeStep.EXECUTE_REVIEW_PROMPT,
            (
                f"review prompt for {frontier_slice_id} exists; "
                f"review report is missing at {review_report_path}"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics),
        )

    # Parse review report verdict
    rr_parse = parse_review_report_verdict(ReviewReportVerdictParseCommand(path=review_report_abs))
    if not rr_parse.valid:
        return _status(
            LoopResumeStep.FIX_REVIEW_REPORT,
            (
                f"review report for {frontier_slice_id} at {review_report_path} "
                "has no parseable verdict; fix it"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics + list(rr_parse.errors)),
        )

    # A valid parse always carries a verdict; narrow it explicitly.
    rr_verdict = rr_parse.verdict
    if rr_verdict is None:
        return _status(
            LoopResumeStep.FIX_REVIEW_REPORT,
            (
                f"review report for {frontier_slice_id} at {review_report_path} "
                "has no parseable verdict; fix it"
            ),
            "",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics),
        )

    # M003-S04: expose the already-parsed typed verdict through the private
    # channel so the runner policy never reparses the report.
    if verdict_out is not None:
        verdict_out.append(rr_verdict)

    # A rework ``needs_work`` report is durable negative evidence, not an
    # acceptance receipt. Start a new uniquely linked coding attempt without
    # recording a verdict artifact: the accepted-evidence contract deliberately
    # treats records citing non-pass reports as contradictory state.
    if active_rework is not None and rr_verdict == ReviewVerdict.NEEDS_WORK:
        return _status(
            LoopResumeStep.MAKE_CODING_PROMPT,
            (
                f"rework review for {frontier_slice_id} found needs_work; "
                "create a fresh corrective coding prompt"
            ),
            "python -m frutlups make-coding-prompt <project>",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            diagnostics=tuple(diagnostics),
        )

    # Check verdict record
    verdict_record_abs = root / verdict_record_path if verdict_record_path else None
    if verdict_record_abs is None or not verdict_record_abs.is_file():
        return _status(
            LoopResumeStep.RECORD_VERDICT,
            (
                f"review report for {frontier_slice_id} has verdict "
                f"{rr_verdict.value}; record it with record-verdict"
            ),
            f"python -m frutlups record-verdict <project> --review-report {review_report_path}",
            frontier_slice_id=frontier_slice_id,
            frontier_slice_title=frontier_slice_title,
            coding_prompt_path=coding_prompt_path,
            self_report_path=self_report_path,
            review_prompt_path=review_prompt_path,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            diagnostics=tuple(diagnostics),
        )

    # Verdict record exists — frontier recorded
    return _status(
        LoopResumeStep.FRONTIER_RECORDED,
        (
            f"verdict for {frontier_slice_id} is recorded; "
            "recompute frontier from accepted review evidence"
        ),
        "python -m frutlups next <project>",
        frontier_slice_id=frontier_slice_id,
        frontier_slice_title=frontier_slice_title,
        coding_prompt_path=coding_prompt_path,
        self_report_path=self_report_path,
        review_prompt_path=review_prompt_path,
        review_report_path=review_report_path,
        verdict_record_path=verdict_record_path,
        diagnostics=tuple(diagnostics),
    )


def build_loop_resume_status(
    status: ProjectStatus | Path | str,
    layout_config: Path | str | None = None,
) -> LoopResumeStatus:
    """Build the resumable loop state for the current frontier slice.

    Accepts a pre-built :class:`ProjectStatus` or any path accepted by
    :func:`build_status`. When given a path, the status and the resume derive
    from one selected layout and one acceptance-evidence snapshot
    (Prompt 031). A caller that deliberately passes a previously built public
    ``ProjectStatus`` begins a new read-only resume invocation with its own
    single scan. ``layout_config`` selects an explicit layout config when a
    path is passed. Returns a :class:`LoopResumeStatus` describing the
    current loop step and the next recommended action. Never raises.
    """

    evidence: _AcceptanceEvidence | None = None
    if not isinstance(status, ProjectStatus):
        status, evidence = _build_status_with_evidence(
            status, layout_config=layout_config
        )
    frontier = _build_frontier_from_status(status)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    return _compute_loop_resume(
        root=status.root,
        inferred_slice=frontier.inferred_slice,
        prompt_artifacts=status.prompt_artifacts,
        slices=status.slices,
        profile=profile,
        evidence=evidence,
    )


def _loop_resume_with_verdict(
    status: ProjectStatus,
    evidence: "_AcceptanceEvidence | None" = None,
) -> tuple[LoopResumeStatus, ReviewVerdict | None]:
    """The loop resume plus the parsed current review verdict (M003-S04).

    Private single-selection helper for the runner-policy seam: carries the
    already-parsed typed :class:`ReviewVerdict` (or ``None`` when the current
    step has no parseable verdict) without a second read and without string
    inference. ``evidence`` threads the Prompt 031 one-snapshot input. The
    public resume shape is unchanged.
    """

    resume, verdict, _evidence_snapshot = _loop_resume_with_verdict_and_evidence(
        status, evidence=evidence
    )
    return resume, verdict


def _loop_resume_with_verdict_and_evidence(
    status: ProjectStatus,
    evidence: "_AcceptanceEvidence | None" = None,
) -> tuple[LoopResumeStatus, ReviewVerdict | None, _AcceptanceEvidence]:
    """The loop resume, typed verdict, and selected acceptance evidence.

    Private single-selection helper (M003-S06/Prompt 031): when ``evidence``
    is supplied (the snapshot the selected status itself used), the resume
    consumes it verbatim and no second authority scan occurs; the same
    snapshot is returned so gate, frontier, and runner values can never
    disagree with the resume or the status about the typed evidence.
    """

    frontier = _build_frontier_from_status(status)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    verdict_out: list = []
    evidence_out: list = []
    resume = _compute_loop_resume(
        root=status.root,
        inferred_slice=frontier.inferred_slice,
        prompt_artifacts=status.prompt_artifacts,
        slices=status.slices,
        profile=profile,
        verdict_out=verdict_out,
        evidence_out=evidence_out,
        evidence=evidence,
    )
    used = evidence_out[0] if evidence_out else _AcceptanceEvidence((), (), (), ())
    return resume, (verdict_out[0] if verdict_out else None), used


# ---------------------------------------------------------------------------
# M003-S06: versioned planning-frontier output (Decision 6)
# ---------------------------------------------------------------------------

PLANNING_FRONTIER_CONTRACT_ID = "frutlups.planning_frontier"
"""The one explicit planning-frontier contract identifier (Decision 6.1)."""

PLANNING_FRONTIER_CONTRACT_VERSION = "1"
"""The contract version this product emits."""

PLANNING_FRONTIER_SUPPORTED_VERSIONS: tuple[str, ...] = ("1",)
"""Versions the in-tree consumer boundary implements. A version outside this
tuple is refused fail-closed; an unknown newer version is never best-effort
interpreted (Decision 6 resolution 1)."""

_ARCHITECT_FRONTIER_ACTION = (
    "author the next roadmap slice for the current scope, or produce the "
    "explicit accepted closure evidence that ends it"
)
"""The exactly-one bounded architect action carried by ``needs_specification``
(Decision 6 resolution 4). The outcome itself performs no write."""

_BLOCK_OWNER_HUMAN = "human"
"""The named owner for verdict-derived blocks: a parsed ``blocked`` verdict and
an ``override`` verdict both require an explicit human decision to clear
(Decision 6 resolution 2)."""

# Typed status diagnostic codes that make the roadmap side of durable state
# individually invalid or ambiguous for frontier purposes (Decision 6
# resolution 6). Codes are stable typed identifiers, never message prose.
_FRONTIER_INVALID_ROADMAP_CODES: tuple[str, ...] = (
    "no_active_roadmap",
    "no_milestones_parsed",
)
_FRONTIER_AMBIGUOUS_ROADMAP_CODES: tuple[str, ...] = (
    "multiple_active_roadmaps",
    "multiple_detailed_roadmaps",
)


class PlanningFrontierOutcome(StrEnum):
    """The five fixed planning-frontier outcome names (Decision 6)."""

    READY = "ready"
    NEEDS_SPECIFICATION = "needs_specification"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    INVALID = "invalid"


@dataclass(frozen=True)
class PlanningFrontierStatus:
    """The versioned, typed planning-frontier output (M003-S06, Decision 6).

    Emitted inside the existing read-only status surface. ``outcome`` is one
    of the five fixed :class:`PlanningFrontierOutcome` values (kept as a plain
    string so an unknown value from a forged or newer producer is
    representable and can be refused by the consumer boundary). The optional
    fields carry only what the selected outcome requires: ``action``/``actor``
    for ``needs_specification``, ``block_citation``/``block_owner`` for
    ``blocked``, and ``completion_evidence`` for ``complete``; all other
    combinations are empty strings. Every path value is repo-relative;
    diagnostics are deterministic, individually bounded, and never carry
    machine-local or hostile values.
    """

    contract_id: str
    contract_version: str
    outcome: str
    action: str
    actor: str
    block_citation: str
    block_owner: str
    completion_evidence: str
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "outcome": self.outcome,
            "action": self.action,
            "actor": self.actor,
            "block_citation": self.block_citation,
            "block_owner": self.block_owner,
            "completion_evidence": self.completion_evidence,
            "diagnostics": list(self.diagnostics),
        }


def _accepted_closure_evidence(
    status: ProjectStatus, evidence: _AcceptanceEvidence
) -> str:
    """The repo-relative closure-receipt record path, or ``""`` (Prompt 031).

    Decision 6 resolution 3: ``complete`` requires explicit accepted closure
    evidence produced by the Decision 5 review/verdict authority path.
    Filenames never suffice; the qualifying shape is the real generated
    closure receipt:

    1. the selected status has a real detailed roadmap with at least one
       parsed authored slice (the same selected snapshot already yielded
       ``no_frontier``; empty, unreadable, ambiguous, or slice-free roadmaps
       never reach this helper with a qualifying state);
    2. the terminal authored slice in the selected detailed-roadmap order is
       accepted by its canonically parsed ``pass`` review report
       (Decision 5: report authority, present in ``evidence.pass_reports``);
    3. a contained verdict record is paired to that exact report by its live
       ``## Source`` citation with no contradiction (so the report is not in
       ``unrecorded_pass_reports``); and
    4. the same bounded record read yielded the generated closure fields
       (:class:`_ClosureReceipt`): the exact terminal slice ID and milestone,
       parsed verdict ``pass``, ``Kind: milestone_complete``, and
       ``Next slice: none``, agreeing with the independently selected roadmap
       slice.

    The returned evidence is the record path — the accepted closure receipt —
    never a report filename. Ordinary bookkeeping records, Source-only
    records, terminal-looking names, record/journal prose, profile data,
    optional registers, empty work, retry exhaustion, and quiet progress all
    fail this test.
    """

    if status.detailed_roadmap is None or not status.slices:
        return ""
    terminal = status.slices[-1]
    terminal_id = terminal.slice_id.upper()
    if terminal_id not in evidence.accepted_slice_ids:
        return ""
    for receipt in evidence.closure_receipts:
        if receipt.slice_id.upper() != terminal_id:
            continue
        if receipt.milestone_id.upper() != terminal.milestone_id.upper():
            continue
        if receipt.report_path not in evidence.pass_reports:
            continue
        return receipt.record_path
    return ""


def _frontier_result(
    outcome: PlanningFrontierOutcome,
    diagnostics: list[str],
    *,
    action: str = "",
    actor: str = "",
    block_citation: str = "",
    block_owner: str = "",
    completion_evidence: str = "",
) -> PlanningFrontierStatus:
    return PlanningFrontierStatus(
        contract_id=PLANNING_FRONTIER_CONTRACT_ID,
        contract_version=PLANNING_FRONTIER_CONTRACT_VERSION,
        outcome=outcome.value,
        action=action,
        actor=actor,
        block_citation=block_citation,
        block_owner=block_owner,
        completion_evidence=completion_evidence,
        diagnostics=tuple(_cap_evidence_diagnostic(diag) for diag in diagnostics),
    )


def _compute_planning_frontier(
    status: ProjectStatus,
    resume: LoopResumeStatus,
    verdict: "ReviewVerdict | None",
    evidence: _AcceptanceEvidence,
    gate_state: str = "",
) -> PlanningFrontierStatus:
    """Map the selected typed durable state to one planning-frontier outcome.

    The one typed computation (Decision 6 resolution 2): inputs are the
    already selected :class:`ProjectStatus`, :class:`LoopResumeStatus`, typed
    review verdict, :class:`_AcceptanceEvidence` snapshot, and the existing
    human gate state string. No message prose, recommended-command string,
    journal line, roadmap prose, optional register, or profile result is
    consulted, and no second frontier engine or layout reload exists.

    Pure and total: every one of the nine :class:`LoopResumeStep` values maps
    to exactly one outcome, every fail-closed condition is refused
    individually, and nothing defaults to ``ready`` or ``complete``.
    ``HumanGateState.OPEN``, ``STOP``, and ``FINAL_HANDOFF`` remain
    independent human gates and never select an outcome; only ``blocked``
    carries frontier meaning here (``no_frontier`` is already carried by the
    resume step). Never raises and never writes.
    """

    # Decision 6 resolution 6 / Review 029: typed acceptance-authority
    # defects and verdict-record contradictions are contradictory durable
    # state and fail closed ahead of every other mapping.
    if evidence.authority_defects:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            ["contradictory durable state: acceptance authority defect"]
            + [defect.diagnostic for defect in evidence.authority_defects],
        )
    if evidence.contradictions:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            ["contradictory durable state: verdict record without accepting review report"]
            + [contradiction.diagnostic for contradiction in evidence.contradictions],
        )

    # Invalid and ambiguous roadmap state, each refused individually from
    # typed diagnostic codes (never message prose).
    invalid_codes = [
        diag.code
        for diag in status.diagnostics
        if diag.code in _FRONTIER_INVALID_ROADMAP_CODES
    ]
    if invalid_codes:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            [f"invalid roadmap state: {code}" for code in invalid_codes],
        )
    ambiguous_codes = [
        diag.code
        for diag in status.diagnostics
        if diag.code in _FRONTIER_AMBIGUOUS_ROADMAP_CODES
    ]
    if ambiguous_codes:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            [f"ambiguous roadmap selection: {code}" for code in ambiguous_codes],
        )
    rework_codes = [
        diag.code
        for diag in status.diagnostics
        if diag.code.startswith("rework_declaration_")
    ]
    if rework_codes:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            [f"invalid rework declaration state: {code}" for code in rework_codes],
        )
    rework_evidence_codes = [
        diag.code
        for diag in status.diagnostics
        if diag.code == "rework_review_output_declaration_unrecognized"
    ]
    if rework_evidence_codes:
        return _frontier_result(
            PlanningFrontierOutcome.INVALID,
            [f"invalid rework evidence state: {code}" for code in rework_evidence_codes],
        )

    # Decision 6 resolution 5: a block requires both a safe citation and a
    # named owner; a partially specified block is itself invalid.
    blocked_by_verdict = verdict is not None and verdict in (
        ReviewVerdict.BLOCKED,
        ReviewVerdict.OVERRIDE,
    )
    if blocked_by_verdict or gate_state == "blocked":
        citation = resume.review_report_path
        owner = _BLOCK_OWNER_HUMAN if blocked_by_verdict else ""
        if not citation or not owner:
            return _frontier_result(
                PlanningFrontierOutcome.INVALID,
                [
                    "blocked state without a complete citation and owner; "
                    "no partial block is accepted"
                ],
            )
        reason = (
            "parsed blocked verdict"
            if verdict == ReviewVerdict.BLOCKED
            else "parsed override verdict requiring an explicit human choice"
        )
        return _frontier_result(
            PlanningFrontierOutcome.BLOCKED,
            [f"blocked: {reason}; citation {citation}; owner {owner}"],
            block_citation=citation,
            block_owner=owner,
        )

    if resume.step == LoopResumeStep.NO_FRONTIER:
        # Decision 6 resolution 7: the native no-frontier observation is
        # preserved honestly as input evidence, never as an outcome.
        closure = _accepted_closure_evidence(status, evidence)
        if closure:
            return _frontier_result(
                PlanningFrontierOutcome.COMPLETE,
                [
                    "native no_frontier observed with explicit accepted "
                    f"closure evidence {closure}"
                ],
                completion_evidence=closure,
            )
        return _frontier_result(
            PlanningFrontierOutcome.NEEDS_SPECIFICATION,
            [
                "native no_frontier observed without explicit accepted "
                "closure evidence"
            ],
            action=_ARCHITECT_FRONTIER_ACTION,
            actor="architect",
        )

    if resume.step in (
        LoopResumeStep.MAKE_CODING_PROMPT,
        LoopResumeStep.EXECUTE_CODING_PROMPT,
        LoopResumeStep.FIX_SELF_REPORT,
        LoopResumeStep.MAKE_REVIEW_PROMPT,
        LoopResumeStep.EXECUTE_REVIEW_PROMPT,
        LoopResumeStep.FIX_REVIEW_REPORT,
        LoopResumeStep.RECORD_VERDICT,
        LoopResumeStep.FRONTIER_RECORDED,
    ):
        # Decision 6 resolution 2: declared loop steps, including the two
        # repair steps, are work, not stops.
        return _frontier_result(
            PlanningFrontierOutcome.READY,
            [f"declared loop step {resume.step.value}"],
        )

    # Unrecognized durable state fails closed (Decision 6 resolution 6).
    return _frontier_result(
        PlanningFrontierOutcome.INVALID,
        ["unrecognized loop-resume state"],
    )


def build_rework_declaration_plan(
    start: Path | str,
    *,
    pass_id: str,
    slice_ids: tuple[str, ...],
    layout_config: Path | str | None = None,
) -> ReworkDeclarationPlan:
    """Plan one bounded append-only accepted-slice rework declaration.

    The writer is available only from a genuinely ``complete`` planning
    frontier.  It snapshots the current maximum prompt sequence, validates
    every requested identifier against the selected roadmap and historical
    accepted evidence, and canonicalizes the worklist to roadmap order.
    """

    status, evidence = _build_status_with_evidence(
        start, layout_config=layout_config
    )
    errors: list[str] = []
    blockers = _layout_mutation_blockers(status.layout)
    if blockers:
        errors.append(_layout_mutation_refusal_message(blockers))

    resume, verdict, used_evidence = _loop_resume_with_verdict_and_evidence(
        status, evidence=evidence
    )
    planning = _compute_planning_frontier(
        status,
        resume,
        verdict,
        used_evidence,
    )
    if planning.outcome != PlanningFrontierOutcome.COMPLETE.value:
        errors.append("rework declaration requires a complete planning frontier")

    normalized_pass = pass_id.strip() if isinstance(pass_id, str) else ""
    if (
        not normalized_pass
        or len(normalized_pass) > MAX_REWORK_PASS_ID
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", normalized_pass) is None
    ):
        errors.append("pass_id must match [a-z][a-z0-9_-]{0,63}")

    raw_slices = tuple(slice_ids) if isinstance(slice_ids, (tuple, list)) else ()
    if not 1 <= len(raw_slices) <= MAX_REWORK_SLICES:
        errors.append(f"slice_ids must contain 1 through {MAX_REWORK_SLICES} identifiers")
    normalized_slices: list[str] = []
    for value in raw_slices:
        if not isinstance(value, str) or re.fullmatch(
            r"M\d+-S\d+", value.strip(), re.IGNORECASE
        ) is None:
            errors.append("every slice_id must be an M<number>-S<number> identifier")
            continue
        normalized_slices.append(value.strip().upper())
    if len(set(normalized_slices)) != len(normalized_slices):
        errors.append("slice_ids must not contain duplicates")

    requested = set(normalized_slices)
    roadmap_ids = tuple(item.slice_id.upper() for item in status.slices)
    unknown = requested.difference(roadmap_ids)
    if unknown:
        errors.append("every rework slice must exist in the selected detailed roadmap")
    accepted = {item.upper() for item in status.accepted_slice_ids}
    if requested.difference(accepted):
        errors.append("every rework slice must already have accepted historical evidence")
    canonical_slices = tuple(item for item in roadmap_ids if item in requested)

    if normalized_pass in {
        item.pass_id for item in evidence.rework_declarations
    }:
        errors.append("pass_id already exists in the append-only declaration history")
    declaration_sequence = len(evidence.rework_declarations) + 1
    # ``MAX_REWORK_DECLARATIONS`` is the single count authority shared with the
    # reader and the write boundary.  Refusing here keeps the planner from ever
    # proposing a declaration the reader would reject on the next read.  This is
    # the declaration-count space only; the prompt-sequence bound below stays
    # independent.
    if declaration_sequence > MAX_REWORK_DECLARATIONS:
        errors.append(REWORK_DECLARATION_COUNT_EXHAUSTED)
    baseline = max(
        (
            item.sequence
            for item in status.prompt_artifacts
            if isinstance(item.sequence, int)
        ),
        default=0,
    )
    if baseline >= MAX_REWORK_PROMPT_SEQUENCE:
        errors.append("prompt sequence space is exhausted; no fresh rework prompt can be written")

    target = (
        declaration_path(declaration_sequence, normalized_pass)
        if normalized_pass and declaration_sequence <= MAX_REWORK_DECLARATIONS
        else ""
    )
    if errors:
        return ReworkDeclarationPlan(
            root=status.root,
            valid=False,
            errors=tuple(dict.fromkeys(errors)),
            declaration=None,
            target_path=target,
            content="",
            mutation_refused=bool(blockers),
            fallback_label=(
                _layout_fallback_label_message(blockers) if blockers else ""
            ),
        )

    declaration = ReworkDeclaration(
        declaration_sequence=declaration_sequence,
        pass_id=normalized_pass,
        baseline_prompt_sequence=baseline,
        slice_ids=canonical_slices,
        path=target,
    )
    return ReworkDeclarationPlan(
        root=status.root,
        valid=True,
        errors=(),
        declaration=declaration,
        target_path=target,
        content=render_rework_declaration(declaration),
    )
