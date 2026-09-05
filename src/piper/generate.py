from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .errors import PiperError

__all__ = ["PROVIDERS", "build_context", "render", "generate", "default_output_path"]

PROVIDERS: dict[str, str] = {"github": "github-actions.yml.j2"}

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# The newest interpreter piper will put in a build matrix.
_LATEST_PYTHON_MINOR = 13
_DEFAULT_NODE_VERSIONS = ["20", "22"]

_PROVIDER_OUTPUTS: dict[str, str] = {"github": ".github/workflows/ci.yml"}


# Every step is rendered with this key set so StrictUndefined can stay enabled.
_STEP_DEFAULTS: dict[str, Any] = {"uses": None, "run": None, "params": {}}


def _normalise_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return {**_STEP_DEFAULTS, **step}


def default_output_path(provider: str) -> str:
    try:
        return _PROVIDER_OUTPUTS[provider]
    except KeyError:
        raise PiperError(f"unknown provider {provider!r}") from None


def _quote(value: str) -> str:
    """
    YAML-quote a scalar.

    Without this, a python-version of 3.10 parses as the float 3.1 and the job
    silently runs on the wrong interpreter.
    """
    return '"' + value.replace('"', '\\"') + '"'


def _python_versions(root: Path) -> list[str]:
    """Derive a version matrix from requires-python, falling back to a sane default."""
    floor = 10
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        match = re.search(r'requires-python\s*=\s*["\']\s*>=\s*3\.(\d+)', text)
        if match:
            floor = int(match.group(1))

    floor = max(floor, 8)
    top = max(floor, _LATEST_PYTHON_MINOR)
    return [f"3.{minor}" for minor in range(floor, top + 1)]


def _lint_steps(root: Path) -> list[dict[str, Any]]:
    """
    Prefer the project's own pre-commit config over re-declaring each linter.

    Duplicating the linter list in CI is how CI and local checks drift apart.
    """
    if (root / ".pre-commit-config.yaml").exists():
        return [
            {"name": "Install pre-commit", "run": "python -m pip install pre-commit"},
            {"name": "Run pre-commit", "run": "pre-commit run --all-files --show-diff-on-failure"},
        ]

    steps: list[dict[str, Any]] = []
    pyproject_text = ""
    if (root / "pyproject.toml").exists():
        try:
            pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pyproject_text = ""

    if "[tool.ruff" in pyproject_text or (root / "ruff.toml").exists():
        steps.append({"name": "Lint (ruff)", "run": "python -m pip install ruff && ruff check ."})
    if "[tool.black]" in pyproject_text:
        steps.append(
            {
                "name": "Format check (black)",
                "run": "python -m pip install black && black --check .",
            }
        )
    if "[tool.mypy]" in pyproject_text or (root / "mypy.ini").exists():
        steps.append({"name": "Typecheck (mypy)", "run": "python -m pip install mypy && mypy"})
    return steps


def _python_job(detection: Mapping[str, Any], root: Path) -> dict[str, Any]:
    versions = _python_versions(root)
    steps: list[dict[str, Any]] = [
        {
            "name": "Set up Python ${{ matrix.python-version }}",
            "uses": "actions/setup-python@v5",
            "params": {"python-version": "${{ matrix.python-version }}", "cache": _quote("pip")},
        }
    ]

    if (root / "requirements.txt").exists():
        steps.append(
            {
                "name": "Install dependencies",
                "run": (
                    "python -m pip install --upgrade pip "
                    "&& python -m pip install -r requirements.txt"
                ),
            }
        )
    else:
        # Not every project defines a dev extra, so fall back to a plain editable install.
        steps.append({"name": "Upgrade pip", "run": "python -m pip install --upgrade pip"})
        steps.append(
            {
                "name": "Install dependencies",
                "run": 'python -m pip install -e ".[dev]" || python -m pip install -e .',
            }
        )

    steps.extend(_lint_steps(root))

    if "pytest" in (detection.get("test_runners") or []):
        steps.append({"name": "Run tests", "run": "python -m pytest"})

    return {
        "id": "python",
        "name": "Python",
        "matrix": {"key": "python-version", "options": [_quote(v) for v in versions]},
        "steps": steps,
    }


