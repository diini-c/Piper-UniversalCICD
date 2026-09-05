from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .errors import PiperError

__all__ = ["ACTIONS", "PROVIDERS", "build_context", "render", "generate", "default_output_path"]

PROVIDERS: dict[str, str] = {"github": "github-actions.yml.j2"}

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# The newest interpreter piper will put in a build matrix.
_LATEST_PYTHON_MINOR = 13
_DEFAULT_NODE_VERSIONS = ["20", "22"]

_PROVIDER_OUTPUTS: dict[str, str] = {"github": ".github/workflows/ci.yml"}

# Pinned in one place because the checkout version used to live in the template
# while every other action lived here, so a bump could silently miss one.
# The actions/* family moved to Node 24 in v7; v4/v5 now emit a deprecation
# warning on every run.
ACTIONS: dict[str, str] = {
    "checkout": "actions/checkout@v7",
    "setup-python": "actions/setup-python@v7",
    "setup-node": "actions/setup-node@v7",
    "setup-go": "actions/setup-go@v7",
    "upload-artifact": "actions/upload-artifact@v7",
    # Third-party actions are left at the versions we have actually run.
    "pnpm": "pnpm/action-setup@v4",
    "paths-filter": "dorny/paths-filter@v3",
    "rust-toolchain": "dtolnay/rust-toolchain@stable",
    "rust-cache": "Swatinem/rust-cache@v2",
    "buildx": "docker/setup-buildx-action@v3",
    "docker-build": "docker/build-push-action@v6",
}


# Every step and job is rendered with this key set so StrictUndefined can stay on.
_STEP_DEFAULTS: dict[str, Any] = {"uses": None, "run": None, "params": {}, "step_id": None}
_JOB_DEFAULTS: dict[str, Any] = {
    "matrix": None,
    "needs": None,
    "if_expr": None,
    "outputs": None,
    "working_directory": None,
}


def _normalise_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return {**_STEP_DEFAULTS, **step}


def _normalise_job(job: Mapping[str, Any]) -> dict[str, Any]:
    normalised = {**_JOB_DEFAULTS, **job}
    normalised["steps"] = [_normalise_step(step) for step in normalised["steps"]]
    return normalised


