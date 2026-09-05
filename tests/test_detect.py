from __future__ import annotations

import json
from pathlib import Path

from piper.detect import detect_at, scan, write_report

from .conftest import ProjectBuilder


def test_empty_directory_detects_nothing(tmp_path: Path) -> None:
    detection = detect_at(tmp_path)
    assert detection["types"] == []
    assert detection["framework"] is None
    assert detection["deploy"] is None
    assert detection["package_manager"] is None


def test_python_project_from_pyproject(make_project: ProjectBuilder) -> None:
    root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
    detection = detect_at(root)
    assert detection["types"] == ["python"]
    assert detection["signals"]["python"] == ["pyproject.toml"]


def test_multiple_types_are_all_reported(make_project: ProjectBuilder) -> None:
    root = make_project(
        {
            "pyproject.toml": "[project]\nname='x'\n",
            "package.json": {"name": "x"},
            "Cargo.toml": "[package]\nname='x'\n",
            "go.mod": "module x\n",
        }
    )
    assert detect_at(root)["types"] == ["node", "python", "rust", "go"]


class TestFrameworkDetection:
    """Framework must come from declared dependencies, not from build-tool filenames."""

    def test_vite_alone_is_not_vue(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "vite.config.ts": ""})
        # The original implementation reported "vue" here purely because of the filename.
        assert detect_at(root)["framework"] == "vite"

    def test_vite_with_react_dependency_is_react(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {
                "package.json": {"name": "x", "dependencies": {"react": "18.2.0"}},
                "vite.config.ts": "",
            }
        )
        assert detect_at(root)["framework"] == "react"

    def test_vite_with_svelte_dependency_is_svelte(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {
                "package.json": {"name": "x", "devDependencies": {"svelte": "4.0.0"}},
                "vite.config.ts": "",
            }
        )
        assert detect_at(root)["framework"] == "svelte"

    def test_next_config_wins(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "next.config.js": ""})
        assert detect_at(root)["framework"] == "next"

    def test_next_dependency_without_config(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x", "dependencies": {"next": "14"}}})
        assert detect_at(root)["framework"] == "next"

    def test_nuxt_config_reported_as_nuxt(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "nuxt.config.ts": ""})
        assert detect_at(root)["framework"] == "nuxt"

    def test_python_framework_from_pyproject(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {"pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi>=0.110"]\n'}
        )
        assert detect_at(root)["framework"] == "fastapi"

    def test_python_framework_from_requirements(self, make_project: ProjectBuilder) -> None:
        root = make_project({"requirements.txt": "Django[bcrypt]>=4.2  # web\ngunicorn\n"})
        assert detect_at(root)["framework"] == "django"

    def test_poetry_dependencies_are_read(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {"pyproject.toml": '[tool.poetry.dependencies]\nflask = "^3.0"\npython = "^3.11"\n'}
        )
        assert detect_at(root)["framework"] == "flask"


class TestDeployDetection:
    def test_vercel_beats_docker(self, make_project: ProjectBuilder) -> None:
        root = make_project({"vercel.json": "{}", "Dockerfile": "FROM scratch"})
        assert detect_at(root)["deploy"] == "vercel"

    def test_docker_is_the_fallback(self, make_project: ProjectBuilder) -> None:
        root = make_project({"Dockerfile": "FROM scratch"})
        assert detect_at(root)["deploy"] == "docker"

    def test_fly_is_recognised(self, make_project: ProjectBuilder) -> None:
        root = make_project({"fly.toml": "app='x'"})
        assert detect_at(root)["deploy"] == "fly"


