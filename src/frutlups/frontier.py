"""Frontier-v2: the routing transition computed from separated closure receipts (M005-S01).

Routing is the operating tool's third dimension, never inferred from a
verdict plus slice position and never implied by the objective status
(``05_governance/current/review_protocol.md`` Closure Record;
``docs/template_framework/closure_convergence.md`` Objective Status Is Not
A Verdict). This module computes that dimension from the parsed closure
evidence of the current slice's review report.

Milestone completion requires explicit accepted closure evidence. Over the
finite domain of Prompt 006, the explicitly compatible accepted-closure
combinations are:

- an accepting verdict (``pass`` or ``override``) with ``Objective status:
  achieved`` on the milestone's last slice; and
- an accepting verdict with ``Objective status: not_applicable`` together
  with an explicit ``milestone_complete`` routing status supplied by the
  operating tool.

Everything else never completes: ``needs_work`` recodes the same slice,
``blocked`` unblocks the same slice, an accepting verdict with
``not_achieved`` or ``indeterminate`` is a legal receipt that requires human
routing, an accepting verdict with ``not_applicable`` and no compatible
explicit routing status requires human routing, absent or refused closure
evidence routes ``invalid``, and last-slice position alone never completes.
"""

from __future__ import annotations

from dataclasses import dataclass

from frutlups.closure import (
    CLOSURE_REASON_CODES,
    ClosureParseResult,
    ClosureReceipt,
    FrutlupsRoute,
    ObjectiveStatus,
    build_closure_receipt,
    parse_closure_decision_text,
)
from frutlups.review_report import ReviewVerdict

__all__ = [
    "ACCEPTING_VERDICTS",
    "FrontierTransition",
    "compute_frontier_transition",
    "frontier_transition_from_report_text",
]

ACCEPTING_VERDICTS: frozenset[ReviewVerdict] = frozenset(
    {ReviewVerdict.PASS, ReviewVerdict.OVERRIDE}
)
"""Verdicts that accept the delivered change; both stay subject to the objective."""


@dataclass(frozen=True)
class FrontierTransition:
    """The computed route, whether it completes the milestone, and why.

    ``receipt`` carries the three separated dimensions when closure evidence
    was admissible; it is ``None`` when the route is ``invalid`` because the
    evidence was absent, refused, or the explicit routing status was outside
    the route vocabulary. ``milestone_complete`` is ``True`` exactly when
    ``route`` is ``milestone_complete``. ``reason`` is a short stable code
    naming the branch that produced the route (the causal witness).
    """

    route: FrutlupsRoute
    milestone_complete: bool
    reason: str
    receipt: ClosureReceipt | None

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route.value,
            "milestone_complete": self.milestone_complete,
            "reason": self.reason,
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
        }


def _invalid(reason: str) -> FrontierTransition:
    return FrontierTransition(
        route=FrutlupsRoute.INVALID, milestone_complete=False, reason=reason, receipt=None
    )


def _admission_refusal(closure: object) -> str:
    """Name why ``closure`` is not a coherent parser result, or ``""``.

    ``ClosureParseResult`` is publicly constructible, so ``valid=True`` is a
    claim to verify, not authority (M005-R1-F1). A result is admitted only in
    a state the released parser emits: an exact ``ClosureParseResult`` whose
    ``reason_codes`` is a tuple of contract codes, and either a refusal
    (``valid`` exactly ``False`` with at least one code) — reported as
    ``closure_refused:<codes>`` — or an acceptance (``valid`` exactly ``True``,
    no codes, ``verdict`` a ``ReviewVerdict``, ``objective_status`` an
    ``ObjectiveStatus``, and non-blank string evidence and next move).
    Anything else is ``closure_incoherent:<field>``. Never raises; never
    echoes foreign values.
    """

    if type(closure) is not ClosureParseResult:
        return "closure_incoherent:type"
    field = closure.__dict__.get  # a missing field reads as None: never raises
    codes = field("reason_codes")
    if type(codes) is not tuple or any(code not in CLOSURE_REASON_CODES for code in codes):
        return "closure_incoherent:reason_codes"
    valid = field("valid")
    if valid is False:
        return "closure_refused:" + ",".join(codes) if codes else "closure_incoherent:valid"
    if valid is not True:
        return "closure_incoherent:valid"
    if codes:
        return "closure_incoherent:reason_codes"
    if type(field("verdict")) is not ReviewVerdict:
        return "closure_incoherent:verdict"
    if type(field("objective_status")) is not ObjectiveStatus:
        return "closure_incoherent:objective_status"
    for name in ("objective_evidence", "next_move"):
        value = field(name)
        if type(value) is not str or not value.strip():
            return f"closure_incoherent:{name}"
    return ""


