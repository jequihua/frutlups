"""Documentation invariants for the M015-S02 artifact-template integration guide.

Guards that the package-local integration guide exists, is discoverable from the
package README, and stays accurate about the artifact-first template: it names
the load-bearing workspaces and loop-state artifacts, uses the Python 3.11 venv
invocation style, presents the loop order, distinguishes logical roles from
providers, and frames llloom as optional/read-only. These are cheap,
deterministic checks over repository files; nothing is built, executed, or
written.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# 08_pkg/ package workspace root (parent of this tests/ directory).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_GUIDE = _PKG_ROOT / "ARTIFACT_TEMPLATE_GUIDE.md"
_README = _PKG_ROOT / "README.md"


class ArtifactTemplateGuideDocsTests(unittest.TestCase):
    def test_guide_file_exists(self) -> None:
        self.assertTrue(
            _GUIDE.is_file(), "08_pkg/ARTIFACT_TEMPLATE_GUIDE.md is missing"
        )

    def test_readme_links_guide(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        self.assertIn(
            "ARTIFACT_TEMPLATE_GUIDE.md",
            readme,
            "README does not link ARTIFACT_TEMPLATE_GUIDE.md",
        )

    def test_guide_uses_venv_invocation_style(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8")
        self.assertIn(".venv\\Scripts\\python.exe", text)

    def test_guide_names_loop_state_artifacts(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        for fragment in (
            "context.md",
            "for_coding_agent",
            "for_review_agent",
            "05_governance",
            "known_divergences",
        ):
            self.assertIn(fragment, text, f"guide omits '{fragment}'")

    def test_guide_presents_loop_order(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
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

    def test_guide_distinguishes_roles_from_providers(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        for role in ("architect", "reviewer", "coder", "human"):
            self.assertIn(role, text, f"guide omits the '{role}' role")
        # roles are described as logical / provider-neutral
        self.assertIn("logical", text)
        self.assertIn("provider-neutral", text)

    def test_guide_notes_memory_optional_read_only(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("llloom", text)
        self.assertIn("optional", text)
        self.assertIn("read-only", text)


if __name__ == "__main__":
    unittest.main()