class TestPackageManager:
    def test_pnpm_lockfile(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "pnpm-lock.yaml": ""})
        assert detect_at(root)["package_manager"] == "pnpm"

    def test_yarn_lockfile(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}, "yarn.lock": ""})
        assert detect_at(root)["package_manager"] == "yarn"

    def test_npm_is_default_for_bare_package_json(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}})
        assert detect_at(root)["package_manager"] == "npm"

    def test_package_manager_field_is_honoured(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x", "packageManager": "pnpm@9.1.0"}})
        assert detect_at(root)["package_manager"] == "pnpm"

    def test_none_for_non_node_project(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        assert detect_at(root)["package_manager"] is None


class TestTestRunners:
    def test_pytest_from_tests_directory(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n", "tests/test_a.py": ""})
        assert "pytest" in detect_at(root)["test_runners"]

    def test_vitest_from_dev_dependencies(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x", "devDependencies": {"vitest": "2"}}})
        assert "vitest" in detect_at(root)["test_runners"]

    def test_cargo_and_go(self, make_project: ProjectBuilder) -> None:
        root = make_project({"Cargo.toml": "[package]\nname='x'\n", "go.mod": "module x\n"})
        runners = detect_at(root)["test_runners"]
        assert "cargo-test" in runners
        assert "go-test" in runners

    def test_no_runner_when_nothing_indicates_one(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "x"}})
        assert detect_at(root)["test_runners"] == []


class TestMalformedInput:
    """A broken config file must degrade gracefully rather than abort the scan."""

    def test_invalid_package_json(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": "{ not json at all"})
        detection = detect_at(root)
        assert detection["types"] == ["node"]
        assert detection["framework"] is None

    def test_invalid_pyproject(self, make_project: ProjectBuilder) -> None:
        root = make_project({"pyproject.toml": "this is [[[ not toml"})
        detection = detect_at(root)
        assert detection["types"] == ["python"]
        assert detection["framework"] is None


class TestWorkspaces:
    def test_nested_projects_are_found(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {
                "pyproject.toml": "[project]\nname='root'\n",
                "apps/web/package.json": {"name": "web", "dependencies": {"react": "18"}},
                "services/api/requirements.txt": "flask\n",
            }
        )
        detection = scan(str(root), depth=2)
        found = {workspace["root"]: workspace for workspace in detection["workspaces"]}
        assert set(found) == {"apps/web", "services/api"}
        assert found["apps/web"]["framework"] == "react"
        assert found["services/api"]["framework"] == "flask"

    def test_depth_zero_skips_workspaces(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {"pyproject.toml": "[project]\nname='root'\n", "apps/web/package.json": {"name": "web"}}
        )
        assert scan(str(root), depth=0)["workspaces"] == []

    def test_node_modules_is_never_scanned(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {
                "package.json": {"name": "root"},
                "node_modules/left-pad/package.json": {"name": "left-pad"},
            }
        )
        assert scan(str(root), depth=3)["workspaces"] == []

    def test_gitignored_directories_are_skipped(self, make_project: ProjectBuilder) -> None:
        root = make_project(
            {
                "package.json": {"name": "root"},
                ".gitignore": "generated/\n",
                "generated/package.json": {"name": "gen"},
                "kept/package.json": {"name": "kept"},
            }
        )
        workspaces = {w["root"] for w in scan(str(root), depth=2)["workspaces"]}
        assert workspaces == {"kept"}

    def test_directories_without_signals_are_omitted(self, make_project: ProjectBuilder) -> None:
        root = make_project({"package.json": {"name": "root"}, "docs/readme.md": "hi"})
        assert scan(str(root), depth=2)["workspaces"] == []


class TestWriteReport:
    def test_creates_parent_directories(self, make_project: ProjectBuilder, tmp_path: Path) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        target = tmp_path / "deep" / "nested" / "detection.json"
        returned = write_report(detect_at(root), str(target))
        assert returned == str(target)
        assert json.loads(target.read_text())["types"] == ["python"]

    def test_output_ends_with_newline(self, make_project: ProjectBuilder, tmp_path: Path) -> None:
        root = make_project({"pyproject.toml": "[project]\nname='x'\n"})
        target = tmp_path / "detection.json"
        write_report(detect_at(root), str(target))
        assert target.read_text().endswith("\n")
