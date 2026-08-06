"""Logical agent role configuration.

The package treats architect, reviewer, and coder as logical roles. Provider or
model choices are configuration, not core assumptions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class AgentRole(StrEnum):
    """Logical roles in the artifact-first loop."""

    ARCHITECT = "architect"
    REVIEWER = "reviewer"
    CODER = "coder"
    HUMAN = "human"


class AgentMode(StrEnum):
    """How an assigned agent is reached. Descriptive only — never executed.

    ``api`` is a hosted/provider API, ``local`` is a locally-run model, and
    ``manual`` is a human or file/manual handoff. This is metadata, not a
    dispatch instruction; the package never calls a provider.
    """

    API = "api"
    LOCAL = "local"
    MANUAL = "manual"


AGENT_MODES: tuple[str, ...] = (
    AgentMode.API.value,
    AgentMode.LOCAL.value,
    AgentMode.MANUAL.value,
)
"""Stable tuple of the controlled ``mode`` vocabulary for :class:`AgentProfile`.

``family``, ``provider``, and ``model`` are open descriptive strings so any
present or future provider can be represented; ``mode`` is intentionally a small
controlled set so configs stay comparable.
"""


REQUIRED_AGENT_ROLES: tuple[AgentRole, ...] = (
    AgentRole.ARCHITECT,
    AgentRole.REVIEWER,
    AgentRole.CODER,
    AgentRole.HUMAN,
)
"""The four core logical roles every project config is expected to assign.

The order is load-bearing: it is the stable order used by
:meth:`RoleConfig.assigned_roles`, :meth:`RoleConfig.missing_roles`, and
:func:`validate_role_config`. The tuple is provider-neutral; it says nothing
about which provider or model family fills a role.
"""


def required_agent_roles() -> tuple[AgentRole, ...]:
    """Return the stable tuple of required logical roles."""

    return REQUIRED_AGENT_ROLES


@dataclass(frozen=True)
class AgentProfile:
    """Provider/model metadata for a role assignment.

    Descriptive only: this schema records *what* an agent is, never *how* to
    call it. There are no credentials, endpoints, sampling parameters, or
    execution settings here. All provider fields are optional so the same
    profile shape represents GPT-family, Anthropic/Claude-family,
    local/manual, and future-family agents without privileging any provider.

    Fields:
      ``label``        human-readable name (required, non-empty)
      ``family``       open descriptive family string (e.g. ``gpt``, ``anthropic``)
      ``provider``     open descriptive provider/vendor string
      ``model``        open descriptive model string
      ``mode``         one of :data:`AGENT_MODES` (``api``/``local``/``manual``)
      ``capabilities`` ordered descriptive capability tags
      ``notes``        ordered freeform notes
    """

    label: str
    family: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: str | None = None
    capabilities: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe mapping of this profile.

        Never raises for constructible-but-malformed ``capabilities``/``notes``:
        a non-list/tuple value is rendered as a single plain-Python placeholder,
        and non-string entries inside a sequence are rendered as placeholders.
        This mirrors the non-raising contract of
        :func:`validate_agent_profile`, which still reports those malformed
        values as errors. Valid profiles serialize unchanged.
        """

        return {
            "label": self.label,
            "family": self.family,
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "capabilities": _coerce_plain_sequence(self.capabilities),
            "notes": _coerce_plain_sequence(self.notes),
        }


