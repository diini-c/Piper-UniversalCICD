from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from piper.errors import PiperError
from piper.generate import build_context, default_output_path, generate, render

from .conftest import ProjectBuilder

PYTHON_DETECTION: dict[str, Any] = {
    "types": ["python"],
    "framework": None,
    "deploy": None,
    "package_manager": None,
    "test_runners": ["pytest"],
    "signals": {"python": ["pyproject.toml"], "docker": []},
}

NODE_DETECTION: dict[str, Any] = {
    "types": ["node"],
    "framework": "next",
    "deploy": "vercel",
    "package_manager": "pnpm",
    "test_runners": ["vitest"],
    "signals": {"node": ["package.json"], "docker": []},
}


def _load(output: str) -> dict[Any, Any]:
    """
    Parse a rendered workflow.

    The mapping is not str-keyed: YAML 1.1 resolves the `on:` trigger key to the
    boolean True, which is why this is dict[Any, Any].
    """
    parsed = yaml.safe_load(output)
    assert isinstance(parsed, dict)
    return parsed


class TestRenderedYaml:
    def test_output_is_valid_yaml(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": '[project]\nrequires-python = ">=3.10"\n'})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        assert workflow["name"] == "CI"
        assert "python" in workflow["jobs"]

    def test_python_versions_stay_strings(self, make_project: ProjectBuilder) -> None:
        """Unquoted 3.10 parses as the float 3.1 and silently runs the wrong interpreter."""
        root = make_project({"pyproject.toml": '[project]\nrequires-python = ">=3.10"\n'})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        versions = workflow["jobs"]["python"]["strategy"]["matrix"]["python-version"]
        assert versions == ["3.10", "3.11", "3.12", "3.13"]
        assert all(isinstance(version, str) for version in versions)

    def test_version_floor_comes_from_requires_python(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": '[project]\nrequires-python = ">=3.12"\n'})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        assert workflow["jobs"]["python"]["strategy"]["matrix"]["python-version"] == [
            "3.12",
            "3.13",
        ]

    def test_checkout_is_always_the_first_step(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        assert workflow["jobs"]["python"]["steps"][0]["uses"] == "actions/checkout@v4"

    def test_pytest_step_present_when_runner_detected(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        runs = [step.get("run") for step in workflow["jobs"]["python"]["steps"]]
        assert "python -m pytest" in runs

    def test_pytest_step_absent_without_runner(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        detection = {**PYTHON_DETECTION, "test_runners": []}
        workflow = _load(render(detection, root=str(root)))
        runs = [step.get("run") for step in workflow["jobs"]["python"]["steps"]]
        assert "python -m pytest" not in runs

    def test_precommit_replaces_individual_linters(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {"pyproject.toml": "[tool.ruff]\n", ".pre-commit-config.yaml": "repos:\n"}
        )
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        runs = " ".join(str(step.get("run")) for step in workflow["jobs"]["python"]["steps"])
        assert "pre-commit run --all-files" in runs
        assert "ruff check" not in runs

    def test_individual_linters_when_no_precommit(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[tool.ruff]\n[tool.black]\n[tool.mypy]\n"})
        workflow = _load(render(PYTHON_DETECTION, root=str(root)))
        runs = " ".join(str(step.get("run")) for step in workflow["jobs"]["python"]["steps"])
        assert "ruff check" in runs
        assert "black --check" in runs
        assert "mypy" in runs


class TestNodeJob:
    def test_pnpm_setup_precedes_node_setup(self, make_project: ProjectBuilder) -> None:
        """setup-node cannot cache pnpm unless pnpm is installed first."""
        root = make_project({"package.json": {"name": "x"}, "pnpm-lock.yaml": ""})
        workflow = _load(render(NODE_DETECTION, root=str(root)))
        uses = [step.get("uses") for step in workflow["jobs"]["node"]["steps"]]
        assert uses.index("pnpm/action-setup@v4") < uses.index("actions/setup-node@v4")

    def test_frozen_lockfile_install(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "pnpm-lock.yaml": ""})
        workflow = _load(render(NODE_DETECTION, root=str(root)))
        runs = [step.get("run") for step in workflow["jobs"]["node"]["steps"]]
        assert "pnpm install --frozen-lockfile" in runs

    def test_npm_project_has_no_pnpm_setup(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "package-lock.json": "{}"})
        detection = {**NODE_DETECTION, "package_manager": "npm"}
        workflow = _load(render(detection, root=str(root)))
        uses = [step.get("uses") for step in workflow["jobs"]["node"]["steps"]]
        assert "pnpm/action-setup@v4" not in uses

    def test_node_versions_stay_strings(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}})
        workflow = _load(render(NODE_DETECTION, root=str(root)))
        versions = workflow["jobs"]["node"]["strategy"]["matrix"]["node-version"]
        assert versions == ["20", "22"]
        assert all(isinstance(version, str) for version in versions)


class TestJobSelection:
    def test_docker_job_added_from_signal(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n", "Dockerfile": "FROM scratch"})
        detection = {
            **PYTHON_DETECTION,
            "signals": {"python": ["pyproject.toml"], "docker": ["Dockerfile"]},
        }
        workflow = _load(render(detection, root=str(root)))
        assert "docker" in workflow["jobs"]

    def test_multiple_stacks_produce_multiple_jobs(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {"pyproject.toml": "[project]\n", "package.json": {"name": "x"}, "go.mod": "module x\n"}
        )
        detection = {
            "types": ["python", "node", "go"],
            "package_manager": "npm",
            "test_runners": ["pytest"],
            "signals": {"docker": []},
        }
        workflow = _load(render(detection, root=str(root)))
        assert set(workflow["jobs"]) == {"python", "node", "go"}

    def test_jobs_have_no_matrix_for_rust(self, make_project: ProjectBuilder) -> None:
        root = make_project({"Cargo.toml": "[package]\n"})
        detection = {"types": ["rust"], "test_runners": ["cargo-test"], "signals": {"docker": []}}
        workflow = _load(render(detection, root=str(root)))
        assert "strategy" not in workflow["jobs"]["rust"]


class TestErrors:
    def test_unknown_provider(self) -> None:
        with pytest.raises(PiperError, match="unknown provider"):
            render(PYTHON_DETECTION, provider="jenkins")

    def test_unknown_provider_output_path(self) -> None:
        with pytest.raises(PiperError, match="unknown provider"):
            default_output_path("jenkins")

    def test_nothing_detected(self, tmp_path: Path) -> None:
        empty: dict[str, Any] = {"types": [], "signals": {}}
        with pytest.raises(PiperError, match="no supported technology"):
            render(empty, root=str(tmp_path))


class TestWriteToDisk:
    def test_generate_creates_workflow_file(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        path, content = generate(PYTHON_DETECTION, root=str(root))
        written = Path(path)
        assert written == root / ".github" / "workflows" / "ci.yml"
        assert written.read_text(encoding="utf-8") == content

    def test_custom_output_path(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        path, _ = generate(PYTHON_DETECTION, root=str(root), out="ci/pipeline.yml")
        assert Path(path) == root / "ci" / "pipeline.yml"

    def test_generation_is_deterministic(self, make_project: ProjectBuilder) -> None:
        """--check depends on identical input producing identical bytes."""
        root = make_project({"pyproject.toml": "[project]\n"})
        assert render(PYTHON_DETECTION, root=str(root)) == render(PYTHON_DETECTION, root=str(root))

    def test_custom_name_and_branch(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\n"})
        workflow = _load(
            render(
                PYTHON_DETECTION, root=str(root), workflow_name="Build", default_branch="develop"
            )
        )
        assert workflow["name"] == "Build"
        assert workflow[True]["push"]["branches"] == ["develop"]


def test_build_context_normalises_every_step(make_project: ProjectBuilder) -> None:
    """StrictUndefined requires every step to carry the full key set."""
    root = make_project({"pyproject.toml": "[project]\n"})
    context = build_context(PYTHON_DETECTION, root=str(root))
    for job in context["jobs"]:
        for step in job["steps"]:
            assert {"name", "uses", "run", "params"} <= set(step)
