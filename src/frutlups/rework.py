"""Typed, bounded declarations for reopening accepted roadmap slices.

The declaration is an append-only JSON artifact.  It names only pass identity,
the prompt-sequence baseline observed by the governed writer, and canonical
roadmap slice identifiers.  No prose field participates in routing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REWORK_DECLARATION_CONTRACT_ID = "frutlups.rework_declaration"
REWORK_DECLARATION_CONTRACT_VERSION = "1"
REWORK_DECLARATION_DIR = "05_governance/rework_declarations"
MAX_REWORK_DECLARATIONS = 128
MAX_REWORK_SLICES = 64
MAX_REWORK_DECLARATION_BYTES = 16 * 1024
MAX_REWORK_PASS_ID = 64
MAX_REWORK_PROMPT_SEQUENCE = 999

# The one owned, stable, non-echoing diagnostic for declaration-count
# exhaustion.  The planner and the write boundary both raise exactly this text
# so a refusal reads identically whichever seam refuses first; it names only the
# module's own bound and never a path, pass value, or other caller input.
REWORK_DECLARATION_COUNT_EXHAUSTED = (
    "rework declaration count is exhausted; "
    f"at most {MAX_REWORK_DECLARATIONS} declarations are supported"
)

_PASS_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SLICE_ID_RE = re.compile(r"^M\d+-S\d+$", re.IGNORECASE)
_FILENAME_RE = re.compile(
    r"^(?P<sequence>\d{3})_(?P<pass_id>[a-z][a-z0-9_-]{0,63})\.json$"
)
_DECLARATION_KEYS = frozenset(
    {
        "contract_id",
        "contract_version",
        "declaration_sequence",
        "pass_id",
        "baseline_prompt_sequence",
        "slice_ids",
    }
)
_DIAGNOSTIC_LIMIT = 240


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while refusing duplicate routing keys."""

    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("rework declaration contains a duplicate JSON key")
        value[key] = item
    return value


def _bounded(message: str) -> str:
    collapsed = " ".join(str(message).split())
    if len(collapsed) <= _DIAGNOSTIC_LIMIT:
        return collapsed
    return collapsed[: _DIAGNOSTIC_LIMIT - 3] + "..."