def validate_agent_profile(profile: AgentProfile) -> tuple[str, ...]:
    """Return deterministic validation errors for ``profile`` (empty when valid).

    Pure and read-only; never raises for constructible inputs. ``label`` must be
    a non-empty string; ``family``/``provider``/``model`` must each be ``None``
    or a non-empty string; ``mode`` must be ``None`` or one of
    :data:`AGENT_MODES`; ``capabilities`` and ``notes`` must be tuples/lists of
    non-empty strings.
    """

    if not isinstance(profile, AgentProfile):
        return ("profile must be an AgentProfile instance",)

    errors: list[str] = []
    if not isinstance(profile.label, str) or not profile.label.strip():
        errors.append("label must be a non-empty string")

    for field_name in ("family", "provider", "model"):
        value = getattr(profile, field_name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{field_name} must be a non-empty string or None")

    if profile.mode is not None:
        if not isinstance(profile.mode, str):
            errors.append("mode must be a string or None")
        elif profile.mode not in AGENT_MODES:
            errors.append(f"mode must be one of {', '.join(AGENT_MODES)} or None")

    for field_name in ("capabilities", "notes"):
        value = getattr(profile, field_name)
        if not isinstance(value, (tuple, list)):
            errors.append(f"{field_name} must be a tuple or list of non-empty strings")
            continue
        for index, entry in enumerate(value):
            if not isinstance(entry, str) or not entry.strip():
                errors.append(f"{field_name}[{index}] must be a non-empty string")

    return tuple(errors)


def _coerce_plain(value: object) -> object:
    """Return a JSON-safe plain-Python representation of ``value``.

    Used so serialization tolerates malformed constructible inputs without
    raising: well-typed scalars pass through unchanged, anything else is
    rendered with ``repr`` so the malformed value stays visible and
    JSON-serializable.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _coerce_plain_sequence(value: object) -> object:
    """Return a JSON-safe representation of a string-sequence field.

    A ``tuple``/``list`` becomes a list with each entry coerced via
    :func:`_coerce_plain` (so non-string entries stay visible and
    JSON-serializable). Any other value is coerced as a single placeholder
    rather than crashing on ``list(...)``. Never raises.
    """

    if isinstance(value, (tuple, list)):
        return [_coerce_plain(entry) for entry in value]
    return _coerce_plain(value)


@dataclass(frozen=True)
class RoleAssignment:
    """Mapping between a logical role and an agent profile."""

    role: AgentRole
    profile: AgentProfile

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe mapping of this assignment.

        Never raises for constructible-but-malformed fields: a ``role`` that
        is not an :class:`AgentRole` and a ``profile`` that is not an
        :class:`AgentProfile` are serialized as plain-Python placeholders
        rather than crashing. This keeps serialization consistent with the
        non-raising contract of :func:`validate_role_config`, which still
        reports those wrong-typed fields as errors.
        """

        role = self.role.value if isinstance(self.role, AgentRole) else _coerce_plain(self.role)
        profile = (
            self.profile.to_dict()
            if isinstance(self.profile, AgentProfile)
            else _coerce_plain(self.profile)
        )
        return {
            "role": role,
            "profile": profile,
        }


@dataclass(frozen=True)
class RoleConfig:
    """Configurable role mapping for one project.

    The same :class:`AgentProfile` may be assigned to more than one role
    (for example architect and reviewer sharing one family), roles may be
    swapped freely, and there is no assumption that any provider is required.
    The inspection helpers (:meth:`assigned_roles`, :meth:`missing_roles`,
    :meth:`duplicate_roles`) are deterministic and never raise for
    constructible-but-malformed ``assignments``.
    """

    assignments: tuple[RoleAssignment, ...]

    def profile_for(self, role: AgentRole) -> AgentProfile | None:
        """Return the profile assigned to ``role``, or ``None``.

        When ``role`` is assigned more than once, the first assignment in
        order wins; :meth:`duplicate_roles` surfaces the duplication.
        """

        for assignment in self.assignments:
            if getattr(assignment, "role", None) == role:
                return getattr(assignment, "profile", None)
        return None

    def assigned_roles(self) -> tuple[AgentRole, ...]:
        """Return the distinct assigned roles in stable order.

        Roles are ordered by :data:`REQUIRED_AGENT_ROLES` first, then any
        remaining roles in first-appearance order. Malformed assignment
        entries (non-``RoleAssignment`` or non-``AgentRole`` roles) are
        skipped rather than raising.
        """

        present = list(_iter_assigned_roles(self.assignments))
        ordered: list[AgentRole] = []
        for role in REQUIRED_AGENT_ROLES:
            if role in present and role not in ordered:
                ordered.append(role)
        for role in present:
            if role not in ordered:
                ordered.append(role)
        return tuple(ordered)

    def missing_roles(self, required: Iterable[AgentRole] | None = None) -> tuple[AgentRole, ...]:
        """Return required roles that have no assignment, in required order.

        ``required`` defaults to :data:`REQUIRED_AGENT_ROLES`. Order follows
        ``required``; never raises for malformed assignments.
        """

        required_roles = tuple(required) if required is not None else REQUIRED_AGENT_ROLES
        assigned = set(_iter_assigned_roles(self.assignments))
        return tuple(role for role in required_roles if role not in assigned)

    def duplicate_roles(self) -> tuple[AgentRole, ...]:
        """Return roles assigned more than once, in stable order."""

        counts: dict[AgentRole, int] = {}
        for role in _iter_assigned_roles(self.assignments):
            counts[role] = counts.get(role, 0) + 1
        return tuple(role for role in self.assigned_roles() if counts.get(role, 0) > 1)

    def to_dict(self) -> dict[str, object]:
        return {
            "assignments": [
                assignment.to_dict()
                for assignment in self.assignments
                if isinstance(assignment, RoleAssignment)
            ],
        }


def _iter_assigned_roles(assignments: object):
    """Yield ``AgentRole`` values from ``assignments``, skipping malformed entries.

    Defensive against non-iterable ``assignments`` and entries that are not
    ``RoleAssignment`` instances or whose ``role`` is not an ``AgentRole``.
    """

    if not isinstance(assignments, (tuple, list)):
        return
    for assignment in assignments:
        role = getattr(assignment, "role", None)
        if isinstance(role, AgentRole):
            yield role


def validate_role_config(
    config: RoleConfig,
    required: Iterable[AgentRole] | None = None,
) -> tuple[str, ...]:
    """Return deterministic validation errors for ``config`` (empty when valid).

    Surfaces non-``RoleConfig`` input, malformed ``assignments`` entries,
    wrong-typed role/profile fields, missing required roles, and duplicate
    role assignments. Pure and read-only: no filesystem access, no provider
    assumptions, and never raises for constructible inputs.
    """

    if not isinstance(config, RoleConfig):
        return ("config must be a RoleConfig instance",)

    errors: list[str] = []
    assignments = config.assignments
    if not isinstance(assignments, (tuple, list)):
        return ("assignments must be a tuple or list of RoleAssignment instances",)

    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, RoleAssignment):
            errors.append(f"assignments[{index}] must be a RoleAssignment instance")
            continue
        if not isinstance(assignment.role, AgentRole):
            errors.append(f"assignments[{index}].role must be an AgentRole")
        if not isinstance(assignment.profile, AgentProfile):
            errors.append(f"assignments[{index}].profile must be an AgentProfile")
        else:
            for profile_error in validate_agent_profile(assignment.profile):
                errors.append(f"assignments[{index}].profile: {profile_error}")

    for role in config.missing_roles(required):
        errors.append(f"missing required role: {role.value}")
    for role in config.duplicate_roles():
        errors.append(f"duplicate assignment for role: {role.value}")

    return tuple(errors)