def _route_for(
    verdict: ReviewVerdict,
    objective_status: ObjectiveStatus,
    is_last_slice: bool,
    explicit_routing_status: FrutlupsRoute | None,
) -> tuple[FrutlupsRoute, str]:
    if verdict is ReviewVerdict.NEEDS_WORK:
        return FrutlupsRoute.RECODE_SAME_SLICE, "needs_work_recodes_same_slice"
    if verdict is ReviewVerdict.BLOCKED:
        return FrutlupsRoute.UNBLOCK_SAME_SLICE, "blocked_unblocks_same_slice"
    # accepting verdict: pass or override
    if objective_status is ObjectiveStatus.ACHIEVED:
        if is_last_slice:
            return FrutlupsRoute.MILESTONE_COMPLETE, "accepted_achieved_last_slice"
        return FrutlupsRoute.ADVANCE_TO_NEXT_SLICE, "accepted_achieved_advances"
    if objective_status is ObjectiveStatus.NOT_APPLICABLE:
        if explicit_routing_status is FrutlupsRoute.MILESTONE_COMPLETE:
            return (
                FrutlupsRoute.MILESTONE_COMPLETE,
                "accepted_not_applicable_explicit_milestone_complete",
            )
        if explicit_routing_status is FrutlupsRoute.ADVANCE_TO_NEXT_SLICE:
            return (
                FrutlupsRoute.ADVANCE_TO_NEXT_SLICE,
                "accepted_not_applicable_explicit_advance",
            )
        return (
            FrutlupsRoute.HUMAN_OVERRIDE_REQUIRED,
            "accepted_not_applicable_without_compatible_routing_status",
        )
    # accepted + not_achieved / indeterminate: legal receipt, never completion
    return (
        FrutlupsRoute.HUMAN_OVERRIDE_REQUIRED,
        f"accepted_{objective_status.value}_requires_human_routing",
    )


def compute_frontier_transition(
    closure: ClosureParseResult | None,
    *,
    is_last_slice: bool,
    explicit_routing_status: object = None,
) -> FrontierTransition:
    """Compute the frontier-v2 route from parsed closure evidence.

    ``closure`` is the current slice's parsed review report, or ``None`` when
    no closure evidence exists; absent, refused, or incoherent evidence
    (see :func:`_admission_refusal`) routes ``invalid`` before any route or
    receipt is selected and never completes. ``is_last_slice`` is positional
    information only: it selects between advancing and completing once
    accepted ``achieved`` evidence exists and is never sufficient alone.
    ``explicit_routing_status`` is the operating tool's optional explicit
    routing status, consulted only for accepted ``not_applicable`` receipts;
    a value outside the route vocabulary routes ``invalid``. Never raises.
    """

    if closure is None:
        return _invalid("closure_evidence_absent")
    refusal = _admission_refusal(closure)
    if refusal:
        return _invalid(refusal)

    explicit: FrutlupsRoute | None = None
    if explicit_routing_status is not None:
        if isinstance(explicit_routing_status, FrutlupsRoute):
            explicit = explicit_routing_status
        elif (
            type(explicit_routing_status) is str
            and explicit_routing_status in FrutlupsRoute.__members__.values()
        ):
            explicit = FrutlupsRoute(explicit_routing_status)
        else:
            return _invalid("explicit_routing_status_invalid")

    verdict = closure.verdict
    objective_status = closure.objective_status
    if not isinstance(verdict, ReviewVerdict) or not isinstance(objective_status, ObjectiveStatus):
        # Unreachable: admission above already proved both fields are exact enum
        # members; narrow them for the typed route selection and fail closed.
        return _invalid("closure_incoherent:verdict")
    route, reason = _route_for(verdict, objective_status, bool(is_last_slice), explicit)
    built = build_closure_receipt(verdict, objective_status, route)
    if built.receipt is None:  # unreachable over the finite domain; fail closed
        return _invalid("receipt_refused:" + built.reason)
    return FrontierTransition(
        route=route,
        milestone_complete=route is FrutlupsRoute.MILESTONE_COMPLETE,
        reason=reason,
        receipt=built.receipt,
    )


def frontier_transition_from_report_text(
    content: object,
    *,
    is_last_slice: bool,
    explicit_routing_status: object = None,
) -> FrontierTransition:
    """Parse report text and compute its frontier-v2 transition in one call."""

    return compute_frontier_transition(
        parse_closure_decision_text(content),
        is_last_slice=is_last_slice,
        explicit_routing_status=explicit_routing_status,
    )