def _slug(path: str) -> str:
    """Turn a workspace path into a YAML-safe, unique job-id fragment."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_").lower()
    return cleaned or "root"


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


def _cache_params(prefix: str, filename: str | None, manager: str) -> dict[str, str]:
    """
    Build the cache half of a setup-* action's `with:` block.

    setup-python and setup-node are `uses` steps, so they never inherit a job's
    defaults.run.working-directory. Inside a workspace the dependency file has to
    be named from the repository root or the action caches the wrong tree — and
    with no dependency file at all the action hard-fails, so caching is only
    requested when there is something to key it on.
    """
    if filename is None:
        return {}
    path = f"{prefix}/{filename}" if prefix else filename
    return {"cache": _quote(manager), "cache-dependency-path": _quote(path)}


def _python_job(detection: Mapping[str, Any], root: Path, prefix: str = "") -> dict[str, Any]:
    versions = _python_versions(root)
    dependency_file = next(
        (name for name in ("requirements.txt", "pyproject.toml") if (root / name).exists()),
        None,
    )
    steps: list[dict[str, Any]] = [
        {
            "name": "Set up Python ${{ matrix.python-version }}",
            "uses": ACTIONS["setup-python"],
            "params": {
                "python-version": "${{ matrix.python-version }}",
                **_cache_params(prefix, dependency_file, "pip"),
            },
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


def _node_job(detection: Mapping[str, Any], root: Path, prefix: str = "") -> dict[str, Any]:
    manager = str(detection.get("package_manager") or "npm")
    lockfile = next(
        (
            name
            for name in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json")
            if (root / name).exists()
        ),
        None,
    )
    steps: list[dict[str, Any]] = []

    # setup-node can only cache pnpm once pnpm itself is on PATH.
    if manager == "pnpm":
        steps.append(
            {
                "name": "Set up pnpm",
                "uses": ACTIONS["pnpm"],
                "params": {"version": _quote("9")},
            }
        )

    steps.append(
        {
            "name": "Set up Node ${{ matrix.node-version }}",
            "uses": ACTIONS["setup-node"],
            "params": {
                "node-version": "${{ matrix.node-version }}",
                **_cache_params(prefix, lockfile, manager),
            },
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
            {"name": "Set up Rust", "uses": ACTIONS["rust-toolchain"]},
            {"name": "Cache cargo", "uses": ACTIONS["rust-cache"]},
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
                "uses": ACTIONS["setup-go"],
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
            {"name": "Set up Buildx", "uses": ACTIONS["buildx"]},
            {
                "name": "Build image",
                "uses": ACTIONS["docker-build"],
                "params": {
                    "context": _quote("."),
                    "push": "false",
                    "tags": _quote("piper-ci:test"),
                },
            },
        ],
    }


def _jobs_for(detection: Mapping[str, Any], root: Path, prefix: str = "") -> list[dict[str, Any]]:
    """Build the per-language jobs a single project directory implies."""
    types = detection.get("types") or []
    jobs: list[dict[str, Any]] = []
    if "python" in types:
        jobs.append(_python_job(detection, root, prefix))
    if "node" in types:
        jobs.append(_node_job(detection, root, prefix))
    if "rust" in types:
        jobs.append(_rust_job())
    if "go" in types:
        jobs.append(_go_job())
    return jobs


def _changes_job(paths_by_slug: dict[str, str]) -> dict[str, Any]:
    """
    A gate job that reports which workspaces a push or PR actually touched.

    GitHub only supports path filters at workflow level, not per job, so the
    filtering has to run as a job whose outputs the workspace jobs gate on. A
    job skipped this way still reports a conclusion, which keeps it usable as a
    required status check — a workflow-level `paths:` filter would report
    nothing at all and leave branch protection waiting forever.
    """
    filters: list[str] = []
    for slug, path in paths_by_slug.items():
        filters.append(f"{slug}:")
        filters.append(f"  - '{path}/**'")

    return {
        "id": "changes",
        "name": "Detect changed workspaces",
        "outputs": {slug: "${{ steps.filter.outputs." + slug + " }}" for slug in paths_by_slug},
        "steps": [
            {
                "name": "Filter changed paths",
                "uses": ACTIONS["paths-filter"],
                "step_id": "filter",
                "params": {"filters": filters},
            }
        ],
    }


def _workspace_jobs(
    workspaces: list[Mapping[str, Any]], base: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build jobs for every nested project, plus the path map the gate needs."""
    jobs: list[dict[str, Any]] = []
    paths_by_slug: dict[str, str] = {}

    for workspace in workspaces:
        relative = str(workspace.get("root") or "").strip().strip("/")
        if not relative or relative == ".":
            continue

        slug = _slug(relative)
        built = _jobs_for(workspace, base / relative, relative)
        if not built:
            continue

        for job in built:
            job["id"] = f"{slug}_{job['id']}"
            job["name"] = f"{relative} · {job['name']}"
            # Only `run` steps inherit this; `uses` steps such as checkout still
            # operate from the repository root, which is what they expect.
            job["working_directory"] = relative
            job["needs"] = "changes"
            job["if_expr"] = f"needs.changes.outputs.{slug} == 'true'"
            jobs.append(job)

        paths_by_slug[slug] = relative

    return jobs, paths_by_slug


def build_context(
    detection: Mapping[str, Any],
    root: str = ".",
    workflow_name: str = "CI",
    default_branch: str = "main",
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Turn a detection result into the data the workflow template renders."""
    base = Path(root)
    signals = detection.get("signals") or {}

    jobs = _jobs_for(detection, base)
    if signals.get("docker"):
        jobs.append(_docker_job())

    # Nested projects each get their own job, gated on whether they changed, so a
    # commit under one workspace does not rebuild every other one.
    workspaces = detection.get("workspaces") or []
    workspace_jobs, paths_by_slug = _workspace_jobs(workspaces, base)
    if workspace_jobs:
        jobs = [_changes_job(paths_by_slug), *jobs, *workspace_jobs]

    return {
        "checkout_action": ACTIONS["checkout"],
        "workflow_name": workflow_name,
        "default_branch": default_branch,
        "version": version,
        "jobs": [_normalise_job(job) for job in jobs],
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

    if not (
        detection.get("types")
        or detection.get("signals", {}).get("docker")
        or detection.get("workspaces")
    ):
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