def _safe_relative(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ReworkDeclarationDiagnostic:
    """One stable, bounded declaration defect."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ReworkDeclaration:
    """Version-1 typed declaration of one bounded accepted-slice rework pass."""

    declaration_sequence: int
    pass_id: str
    baseline_prompt_sequence: int
    slice_ids: tuple[str, ...]
    path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_id": REWORK_DECLARATION_CONTRACT_ID,
            "contract_version": REWORK_DECLARATION_CONTRACT_VERSION,
            "declaration_sequence": self.declaration_sequence,
            "pass_id": self.pass_id,
            "baseline_prompt_sequence": self.baseline_prompt_sequence,
            "slice_ids": list(self.slice_ids),
        }


@dataclass(frozen=True)
class ReworkDeclarationInventory:
    """One deterministic read of the declaration authority directory."""

    declarations: tuple[ReworkDeclaration, ...]
    diagnostics: tuple[ReworkDeclarationDiagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class ReworkDeclarationPlan:
    """Read-only plan for one append-only declaration write."""

    root: Path
    valid: bool
    errors: tuple[str, ...]
    declaration: ReworkDeclaration | None
    target_path: str
    content: str
    mutation_refused: bool = False
    fallback_label: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "valid": self.valid,
            "errors": list(self.errors),
            "declaration": self.declaration.to_dict() if self.declaration else None,
            "target_path": self.target_path,
            "would_write": self.valid,
            "mutation_refused": self.mutation_refused,
        }


@dataclass(frozen=True)
class ReworkDeclarationWriteCommand:
    """Explicit single-artifact write command for a validated plan."""

    project_root: Path
    plan: ReworkDeclarationPlan


@dataclass(frozen=True)
class ReworkDeclarationWriteResult:
    """Result of the append-only declaration writer."""

    wrote: bool
    target_path: str
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "wrote": self.wrote,
            "target_path": self.target_path,
            "errors": list(self.errors),
        }


def declaration_path(sequence: int, pass_id: str) -> str:
    """Return the canonical repo-relative path for one declaration."""

    return f"{REWORK_DECLARATION_DIR}/{sequence:03d}_{pass_id}.json"


def render_rework_declaration(declaration: ReworkDeclaration) -> str:
    """Render canonical UTF-8 JSON bytes (represented as text)."""

    return json.dumps(declaration.to_dict(), indent=2, sort_keys=True) + "\n"


def _diagnostic(code: str, path: str, message: str) -> ReworkDeclarationDiagnostic:
    return ReworkDeclarationDiagnostic(code=code, path=path, message=_bounded(message))


def _parse_declaration(path: Path, relative: str) -> tuple[ReworkDeclaration | None, tuple]:
    diagnostics: list[ReworkDeclarationDiagnostic] = []
    match = _FILENAME_RE.fullmatch(path.name)
    if match is None:
        return None, (
            _diagnostic(
                "rework_declaration_filename_invalid",
                relative,
                "rework declaration filename must be NNN_<pass_id>.json",
            ),
        )
    try:
        size = path.stat().st_size
        if size > MAX_REWORK_DECLARATION_BYTES:
            raise ValueError("rework declaration exceeds the 16384-byte bound")
        raw = path.read_bytes()
        if len(raw) > MAX_REWORK_DECLARATION_BYTES:
            raise ValueError("rework declaration exceeds the 16384-byte bound")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object)
    except OSError:
        return None, (
            _diagnostic(
                "rework_declaration_unreadable",
                relative,
                "rework declaration could not be read safely",
            ),
        )
    except UnicodeError:
        return None, (
            _diagnostic(
                "rework_declaration_unreadable",
                relative,
                "rework declaration is not valid UTF-8",
            ),
        )
    except (json.JSONDecodeError, RecursionError):
        return None, (
            _diagnostic(
                "rework_declaration_unreadable",
                relative,
                "rework declaration is not bounded valid JSON",
            ),
        )
    except ValueError as exc:
        return None, (
            _diagnostic("rework_declaration_unreadable", relative, str(exc)),
        )
    if not isinstance(value, dict):
        return None, (
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "rework declaration must be a JSON object",
            ),
        )
    if frozenset(value) != _DECLARATION_KEYS:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "rework declaration keys do not match the version-1 schema",
            )
        )
    if value.get("contract_id") != REWORK_DECLARATION_CONTRACT_ID:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_contract_invalid",
                relative,
                "unsupported rework declaration contract_id",
            )
        )
    if value.get("contract_version") != REWORK_DECLARATION_CONTRACT_VERSION:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_contract_invalid",
                relative,
                "unsupported rework declaration contract_version",
            )
        )
    sequence = value.get("declaration_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= 999:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "declaration_sequence must be an integer from 1 through 999",
            )
        )
    elif sequence != int(match.group("sequence")):
        diagnostics.append(
            _diagnostic(
                "rework_declaration_identity_mismatch",
                relative,
                "declaration_sequence does not match the filename",
            )
        )
    pass_id = value.get("pass_id")
    if not isinstance(pass_id, str) or _PASS_ID_RE.fullmatch(pass_id) is None:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "pass_id must match [a-z][a-z0-9_-]{0,63}",
            )
        )
    elif pass_id != match.group("pass_id"):
        diagnostics.append(
            _diagnostic(
                "rework_declaration_identity_mismatch",
                relative,
                "pass_id does not match the filename",
            )
        )
    baseline = value.get("baseline_prompt_sequence")
    if (
        isinstance(baseline, bool)
        or not isinstance(baseline, int)
        or not 0 <= baseline <= MAX_REWORK_PROMPT_SEQUENCE
    ):
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "baseline_prompt_sequence must be an integer from 0 through 999",
            )
        )
    raw_slices = value.get("slice_ids")
    slice_ids: tuple[str, ...] = ()
    if not isinstance(raw_slices, list) or not 1 <= len(raw_slices) <= MAX_REWORK_SLICES:
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                f"slice_ids must contain 1 through {MAX_REWORK_SLICES} identifiers",
            )
        )
    elif any(
        not isinstance(item, str) or _SLICE_ID_RE.fullmatch(item) is None
        for item in raw_slices
    ):
        diagnostics.append(
            _diagnostic(
                "rework_declaration_schema_invalid",
                relative,
                "every slice_ids member must be an M<number>-S<number> identifier",
            )
        )
    else:
        slice_ids = tuple(item.upper() for item in raw_slices)
        if len(set(slice_ids)) != len(slice_ids):
            diagnostics.append(
                _diagnostic(
                    "rework_declaration_schema_invalid",
                    relative,
                    "slice_ids must not contain duplicates",
                )
            )
    if diagnostics:
        return None, tuple(diagnostics)
    assert isinstance(sequence, int)
    assert isinstance(pass_id, str)
    assert isinstance(baseline, int)
    return (
        ReworkDeclaration(
            declaration_sequence=sequence,
            pass_id=pass_id,
            baseline_prompt_sequence=baseline,
            slice_ids=slice_ids,
            path=relative,
        ),
        (),
    )


def load_rework_declarations(root: Path) -> ReworkDeclarationInventory:
    """Read the append-only declaration directory once, bounded and contained."""

    directory = root / REWORK_DECLARATION_DIR
    if not directory.exists():
        return ReworkDeclarationInventory((), ())
    try:
        resolved_root = root.resolve()
        resolved_directory = directory.resolve()
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("rework declaration authority is not a regular directory")
        if not _is_within(resolved_directory, resolved_root):
            raise ValueError("rework declaration authority resolves outside the project root")
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except (OSError, RuntimeError, ValueError):
        return ReworkDeclarationInventory(
            (),
            (
                _diagnostic(
                    "rework_declaration_authority_invalid",
                    REWORK_DECLARATION_DIR,
                    "rework declaration authority could not be resolved safely",
                ),
            ),
        )
    if len(entries) > MAX_REWORK_DECLARATIONS:
        return ReworkDeclarationInventory(
            (),
            (
                _diagnostic(
                    "rework_declaration_bound_exceeded",
                    REWORK_DECLARATION_DIR,
                    f"rework declaration inventory exceeds {MAX_REWORK_DECLARATIONS} entries",
                ),
            ),
        )
    declarations: list[ReworkDeclaration] = []
    diagnostics: list[ReworkDeclarationDiagnostic] = []
    for entry in entries:
        relative = f"{REWORK_DECLARATION_DIR}/{entry.name}"
        try:
            resolved_entry = entry.resolve()
            regular = entry.is_file() and not entry.is_symlink()
        except (OSError, RuntimeError):
            resolved_entry = Path()
            regular = False
        if not regular or not _is_within(resolved_entry, resolved_directory):
            diagnostics.append(
                _diagnostic(
                    "rework_declaration_entry_invalid",
                    relative,
                    "rework declaration entry is not a contained regular file",
                )
            )
            continue
        declaration, found = _parse_declaration(entry, relative)
        diagnostics.extend(found)
        if declaration is not None:
            declarations.append(declaration)
    if diagnostics:
        return ReworkDeclarationInventory((), tuple(diagnostics))
    expected = list(range(1, len(declarations) + 1))
    observed = [item.declaration_sequence for item in declarations]
    if observed != expected:
        return ReworkDeclarationInventory(
            (),
            (
                _diagnostic(
                    "rework_declaration_sequence_invalid",
                    REWORK_DECLARATION_DIR,
                    "declaration_sequence values must be contiguous from 1 in filename order",
                ),
            ),
        )
    pass_ids = [item.pass_id for item in declarations]
    if len(set(pass_ids)) != len(pass_ids):
        return ReworkDeclarationInventory(
            (),
            (
                _diagnostic(
                    "rework_declaration_pass_duplicate",
                    REWORK_DECLARATION_DIR,
                    "pass_id values must be unique",
                ),
            ),
        )
    baselines = [item.baseline_prompt_sequence for item in declarations]
    if baselines != sorted(baselines):
        return ReworkDeclarationInventory(
            (),
            (
                _diagnostic(
                    "rework_declaration_baseline_invalid",
                    REWORK_DECLARATION_DIR,
                    "baseline_prompt_sequence values must be nondecreasing",
                ),
            ),
        )
    return ReworkDeclarationInventory(tuple(declarations), ())


def write_rework_declaration(
    command: ReworkDeclarationWriteCommand,
) -> ReworkDeclarationWriteResult:
    """Write exactly one new declaration, refusing replacement and escape."""

    plan = command.plan
    if (
        not plan.valid
        or plan.mutation_refused
        or plan.declaration is None
        or not plan.content
    ):
        return ReworkDeclarationWriteResult(
            False,
            plan.target_path,
            plan.errors or ("rework declaration plan is invalid",),
        )
    declaration = plan.declaration
    canonical_target = declaration_path(
        declaration.declaration_sequence, declaration.pass_id
    )
    if (
        plan.target_path != canonical_target
        or declaration.path != canonical_target
        or plan.content != render_rework_declaration(declaration)
    ):
        return ReworkDeclarationWriteResult(
            False,
            plan.target_path,
            ("rework declaration plan does not match its canonical artifact",),
        )
    if not _safe_relative(plan.target_path):
        return ReworkDeclarationWriteResult(
            False, plan.target_path, ("target_path must be a safe repository-relative path",)
        )
    root = command.project_root
    # Root identity is the first authority decision, ahead of every observation
    # of project state. Until the command root and the plan root are proven to
    # name the same resolved project, nothing under either of them may be
    # enumerated or parsed, so a foreign declaration directory is never read.
    # Only the documented data-induced resolution failures are caught here; a
    # programming error still propagates.
    try:
        resolved_root = root.resolve()
        resolved_plan_root = plan.root.resolve()
    except (OSError, RuntimeError):
        return ReworkDeclarationWriteResult(
            False, plan.target_path, ("rework declaration could not be written safely",)
        )
    if resolved_root != resolved_plan_root:
        return ReworkDeclarationWriteResult(
            False, plan.target_path, ("rework declaration could not be written safely",)
        )
    # Only the accepted root may supply the live count, and ``resolved_root`` --
    # not the caller's alias -- is that root from here on. Using the accepted
    # snapshot for every later observation and mutation keeps a command root
    # whose resolution changes after identity from redirecting the inventory
    # read or creating anything under a foreign root.
    #
    # The write boundary defends the declaration count independently of the
    # planner, before any directory or file mutation, so a stale or externally
    # constructed valid-looking plan can never leave the authority above
    # ``MAX_REWORK_DECLARATIONS`` entries -- the exact state the reader rejects.
    # Counting existing entries (rather than trusting the plan's sequence) also
    # refuses a forged plan that reuses or backdates a sequence number.
    inventory = load_rework_declarations(resolved_root)
    if not inventory.valid:
        return ReworkDeclarationWriteResult(
            False,
            plan.target_path,
            ("rework declaration inventory is not readable; refusing to write",),
        )
    if len(inventory.declarations) >= MAX_REWORK_DECLARATIONS:
        return ReworkDeclarationWriteResult(
            False, plan.target_path, (REWORK_DECLARATION_COUNT_EXHAUSTED,)
        )
    target = resolved_root / PurePosixPath(plan.target_path)
    try:
        # ``resolved_root`` is the accepted identity snapshot taken above; the
        # target, its parent creation, the containment check, and the exclusive
        # create all hang off it rather than selecting root authority again.
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
        if target.parent.is_symlink() or not target.parent.is_dir():
            raise ValueError("rework declaration parent is not a regular directory")
        if not _is_within(resolved_parent, resolved_root):
            raise ValueError("rework declaration target resolves outside the project root")
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(plan.content)
    except FileExistsError:
        return ReworkDeclarationWriteResult(
            False, plan.target_path, ("rework declaration already exists; replacement is refused",)
        )
    except (OSError, RuntimeError, ValueError):
        return ReworkDeclarationWriteResult(
            False,
            plan.target_path,
            ("rework declaration could not be written safely",),
        )
    return ReworkDeclarationWriteResult(True, plan.target_path, ())