def _node_job(detection: Mapping[str, Any], root: Path) -> dict[str, Any]:
    manager = str(detection.get("package_manager") or "npm")
    steps: list[dict[str, Any]] = []

    # setup-node can only cache pnpm once pnpm itself is on PATH.
    if manager == "pnpm":
        steps.append(
            {
                "name": "Set up pnpm",
                "uses": "pnpm/action-setup@v4",
                "params": {"version": _quote("9")},
            }
        )

    steps.append(
        {
            "name": "Set up Node ${{ matrix.node-version }}",
            "uses": "actions/setup-node@v4",
            "params": {"node-version": "${{ matrix.node-version }}", "cache": _quote(manager)},
        }
    )

    if (root / "pnpm-lock.yaml").exists():
        install = "pnpm install --frozen-lockfile"
    elif (root / "yarn.lock").exists():
        install = "yarn install --frozen-lockfile"
    elif (root / "package-lock.json").exists():
        install = "npm ci"
    else:
        install = f"{manager} install"
    steps.append({"name": "Install dependencies", "run": install})

    runners = detection.get("test_runners") or []
    if any(runner in runners for runner in ("vitest", "jest", "mocha")):
        steps.append({"name": "Run tests", "run": f"{manager} test"})
    if "playwright" in runners:
        steps.append(
            {"name": "Install Playwright browsers", "run": "npx playwright install --with-deps"}
        )
        steps.append({"name": "Run Playwright tests", "run": "npx playwright test"})

    return {
        "id": "node",
        "name": "Node",
        "matrix": {"key": "node-version", "options": [_quote(v) for v in _DEFAULT_NODE_VERSIONS]},
        "steps": steps,
    }


def _rust_job() -> dict[str, Any]:
    return {
        "id": "rust",
        "name": "Rust",
        "matrix": None,
        "steps": [
            {"name": "Set up Rust", "uses": "dtolnay/rust-toolchain@stable"},
            {"name": "Cache cargo", "uses": "Swatinem/rust-cache@v2"},
            {"name": "Check formatting", "run": "cargo fmt --check"},
            {"name": "Clippy", "run": "cargo clippy -- -D warnings"},
            {"name": "Run tests", "run": "cargo test --all-features"},
        ],
    }


def _go_job() -> dict[str, Any]:
    return {
        "id": "go",
        "name": "Go",
        "matrix": None,
        "steps": [
            {
                "name": "Set up Go",
                "uses": "actions/setup-go@v5",
                "params": {"go-version": _quote("stable")},
            },
            {"name": "Vet", "run": "go vet ./..."},
            {"name": "Run tests", "run": "go test ./..."},
        ],
    }


def _docker_job() -> dict[str, Any]:
    return {
        "id": "docker",
        "name": "Docker build",
        "matrix": None,
        "steps": [
            {"name": "Set up Buildx", "uses": "docker/setup-buildx-action@v3"},
            {
                "name": "Build image",
                "uses": "docker/build-push-action@v6",
                "params": {
                    "context": _quote("."),
                    "push": "false",
                    "tags": _quote("piper-ci:test"),
                },
            },
        ],
    }


def build_context(
    detection: Mapping[str, Any],
    root: str = ".",
    workflow_name: str = "CI",
    default_branch: str = "main",
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Turn a detection result into the data the workflow template renders."""
    base = Path(root)
    types = detection.get("types") or []
    signals = detection.get("signals") or {}

    jobs: list[dict[str, Any]] = []
    if "python" in types:
        jobs.append(_python_job(detection, base))
    if "node" in types:
        jobs.append(_node_job(detection, base))
    if "rust" in types:
        jobs.append(_rust_job())
    if "go" in types:
        jobs.append(_go_job())
    if signals.get("docker"):
        jobs.append(_docker_job())

    for job in jobs:
        job["steps"] = [_normalise_step(step) for step in job["steps"]]

    return {
        "workflow_name": workflow_name,
        "default_branch": default_branch,
        "version": version,
        "jobs": jobs,
    }


def render(
    detection: Mapping[str, Any],
    provider: str = "github",
    root: str = ".",
    workflow_name: str = "CI",
    default_branch: str = "main",
) -> str:
    """Render a pipeline definition and verify it is valid YAML before returning it."""
    try:
        template_name = PROVIDERS[provider]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise PiperError(f"unknown provider {provider!r}; expected one of: {known}") from None

    if not (detection.get("types") or detection.get("signals", {}).get("docker")):
        raise PiperError(
            "no supported technology detected, so there is nothing to generate. "
            "Run `piper scan` to see what was found."
        )

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    context = build_context(detection, root, workflow_name, default_branch)
    output = env.get_template(template_name).render(**context)

    # A template that renders but produces broken YAML is worse than one that fails
    # loudly, so the generator refuses to hand back something Actions would reject.
    try:
        yaml.safe_load(output)
    except yaml.YAMLError as exc:  # pragma: no cover - guards against template regressions
        raise PiperError(f"generated pipeline is not valid YAML: {exc}") from exc

    return output


def generate(
    detection: Mapping[str, Any],
    provider: str = "github",
    root: str = ".",
    out: str | None = None,
    workflow_name: str = "CI",
    default_branch: str = "main",
) -> tuple[str, str]:
    """Render a pipeline and write it to disk. Returns (path, content)."""
    content = render(detection, provider, root, workflow_name, default_branch)
    target = Path(root) / (out or default_output_path(provider))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target), content
