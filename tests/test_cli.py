from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main


def _help(args: list[str]) -> tuple[int, str]:
    """Run ``main(args)`` for a --help invocation, returning (exit_code, stdout).

    argparse raises ``SystemExit`` after printing help; ``--help`` exits 0.
    """
    stdout = StringIO()
    code = 0
    with redirect_stdout(stdout):
        try:
            main(args)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, stdout.getvalue()


class CliTests(unittest.TestCase):
    def test_status_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\n### M001: Package Scaffold\n\nStatus: active\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["next_milestone"]["id"], "M001")


class CliHelpTests(unittest.TestCase):
    def test_top_level_help_exits_zero_and_describes_loop(self) -> None:
        code, out = _help(["--help"])
        self.assertEqual(code, 0)
        # describes the artifact-first loop and lists every command
        self.assertIn("artifact-first", out)
        for command in (
            "status",
            "next",
            "make-coding-prompt",
            "make-review-prompt",
            "record-verdict",
        ):
            self.assertIn(command, out)
        # top-level epilog carries a PowerShell workflow example
        self.assertIn("common workflow", out)
        self.assertIn("python.exe -m frutlups status ..", out)

    def test_status_help(self) -> None:
        code, out = _help(["status", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("read-only", out)
        self.assertIn("--json", out)
        self.assertIn("examples", out)
        self.assertIn("frutlups status ..", out)

    def test_next_help(self) -> None:
        code, out = _help(["next", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("read-only", out)
        self.assertIn("frutlups next ..", out)

    def test_make_coding_prompt_help(self) -> None:
        code, out = _help(["make-coding-prompt", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--dry-run", out)
        self.assertIn("--sequence", out)
        self.assertIn("frutlups make-coding-prompt ..", out)

    def test_make_review_prompt_help(self) -> None:
        code, out = _help(["make-review-prompt", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("self-report", out)
        self.assertIn("frutlups make-review-prompt ..", out)

    def test_record_verdict_help(self) -> None:
        code, out = _help(["record-verdict", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--review-report", out)
        self.assertIn("frutlups record-verdict ..", out)
        # The help runs from 08_pkg, where --review-report resolves against the
        # cwd; the example must reach the project root with ..\, not a bare
        # 05_governance/... path that would resolve under 08_pkg and fail.
        self.assertIn("--review-report ..\\05_governance", out)
        self.assertNotIn("--review-report 05_governance", out)

    def test_top_level_help_record_verdict_example_is_cwd_correct(self) -> None:
        # The top-level --help epilog also shows a record-verdict example.
        code, out = _help(["--help"])
        self.assertEqual(code, 0)
        self.assertNotIn("--review-report 05_governance", out)

    def test_examples_use_powershell_venv_style(self) -> None:
        # every command's help should show the venv-style PowerShell invocation
        for args in (
            ["--help"],
            ["status", "--help"],
            ["next", "--help"],
            ["make-coding-prompt", "--help"],
            ["make-review-prompt", "--help"],
            ["record-verdict", "--help"],
        ):
            _, out = _help(args)
            self.assertIn(".venv\\Scripts\\python.exe", out, msg=str(args))

    def test_no_command_prints_help_and_exits_zero(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("usage:", stdout.getvalue())


class CliBehaviorPreservedTests(unittest.TestCase):
    """Help additions must not change non-help command behavior or exit codes."""

    def test_status_text_output_unchanged_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\n### M001: Package Scaffold\n\nStatus: active\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(["status", str(root)])
        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("Project:", out)
        self.assertIn("Prompts:", out)

    def test_record_verdict_missing_required_arg_exits_two(self) -> None:
        # argparse usage error for a missing required option stays exit 2
        stderr = StringIO()
        code = 0
        with redirect_stderr(stderr):
            try:
                main(["record-verdict", "."])
            except SystemExit as exc:
                code = int(exc.code or 0)
        self.assertEqual(code, 2)


def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    unittest.main()
