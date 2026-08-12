"""Documentation invariants for the M015-S03 llloom integration guide.

Guards that the package-local llloom integration guide exists, is discoverable
from the package README, and stays accurate about the optional memory backend:
it frames llloom as optional/disabled-by-default and not a hard dependency,
states the read-vs-mutate distinction (mutation only in explicit memory-update
slices), keeps the authority order with repository artifacts above memory, shows
how memory appears in ``frutlups status`` (including the JSON memory keys), uses
the venv invocation style for frutlups commands, separates llloom commands from
frutlups commands, and stays provider-neutral. These are cheap, deterministic
checks over repository files; nothing is built, executed, or written.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# 08_pkg/ package workspace root (parent of this tests/ directory).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_GUIDE = _PKG_ROOT / "LLLOOM_INTEGRATION_GUIDE.md"
_README = _PKG_ROOT / "README.md"


class LlloomIntegrationGuideDocsTests(unittest.TestCase):
    def test_guide_file_exists(self) -> None:
        self.assertTrue(
            _GUIDE.is_file(), "08_pkg/LLLOOM_INTEGRATION_GUIDE.md is missing"
        )

    def test_readme_links_guide(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        self.assertIn(
            "LLLOOM_INTEGRATION_GUIDE.md",
            readme,
            "README does not link LLLOOM_INTEGRATION_GUIDE.md",
        )

    def test_guide_frames_memory_optional_and_disabled_by_default(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("optional", text)
        self.assertIn("disabled by default", text)
        # llloom must not be presented as a required/hard dependency
        self.assertIn("hard dependency", text)

    def test_guide_states_read_vs_mutate_distinction(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("read-only", text)
        self.assertIn("mutat", text)  # matches mutate / mutation
        self.assertIn("memory-update slice", text)

    def test_guide_keeps_repository_artifacts_above_memory(self) -> None:
        # normalize whitespace so line wrapping in the guide does not break
        # multi-word phrase checks
        text = " ".join(_GUIDE.read_text(encoding="utf-8").lower().split())
        self.assertIn("authority order", text)
        self.assertIn("primary loop state", text)

    def test_guide_shows_memory_in_status(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8")
        self.assertIn("frutlups status", text)
        self.assertIn("Memory: disabled", text)
        # the actual status --json memory keys
        for key in ('"enabled"', '"backend"', '"diagnostics"'):
            self.assertIn(key, text, f"guide omits memory JSON key {key}")
        self.assertIn('"memory_mode"', text)
        self.assertIn('"contract_id": "frutlups.memory_mode"', text)
        self.assertIn('"contract_version": "1"', text)
        self.assertIn("Availability can never activate", text)

    def test_guide_separates_frutlups_and_llloom_commands(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8")
        # frutlups commands use the venv interpreter
        self.assertIn(".venv\\Scripts\\python.exe -m frutlups", text)
        # llloom commands are the external tool, shown with --root
        self.assertIn("llloom --root", text)

    def test_guide_shows_memory_update_guarded_pattern(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8")
        self.assertIn("seed apply", text)
        self.assertIn("--dry-run", text)
        self.assertIn("doctor --last-op", text)

    def test_guide_notes_accepted_warnings_visible(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("accepted_warnings.yaml", text)
        self.assertIn("warning_id", text)

    def test_guide_references_memory_module_and_upstream(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("memory.py", text)
        self.assertIn("upstream", text)

    def test_guide_is_provider_neutral(self) -> None:
        text = _GUIDE.read_text(encoding="utf-8").lower()
        self.assertIn("provider-neutral", text)


if __name__ == "__main__":
    unittest.main()
