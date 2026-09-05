from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from piper.cli import main

from .conftest import ProjectBuilder


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def no_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI tests must never shell out; anything that tries is a bug in the test."""

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected subprocess call: {args!r}")

    monkeypatch.setattr(subprocess, "run", explode)


def test_help_lists_every_command(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("scan", "install", "test", "generate"):
        assert command in result.output


class TestScan:
    def test_writes_report_and_prints_table(
        self, runner: CliRunner, make_project: ProjectBuilder, tmp_path: Path
    ) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        report = tmp_path / "out" / "detection.json"
        result = runner.invoke(main, ["scan", "--root", str(root), "--report", str(report)])
        assert result.exit_code == 0, result.output
        assert "Piper Detection Report" in result.output
        assert json.loads(report.read_text())["types"] == ["python"]

    def test_depth_option_controls_workspace_search(
        self, runner: CliRunner, make_project: ProjectBuilder, tmp_path: Path
    ) -> None:
        root = make_project(
            {"pyproject.toml": "[project]\n", "apps/web/package.json": {"name": "web"}}
        )
        report = tmp_path / "detection.json"
        args = ["scan", "--root", str(root), "--report", str(report), "--depth", "0"]
        assert runner.invoke(main, args).exit_code == 0
        assert json.loads(report.read_text())["workspaces"] == []

    def test_empty_project_still_succeeds(self, runner: CliRunner, tmp_path: Path) -> None:
        report = tmp_path / "detection.json"
        source = tmp_path / "empty"
        source.mkdir()
        result = runner.invoke(main, ["scan", "--root", str(source), "--report", str(report)])
        assert result.exit_code == 0, result.output


class TestInstall:
    def test_dry_run_shows_commands(self, runner: CliRunner, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        result = runner.invoke(main, ["install", "--root", str(root), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "pip" in result.output

    def test_nothing_to_install(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["install", "--root", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "nothing to do" in result.output.lower()


class TestTest:
    def test_dry_run_shows_pytest(self, runner: CliRunner, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n", "tests/test_a.py": ""})
        result = runner.invoke(main, ["test", "--root", str(root), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "pytest" in result.output

    def test_no_runner_detected(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["test", "--root", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "nothing to do" in result.output.lower()


class TestGenerate:
    def test_writes_workflow(self, runner: CliRunner, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n", "tests/test_a.py": ""})
        result = runner.invoke(main, ["generate", "--root", str(root)])
        assert result.exit_code == 0, result.output
        assert (root / ".github" / "workflows" / "ci.yml").exists()

    def test_stdout_does_not_write_a_file(
        self, runner: CliRunner, make_project: ProjectBuilder
    ) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        result = runner.invoke(main, ["generate", "--root", str(root), "--stdout"])
        assert result.exit_code == 0, result.output
        assert "runs-on: ubuntu-latest" in result.output
        assert not (root / ".github").exists()

    def test_unknown_provider_is_rejected_by_click(
        self, runner: CliRunner, make_project: ProjectBuilder
    ) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        result = runner.invoke(main, ["generate", "--root", str(root), "--provider", "jenkins"])
        assert result.exit_code != 0

    def test_empty_project_fails_cleanly(self, runner: CliRunner, tmp_path: Path) -> None:
        """A PiperError must reach the user as a message, not a traceback."""
        result = runner.invoke(main, ["generate", "--root", str(tmp_path)])
        assert result.exit_code != 0
        assert "no supported technology" in result.output
        assert "Traceback" not in result.output


class TestGenerateCheck:
    def test_check_fails_when_file_is_absent(
        self, runner: CliRunner, make_project: ProjectBuilder
    ) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        result = runner.invoke(main, ["generate", "--root", str(root), "--check"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_check_passes_right_after_generate(
        self, runner: CliRunner, make_project: ProjectBuilder
    ) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        assert runner.invoke(main, ["generate", "--root", str(root)]).exit_code == 0
        result = runner.invoke(main, ["generate", "--root", str(root), "--check"])
        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_check_detects_drift(self, runner: CliRunner, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        assert runner.invoke(main, ["generate", "--root", str(root)]).exit_code == 0
        workflow = root / ".github" / "workflows" / "ci.yml"
        workflow.write_text(workflow.read_text() + "\n# edited by hand\n", encoding="utf-8")
        result = runner.invoke(main, ["generate", "--root", str(root), "--check"])
        assert result.exit_code != 0
        assert "out of date" in result.output
