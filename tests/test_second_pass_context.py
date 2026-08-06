"""Tests for M013-S03: second-pass prompt context model and renderer.

Covers the pure context model (frontier + evidence + authority note), JSON-safe
serialization including malformed constructible inputs, deterministic
validation, the markdown renderer's required sections, empty-collection
rendering, and deterministic bounded rendering with visible omission/truncation.
File-based tests use the live read-only collector and assert no files created.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.second_pass import (
    Frontier,
    FrontierSelectionKind,
    PassKind,
    build_pass_frontier,
)
from frutlups.second_pass_evidence import (
    FollowUpItem,
    FollowUpKind,
    KnownDivergence,
    build_second_pass_evidence,
)
from frutlups.second_pass_context import (
    AUTHORITY_NOTE,
    RenderOptions,
    SecondPassContext,
    build_second_pass_context,
    collect_second_pass_context,
    render_second_pass_context,
    validate_second_pass_context,
)


def _frontier(**overrides: object) -> Frontier:
    defaults: dict[str, object] = dict(
        milestone_id="M013",
        slice_id="M013-S03",
        title="generate second-pass prompt context",
        selection_kind=FrontierSelectionKind.HUMAN_SELECTED,
    )
    defaults.update(overrides)
    return Frontier(**defaults)  # type: ignore[arg-type]


def _pass_frontier(**overrides: object):
    defaults: dict[str, object] = dict(
        pass_number=2,
        label="second pass over M013",
        kind=PassKind.SECOND_PASS,
        frontier=_frontier(),
        accepted_baseline_slice_ids=("M013-S01", "M013-S02"),
        evidence_paths=(
            "05_governance/reviews/m013_s01_pass_frontier_data_model_review_report.md",
        ),
    )
    defaults.update(overrides)
    return build_pass_frontier(**defaults)  # type: ignore[arg-type]


def _evidence(**overrides: object):
    defaults: dict[str, object] = dict(
        slice_id="M013-S03",
        accepted_follow_ups=(
            FollowUpItem(
                source_path="05_governance/reviews/m013_s01_x_review_report.md",
                text="residual risk: validates shape, not roadmap existence",
                kind=FollowUpKind.RESIDUAL_RISK,
                source_slice_id="M013-S01",
                accepted=True,
            ),
        ),
        known_divergences=(
            KnownDivergence(
                source_path="05_governance/known_divergences.md",
                identifier="2026-05-24: Inherited Illustrative Prompt Files Remain",
                title="Inherited Illustrative Prompt Files Remain",
                body="The prompt folders still contain inherited files.",
            ),
        ),
        diagnostics=("pass report m001_scaffold_review_report.md has no verdict record",),
    )
    defaults.update(overrides)
    return build_second_pass_evidence(**defaults)  # type: ignore[arg-type]


def _context(**overrides: object) -> SecondPassContext:
    pf = overrides.pop("pass_frontier", _pass_frontier())
    ev = overrides.pop("evidence", _evidence())
    return build_second_pass_context(pf, ev, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Model + serialization
# ---------------------------------------------------------------------------

class ContextModelTests(unittest.TestCase):
    def test_valid_context_no_errors(self) -> None:
        self.assertEqual(validate_second_pass_context(_context()), ())

    def test_to_dict_json_safe(self) -> None:
        d = _context().to_dict()
        json.dumps(d)
        self.assertEqual(d["pass_frontier"]["frontier"]["slice_id"], "M013-S03")
        self.assertEqual(d["evidence"]["slice_id"], "M013-S03")
        self.assertEqual(d["authority_note"], AUTHORITY_NOTE)

    def test_default_authority_note(self) -> None:
        self.assertEqual(_context().authority_note, AUTHORITY_NOTE)

    def test_not_an_instance_flagged(self) -> None:
        self.assertTrue(validate_second_pass_context(object()))  # type: ignore[arg-type]

    def test_nested_validation_prefixes(self) -> None:
        bad_pf = _pass_frontier(pass_number=0)
        bad_ev = build_second_pass_evidence(slice_id="bad")
        errs = validate_second_pass_context(build_second_pass_context(bad_pf, bad_ev))
        self.assertTrue(any(e.startswith("pass_frontier:") for e in errs))
        self.assertTrue(any(e.startswith("evidence:") for e in errs))

    def test_empty_authority_note_flagged(self) -> None:
        ctx = build_second_pass_context(_pass_frontier(), _evidence(), authority_note="  ")
        self.assertTrue(
            any("authority_note" in e for e in validate_second_pass_context(ctx))
        )


class MalformedSerializationTests(unittest.TestCase):
    def test_malformed_members_serialize(self) -> None:
        ctx = SecondPassContext(
            pass_frontier=object(),  # type: ignore[arg-type]
            evidence=object(),  # type: ignore[arg-type]
            authority_note=object(),  # type: ignore[arg-type]
        )
        # validation reports the malformed members...
        self.assertTrue(validate_second_pass_context(ctx))
        # ...but to_dict() still serializes
        json.dumps(ctx.to_dict())

    def test_render_options_malformed_serialize(self) -> None:
        json.dumps(RenderOptions(max_follow_ups=object()).to_dict())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class RenderTests(unittest.TestCase):
    def test_includes_pass_identity_and_frontier(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Pass", out)
        self.assertIn("second pass over M013", out)
        self.assertIn("second_pass", out)
        self.assertIn("## Frontier", out)
        self.assertIn("M013-S03", out)
        self.assertIn("generate second-pass prompt context", out)
        self.assertIn("human_selected", out)

    def test_includes_baseline_and_evidence_paths(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Accepted Baseline Slice IDs", out)
        self.assertIn("M013-S01", out)
        self.assertIn("M013-S02", out)
        self.assertIn("## Evidence Paths", out)
        self.assertIn("m013_s01_pass_frontier_data_model_review_report.md", out)

    def test_includes_follow_ups_with_fields(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Accepted Follow-Ups", out)
        self.assertIn("residual_risk", out)
        self.assertIn("[M013-S01]", out)
        self.assertIn("m013_s01_x_review_report.md", out)
        self.assertIn("validates shape, not roadmap existence", out)

    def test_includes_divergences_with_fields(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Known Divergences", out)
        self.assertIn("Inherited Illustrative Prompt Files Remain", out)
        self.assertIn("known_divergences.md", out)
        self.assertIn("inherited files", out.lower())

    def test_includes_diagnostics_as_diagnostics(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Evidence Diagnostics", out)
        self.assertIn("not authoritative facts", out)
        self.assertIn("m001_scaffold_review_report.md has no verdict record", out)

    def test_includes_authority_posture(self) -> None:
        out = render_second_pass_context(_context())
        self.assertIn("## Authority", out)
        self.assertIn("Repository artifacts remain authoritative", out)
        self.assertIn("read-only", out)

    def test_empty_collections_render_deterministically(self) -> None:
        ctx = build_second_pass_context(
            _pass_frontier(accepted_baseline_slice_ids=(), evidence_paths=()),
            build_second_pass_evidence(slice_id="M013-S03"),
        )
        out = render_second_pass_context(ctx)
        out2 = render_second_pass_context(ctx)
        self.assertEqual(out, out2)
        self.assertIn("## Accepted Follow-Ups\n\n- None.", out)
        self.assertIn("## Known Divergences\n\n- None.", out)
        self.assertIn("## Evidence Paths\n\n- None.", out)

    def test_malformed_context_renders_without_raising(self) -> None:
        ctx = SecondPassContext(
            pass_frontier=object(),  # type: ignore[arg-type]
            evidence=object(),  # type: ignore[arg-type]
        )
        out = render_second_pass_context(ctx)
        self.assertIn("Pass identity unavailable", out)
        self.assertIn("Frontier unavailable", out)


class BoundedRenderTests(unittest.TestCase):
    def _many(self) -> SecondPassContext:
        fus = tuple(
            FollowUpItem(source_path=f"r{i}.md", text=f"text {i}", source_slice_id="M013-S01")
            for i in range(5)
        )
        divs = tuple(
            KnownDivergence(source_path="kd.md", identifier=f"H{i}", title=f"H{i}", body=f"b{i}")
            for i in range(4)
        )
        ev = build_second_pass_evidence(
            slice_id="M013-S03", accepted_follow_ups=fus, known_divergences=divs
        )
        return build_second_pass_context(_pass_frontier(), ev)

    def test_caps_make_omissions_visible(self) -> None:
        out = render_second_pass_context(
            self._many(), options=RenderOptions(max_follow_ups=2, max_divergences=1)
        )
        self.assertIn("3 more follow-up(s) omitted", out)
        self.assertIn("3 more divergence(s) omitted", out)

    def test_text_truncation_visible(self) -> None:
        ev = build_second_pass_evidence(
            slice_id="M013-S03",
            accepted_follow_ups=(
                FollowUpItem(source_path="r.md", text="x" * 200, source_slice_id="M013-S01"),
            ),
        )
        out = render_second_pass_context(
            build_second_pass_context(_pass_frontier(), ev),
            options=RenderOptions(max_text_chars=40),
        )
        self.assertIn("[truncated]", out)

    def _follow_up_ctx(self, text: str) -> SecondPassContext:
        ev = build_second_pass_evidence(
            slice_id="M013-S03",
            accepted_follow_ups=(
                FollowUpItem(source_path="r.md", text=text, source_slice_id="M013-S01"),
            ),
        )
        return build_second_pass_context(_pass_frontier(), ev)

    def _divergence_ctx(self, body: str) -> SecondPassContext:
        ev = build_second_pass_evidence(
            slice_id="M013-S03",
            known_divergences=(
                KnownDivergence(source_path="kd.md", identifier="H", title="H", body=body),
            ),
        )
        return build_second_pass_context(_pass_frontier(), ev)

    def test_small_positive_cap_marks_follow_up_truncation(self) -> None:
        # Reproduces the review-066 probe: a 5-char cap must stay visible.
        for cap in (1, 5):
            out = render_second_pass_context(
                self._follow_up_ctx("abcdefghijklmnopqrstuvwxyz"),
                options=RenderOptions(max_text_chars=cap),
            )
            self.assertIn("[truncated]", out, msg=f"cap={cap}")

    def test_small_positive_cap_marks_divergence_truncation(self) -> None:
        for cap in (1, 5):
            out = render_second_pass_context(
                self._divergence_ctx("abcdefghijklmnopqrstuvwxyz"),
                options=RenderOptions(max_text_chars=cap),
            )
            self.assertIn("[truncated]", out, msg=f"cap={cap}")

    def test_zero_cap_does_not_silently_omit(self) -> None:
        out = render_second_pass_context(
            self._follow_up_ctx("abcdefghijklmnopqrstuvwxyz"),
            options=RenderOptions(max_text_chars=0),
        )
        self.assertIn("[truncated]", out)

    def test_none_cap_preserves_unbounded_text(self) -> None:
        long_text = "y" * 200
        out = render_second_pass_context(
            self._follow_up_ctx(long_text),
            options=RenderOptions(max_text_chars=None),
        )
        self.assertIn(long_text, out)
        self.assertNotIn("[truncated]", out)

    def test_text_at_or_below_cap_not_marked(self) -> None:
        out = render_second_pass_context(
            self._follow_up_ctx("short"),
            options=RenderOptions(max_text_chars=10),
        )
        self.assertNotIn("[truncated]", out)

    def test_bounded_render_deterministic(self) -> None:
        ctx = self._many()
        opts = RenderOptions(max_follow_ups=2, max_divergences=2, max_text_chars=30)
        self.assertEqual(
            render_second_pass_context(ctx, options=opts),
            render_second_pass_context(ctx, options=opts),
        )


# ---------------------------------------------------------------------------
# Live read-only collector probe (temp dir; no files created)
# ---------------------------------------------------------------------------

class CollectContextTests(unittest.TestCase):
    def test_collect_renders_and_creates_no_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # minimal project shape so build_next_frontier can run
            (root / "00_brief").mkdir()
            (root / "prompts").mkdir()
            exp = root / "03_experiments"
            exp.mkdir()
            (exp / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\n## M013: Second-Pass Support (active)\n",
                encoding="utf-8",
            )
            (exp / "development_roadmap_frutlups.md").write_text(
                "# Roadmap\n\n### M013-S03: generate second-pass prompt context\n",
                encoding="utf-8",
            )
            gov = root / "05_governance"
            (gov / "reviews").mkdir(parents=True)
            (gov / "known_divergences.md").write_text(
                "# Known Divergences\n\n## Example Divergence\n\nBody.\n",
                encoding="utf-8",
            )
            before = set(root.rglob("*"))
            ctx = collect_second_pass_context(root, slice_id="M013-S03")
            out = render_second_pass_context(ctx)
            json.dumps(ctx.to_dict())
            self.assertIn("# Second-Pass Prompt Context", out)
            self.assertIn("Repository artifacts remain authoritative", out)
            self.assertEqual(set(root.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
