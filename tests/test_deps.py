from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from piper.deps import plan_install, plan_tests, run, run_tests
from piper.errors import CommandFailedError, MissingToolError

from .conftest import ProjectBuilder


class TestRun:
    def test_missing_tool_raises_actionable_error(self) -> None:
        # Previously this surfaced as a bare FileNotFoundError traceback.
        with pytest.raises(MissingToolError) as excinfo:
            run(["definitely-not-a-real-binary-xyz"])
        assert "definitely-not-a-real-binary-xyz" in str(excinfo.value)

    def test_known_tool_gets_an_install_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Forced, so the assertion does not depend on pnpm being absent from the runner.
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        with pytest.raises(MissingToolError) as excinfo:
            run(["pnpm", "install"])
        assert "corepack" in str(excinfo.value)

    def test_sys_executable_bypasses_path_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # sys.executable is absolute, so a which() miss must not block it.
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        run([sys.executable, "-c", "pass"])  # must not raise

    def test_non_zero_exit_raises_command_failed(self) -> None:
        with pytest.raises(CommandFailedError) as excinfo:
            run([sys.executable, "-c", "raise SystemExit(3)"])
        assert excinfo.value.returncode == 3

    def test_success_returns_none(self) -> None:
        run([sys.executable, "-c", "pass"])  # must not raise

    def test_dry_run_never_executes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("subprocess must not run during a dry run")

        monkeypatch.setattr(subprocess, "run", explode)
        run(["definitely-not-a-real-binary-xyz"], dry_run=True)  # must not raise


class TestPlanInstall:
    def test_requirements_txt_takes_priority(self, make_project: ProjectBuilder) -> None:
        root = make_project({"requirements.txt": "click\n", "pyproject.toml": "[project]\n"})
        plan = plan_install({"types": ["python"]}, str(root))
        assert plan[-1] == [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]

    def test_pyproject_uses_editable_install(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        plan = plan_install({"types": ["python"]}, str(root))
        assert plan == [[sys.executable, "-m", "pip", "install", "-e", "."]]

    def test_npm_ci_when_lockfile_present(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "package-lock.json": "{}"})
        plan = plan_install({"types": ["node"], "package_manager": "npm"}, str(root))
        assert plan == [["npm", "ci"]]

    def test_pnpm_frozen_lockfile(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "pnpm-lock.yaml": ""})
        plan = plan_install({"types": ["node"], "package_manager": "pnpm"}, str(root))
        assert plan == [["pnpm", "install", "--frozen-lockfile"]]

    def test_yarn_frozen_lockfile(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "yarn.lock": ""})
        plan = plan_install({"types": ["node"], "package_manager": "yarn"}, str(root))
        assert plan == [["yarn", "install", "--frozen-lockfile"]]

    def test_lockfile_wins_over_declared_manager(self, make_project: ProjectBuilder) -> None:
        """A stale package_manager field must not override the lockfile actually present."""
        root = make_project({"package.json": {"name": "x"}, "pnpm-lock.yaml": ""})
        plan = plan_install({"types": ["node"], "package_manager": "npm"}, str(root))
        assert plan == [["pnpm", "install", "--frozen-lockfile"]]

    def test_rust_and_go(self, make_project: ProjectBuilder) -> None:
        root = make_project({"Cargo.toml": "[package]\n", "go.mod": "module x\n"})
        plan = plan_install({"types": ["rust", "go"]}, str(root))
        assert ["cargo", "fetch"] in plan
        assert ["go", "mod", "download"] in plan

    def test_empty_detection_produces_empty_plan(self, tmp_path: Path) -> None:
        assert plan_install({"types": []}, str(tmp_path)) == []

    def test_missing_types_key_is_tolerated(self, tmp_path: Path) -> None:
        assert plan_install({}, str(tmp_path)) == []

    def test_declared_type_without_manifest_adds_nothing(self, tmp_path: Path) -> None:
        assert plan_install({"types": ["node", "rust", "go"]}, str(tmp_path)) == []


class TestPlanTests:
    def test_pytest(self, tmp_path: Path) -> None:
        plan = plan_tests({"test_runners": ["pytest"]}, str(tmp_path))
        assert plan == [[sys.executable, "-m", "pytest"]]

    def test_node_runner_uses_package_manager(self, tmp_path: Path) -> None:
        plan = plan_tests({"test_runners": ["vitest"], "package_manager": "pnpm"}, str(tmp_path))
        assert plan == [["pnpm", "test"]]

    def test_multiple_node_runners_produce_one_command(self, tmp_path: Path) -> None:
        detection = {"test_runners": ["vitest", "jest", "mocha"], "package_manager": "npm"}
        assert plan_tests(detection, str(tmp_path)) == [["npm", "test"]]

    def test_cargo_test_requires_manifest(self, make_project: ProjectBuilder) -> None:
        root = make_project({"Cargo.toml": "[package]\n"})
        assert plan_tests({"test_runners": ["cargo-test"]}, str(root)) == [["cargo", "test"]]

    def test_cargo_test_skipped_without_manifest(self, tmp_path: Path) -> None:
        assert plan_tests({"test_runners": ["cargo-test"]}, str(tmp_path)) == []

    def test_no_runners(self, tmp_path: Path) -> None:
        assert plan_tests({"test_runners": []}, str(tmp_path)) == []


def test_run_tests_dry_run_reports_plan_without_executing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("subprocess must not run during a dry run")

    monkeypatch.setattr(subprocess, "run", explode)
    plan = run_tests({"test_runners": ["pytest"]}, str(tmp_path), dry_run=True)
    assert plan == [[sys.executable, "-m", "pytest"]]