def role_config_from_assignments(
    assignments: Mapping[AgentRole, AgentProfile] | Iterable[RoleAssignment],
) -> RoleConfig:
    """Build a :class:`RoleConfig` from a role->profile mapping or assignments.

    A ``Mapping`` is expanded into ``RoleAssignment`` entries in the mapping's
    iteration order (insertion order for a plain ``dict``); any other iterable
    is materialised verbatim. Deterministic and read-only; it does not validate
    (call :func:`validate_role_config` for that).
    """

    if isinstance(assignments, Mapping):
        items = tuple(RoleAssignment(role, profile) for role, profile in assignments.items())
    else:
        items = tuple(assignments)
    return RoleConfig(assignments=items)


def _manual_human_profile() -> AgentProfile:
    return AgentProfile(
        label="human project owner",
        family="human",
        mode=AgentMode.MANUAL.value,
    )


def _four_role_config(
    architect: AgentProfile,
    reviewer: AgentProfile,
    coder: AgentProfile,
    human: AgentProfile,
) -> RoleConfig:
    return RoleConfig(
        assignments=(
            RoleAssignment(AgentRole.ARCHITECT, architect),
            RoleAssignment(AgentRole.REVIEWER, reviewer),
            RoleAssignment(AgentRole.CODER, coder),
            RoleAssignment(AgentRole.HUMAN, human),
        )
    )


