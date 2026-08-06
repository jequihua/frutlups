"""Documentation invariants for the M015-S04 mature-project migration guide.

Guards that the package-local migration guide exists, is discoverable from the
package README, and stays accurate about Mode B adoption: it names the
project-root markers and the minimum viable structure, describes running
status/next on a partially-populated project, presents the loop order, keeps
adoption proportional (does not force unused workspaces), uses the Python 3.11
venv invocation style, and frames memory as optional/disabled. These are cheap,
deterministic checks over repository files; nothing is built, executed, or
written.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# 08_pkg/ package workspace root (parent of this tests/ directory).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_GUIDE = _PKG_ROOT / "MIGRATION_GUIDE.md"
_README = _PKG_ROOT / "README.md"


def _normalized(path: Path) -> str:
    """Return the file text lowercased with whitespace collapsed.

    Collapsing whitespace makes multi-word phrase checks robust against line
    wrapping in the guide.
    """
    return " ".join(path.read_text(encoding="utf-8").lower().split())


class MigrationGuideDocsTests(unittest.TestCase):
    def test_guide_file_exists(self) -> None:
        self.assertTrue(_GUIDE.is_file(), "08_pkg/MIGRATION_GUIDE.md is missing")

    def test_readme_links_guide(self) -> None:
        self.assertIn(
            "MIGRATION_GUIDE.md",
            _README.read_text(encoding="utf-8"),
            "README does not link MIGRATION_GUIDE.md",
        )

    def test_guide_uses_venv_invocation_style(self) -> None:
        self.assertIn(".venv\\Scripts\\python.exe", _GUIDE.read_text(encoding="utf-8"))

    def test_guide_describes_mode_b_incremental_adoption(self) -> None:
        text = _normalized(_GUIDE)
        self.assertIn("mode b", text)
        self.assertIn("existing", text)
        self.assertIn("incremental", text)

    def test_guide_names_project_root_markers_and_min_structure(self) -> None:
        text = _normalized(_GUIDE)
        self.assertIn("00_brief", text)
        self.assertIn("prompts/", text)
        self.assertIn("03_experiments", text)
        self.assertIn("active_roadmap", text)
        self.assertIn("05_governance", text)

    def test_guide_covers_partial_repo_status_and_diagnostics(self) -> None:
        text = _normalized(_GUIDE)
        self.assertIn("frutlups status", text)
        # real diagnostic codes / status lines the CLI emits on a partial repo
        self.assertIn("no_active_roadmap", text)
        self.assertIn("no_detailed_roadmap", text)
        self.assertIn("template health: missing required directories", text)

    def test_guide_documents_slices_marker(self) -> None:
        # review-077 blocker 1: detailed-roadmap slices need a `Slices:` marker;
        # bullets directly under the milestone heading are not parsed.
        self.assertIn(
            "Slices:",
            _GUIDE.read_text(encoding="utf-8"),
            "guide omits the required `Slices:` marker",
        )

    def test_guide_record_verdict_path_is_cwd_correct(self) -> None:
        # review-077 blocker 2: from 08_pkg/, --review-report resolves against the
        # cwd, so it must use the ..\\05_governance\\reviews\\ form, never a bare
        # 05_governance/... path.
        text = _GUIDE.read_text(encoding="utf-8")
        self.assertNotIn(
            "--review-report 05_governance",
            text,
            "guide documents a cwd-relative review-report path that fails from 08_pkg/",
        )
        self.assertIn(
            "--review-report ..\\05_governance",
            text,
            "guide should show the corrected ..\\05_governance\\reviews\\ path",
        )

    def test_guide_presents_loop_order(self) -> None:
        text = _normalized(_GUIDE)
        for fragment in (
            "coding prompt",
            "self-report",
            "review prompt",
            "review report",
            "verdict record",
        ):
            self.assertIn(fragment, text, f"guide omits '{fragment}'")
        self.assertLess(
            text.index("coding prompt"),
            text.index("verdict record"),
            "guide does not present the loop in coding -> verdict order",
        )

    def test_guide_warns_against_forcing_unused_workspaces(self) -> None:
        text = _normalized(_GUIDE)
        self.assertIn("unused workspaces", text)
        self.assertIn("proportional", text)

    def test_guide_is_provider_neutral_and_memory_optional(self) -> None:
        text = _normalized(_GUIDE)
        self.assertIn("provider-neutral", text)
        self.assertIn("optional", text)
        self.assertIn("llloom", text)


if __name__ == "__main__":
    unittest.main()
