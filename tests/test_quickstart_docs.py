"""Documentation invariants for the M015-S01 quickstart.

Guards that the package-local quickstart exists, is discoverable from the
package README, and stays accurate about the artifact-first loop: it names every
CLI command, uses the Python 3.11 venv invocation style, and presents the loop
order (coding prompt -> self-report -> review prompt -> review report -> verdict
record). These are cheap, deterministic checks over repository files; nothing is
built, executed, or written.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# 08_pkg/ package workspace root (parent of this tests/ directory).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_QUICKSTART = _PKG_ROOT / "QUICKSTART.md"
_README = _PKG_ROOT / "README.md"


class QuickstartDocsTests(unittest.TestCase):
    def test_quickstart_file_exists(self) -> None:
        self.assertTrue(_QUICKSTART.is_file(), "08_pkg/QUICKSTART.md is missing")

    def test_readme_links_quickstart(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        self.assertIn("QUICKSTART.md", readme, "README does not link QUICKSTART.md")

    def test_quickstart_uses_venv_invocation_style(self) -> None:
        text = _QUICKSTART.read_text(encoding="utf-8")
        self.assertIn(".venv\\Scripts\\python.exe", text)
        self.assertIn("-m pip install -e \".[dev]\"", text)

    def test_quickstart_names_every_cli_command(self) -> None:
        text = _QUICKSTART.read_text(encoding="utf-8")
        for command in (
            "status",
            "next",
            "make-coding-prompt",
            "make-review-prompt",
            "record-verdict",
        ):
            self.assertIn(command, text, f"quickstart does not mention {command}")

    def test_quickstart_presents_loop_order(self) -> None:
        text = _QUICKSTART.read_text(encoding="utf-8").lower()
        # the artifact-first governance order must appear in sequence
        for fragment in (
            "coding prompt",
            "self-report",
            "review prompt",
            "review report",
            "verdict record",
        ):
            self.assertIn(fragment, text, f"quickstart omits '{fragment}'")
        self.assertLess(
            text.index("coding prompt"),
            text.index("verdict record"),
            "quickstart does not present the loop in coding -> verdict order",
        )

    def test_quickstart_notes_memory_optional(self) -> None:
        text = _QUICKSTART.read_text(encoding="utf-8").lower()
        self.assertIn("optional", text)
        self.assertIn("llloom", text)


if __name__ == "__main__":
    unittest.main()