def default_role_config() -> RoleConfig:
    """Return a common preset: shared GPT architect/reviewer, Anthropic coder.

    One preset among many, never a requirement. Architect and reviewer share a
    single GPT-family API profile, the coder uses an Anthropic/Claude-family API
    profile, and the human is a manual/local role. Swap families freely or build
    a different config with :func:`role_config_from_assignments`.
    """

    gpt_reviewer = AgentProfile(
        label="gpt architect/reviewer", family="gpt", mode=AgentMode.API.value
    )
    anthropic_coder = AgentProfile(
        label="anthropic coder", family="anthropic", mode=AgentMode.API.value
    )
    return _four_role_config(gpt_reviewer, gpt_reviewer, anthropic_coder, _manual_human_profile())


def local_role_config() -> RoleConfig:
    """Preset: all roles run locally/manually, with no GPT/Anthropic family.

    Demonstrates that the core loop has no provider requirement.
    """

    local_agent = AgentProfile(label="local model", family="local", mode=AgentMode.LOCAL.value)
    return _four_role_config(local_agent, local_agent, local_agent, _manual_human_profile())


def same_family_role_config(family: str = "gpt") -> RoleConfig:
    """Preset: architect, reviewer, and coder share one ``family``.

    The human remains a manual/local role. ``family`` is a descriptive string;
    any value (including a future family) is accepted.
    """

    shared = AgentProfile(label=f"{family} (shared)", family=family, mode=AgentMode.API.value)
    return _four_role_config(shared, shared, shared, _manual_human_profile())


def swapped_role_config() -> RoleConfig:
    """Preset: the default GPT/Anthropic families inverted.

    Architect and reviewer use the Anthropic/Claude family; the coder uses the
    GPT family. The human remains a manual/local role.
    """

    anthropic_reviewer = AgentProfile(
        label="anthropic architect/reviewer",
        family="anthropic",
        mode=AgentMode.API.value,
    )
    gpt_coder = AgentProfile(label="gpt coder", family="gpt", mode=AgentMode.API.value)
    return _four_role_config(
        anthropic_reviewer, anthropic_reviewer, gpt_coder, _manual_human_profile()
    )


@dataclass(frozen=True)
class RoleConfigPreset:
    """A named, documented example :class:`RoleConfig`.

    Presets are examples, not requirements. ``to_dict()`` returns plain Python.
    """

    name: str
    description: str
    config: RoleConfig

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "config": self.config.to_dict(),
        }


def role_config_presets() -> tuple[RoleConfigPreset, ...]:
    """Return the built-in example presets in stable order.

    These are illustrative configurations only; none is required. The order is
    load-bearing for deterministic listing/serialization.
    """

    return (
        RoleConfigPreset(
            name="common_gpt_anthropic",
            description=(
                "Common example: GPT-family architect/reviewer (shared), "
                "Anthropic/Claude-family coder, manual human. One preset among "
                "many, not a requirement."
            ),
            config=default_role_config(),
        ),
        RoleConfigPreset(
            name="all_local_manual",
            description=(
                "All roles local/manual with no GPT/Anthropic family; shows the "
                "core loop needs no provider."
            ),
            config=local_role_config(),
        ),
        RoleConfigPreset(
            name="same_family",
            description=("Architect, reviewer, and coder share one family; human manual."),
            config=same_family_role_config(),
        ),
        RoleConfigPreset(
            name="swapped_gpt_anthropic",
            description=(
                "Default GPT/Anthropic families inverted: Anthropic "
                "architect/reviewer, GPT coder, manual human."
            ),
            config=swapped_role_config(),
        ),
    )


def role_config_preset(name: str) -> RoleConfigPreset | None:
    """Return the preset named ``name``, or ``None`` if there is no such preset."""

    for preset in role_config_presets():
        if preset.name == name:
            return preset
    return None
