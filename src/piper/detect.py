from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TypedDict

import pathspec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

__all__ = [
    "SIGNALS",
    "Detection",
    "detect_at",
    "scan",
    "write_report",
]


class Detection(TypedDict):
    """Result of inspecting a single directory (plus any nested workspaces)."""

    root: str
    types: list[str]
    framework: str | None
    deploy: str | None
    package_manager: str | None
    test_runners: list[str]
    signals: dict[str, list[str]]
    workspaces: list[Detection]


# Mapping of technology types to their identifying configuration files.
SIGNALS: dict[str, list[str]] = {
    "node": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "tox.ini"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml"],
    "next": ["next.config.js", "next.config.mjs", "next.config.ts"],
    "vue": ["vue.config.js", "nuxt.config.ts", "nuxt.config.js"],
    "vite": ["vite.config.ts", "vite.config.js", "vite.config.mjs"],
    "vercel": ["vercel.json"],
    "netlify": ["netlify.toml"],
    "heroku": ["Procfile"],
    "railway": ["railway.json", "railway.toml"],
    "fly": ["fly.toml"],
}

# Order matters: the first match wins, so the most specific platform is listed first.
_DEPLOY_PRIORITY = ["vercel", "netlify", "fly", "heroku", "railway", "docker"]

# npm package -> framework name. Checked against package.json dependencies, which is
# far more reliable than inferring from a config filename: vite.config.ts says nothing
# about whether the app is Vue, React or Svelte.
_NODE_FRAMEWORK_PACKAGES: dict[str, str] = {
    "next": "next",
    "nuxt": "nuxt",
    "@remix-run/react": "remix",
    "@angular/core": "angular",
    "svelte": "svelte",
    "vue": "vue",
    "react": "react",
    "express": "express",
    "fastify": "fastify",
}

# Same idea for Python: match distribution names found in project metadata.
_PYTHON_FRAMEWORK_PACKAGES: dict[str, str] = {
    "django": "django",
    "fastapi": "fastapi",
    "flask": "flask",
    "starlette": "starlette",
    "pyramid": "pyramid",
}

_NODE_TEST_PACKAGES: dict[str, str] = {
    "vitest": "vitest",
    "jest": "jest",
    "mocha": "mocha",
    "@playwright/test": "playwright",
    "cypress": "cypress",
}

_PYTHON_TEST_PACKAGES: dict[str, str] = {
    "pytest": "pytest",
    "nose2": "nose2",
}

# Lockfile -> package manager, most specific first.
_LOCKFILE_MANAGERS: list[tuple[str, str]] = [
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
]

# Directories never worth descending into when looking for nested projects.
_ALWAYS_SKIP = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        "target",
        "vendor",
        ".next",
        ".nuxt",
        ".pipeline",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    """Best-effort JSON read. A malformed file must not abort the whole scan."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    """Best-effort TOML read, with the same tolerance as _read_json."""
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except (OSError, ValueError):
        return {}
    return data


def _node_dependency_names(root: Path) -> set[str]:
    pkg = _read_json(root / "package.json")
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = pkg.get(section)
        if isinstance(block, dict):
            names.update(str(name).lower() for name in block)
    return names


def _split_requirement(line: str) -> str:
    """Reduce a requirement line such as 'Django[bcrypt]>=4.2  # comment' to 'django'."""
    text = line.split("#", 1)[0].strip()
    for separator in ("[", "=", ">", "<", "!", "~", ";", " ", "\t"):
        text = text.split(separator, 1)[0]
    return text.strip().lower()


def _python_dependency_names(root: Path) -> set[str]:
    names: set[str] = set()

    pyproject = _read_toml(root / "pyproject.toml")
    project = pyproject.get("project")
    if isinstance(project, dict):
        declared = project.get("dependencies")
        if isinstance(declared, list):
            names.update(_split_requirement(str(item)) for item in declared)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    names.update(_split_requirement(str(item)) for item in group)

    # Poetry keeps dependencies somewhere else entirely.
    poetry = pyproject.get("tool", {})
    if isinstance(poetry, dict):
        poetry_block = poetry.get("poetry")
        if isinstance(poetry_block, dict):
            deps = poetry_block.get("dependencies")
            if isinstance(deps, dict):
                names.update(str(name).lower() for name in deps)

    requirements = root / "requirements.txt"
    if requirements.exists():
        try:
            lines = requirements.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            if line.strip() and not line.lstrip().startswith("-"):
                names.add(_split_requirement(line))

    names.discard("")
    return names


def _first_match(candidates: dict[str, str], present: set[str]) -> str | None:
    """Return the mapped value for the first candidate present, preserving dict order."""
    for package, label in candidates.items():
        if package in present:
            return label
    return None


def _detect_framework(
    found: dict[str, list[str]],
    node_deps: set[str],
    python_deps: set[str],
) -> str | None:
    # A config file is decisive when it exists, since it names the framework outright.
    if found["next"]:
        return "next"
    if found["vue"]:
        return "nuxt" if any("nuxt" in name for name in found["vue"]) else "vue"

    framework = _first_match(_NODE_FRAMEWORK_PACKAGES, node_deps)
    if framework is not None:
        return framework

    framework = _first_match(_PYTHON_FRAMEWORK_PACKAGES, python_deps)
    if framework is not None:
        return framework

    # Vite is a build tool, not a framework, so it is only reported when nothing
    # more specific was found.
    return "vite" if found["vite"] else None


def _detect_package_manager(root: Path, found: dict[str, list[str]]) -> str | None:
    for lockfile, manager in _LOCKFILE_MANAGERS:
        if (root / lockfile).exists():
            return manager
    if found["node"]:
        pkg = _read_json(root / "package.json")
        declared = pkg.get("packageManager")
        if isinstance(declared, str) and declared:
            return declared.split("@", 1)[0].strip().lower() or "npm"
        return "npm"
    return None


def _detect_test_runners(
    root: Path,
    types: list[str],
    node_deps: set[str],
    python_deps: set[str],
) -> list[str]:
    runners: list[str] = []

    if "node" in types:
        runners.extend(label for pkg, label in _NODE_TEST_PACKAGES.items() if pkg in node_deps)

    if "python" in types:
        runners.extend(label for pkg, label in _PYTHON_TEST_PACKAGES.items() if pkg in python_deps)
        # A pytest config section counts even when pytest is only an ambient dev tool.
        if "pytest" not in runners:
            pyproject = _read_toml(root / "pyproject.toml")
            tool = pyproject.get("tool")
            has_config = isinstance(tool, dict) and "pytest" in tool
            if has_config or (root / "pytest.ini").exists() or (root / "tests").is_dir():
                runners.append("pytest")

    if "rust" in types:
        runners.append("cargo-test")
    if "go" in types:
        runners.append("go-test")

    return runners


def detect_at(root: str | Path = ".") -> Detection:
    """Inspect a single directory. Does not descend into subdirectories."""
    path = Path(root)
    found: dict[str, list[str]] = {key: [] for key in SIGNALS}

    for tech_type, files in SIGNALS.items():
        for file in files:
            if (path / file).exists():
                found[tech_type].append(file)

    types_list = [name for name in ("node", "python", "rust", "go") if found[name]]

    node_deps = _node_dependency_names(path) if "node" in types_list else set()
    python_deps = _python_dependency_names(path) if "python" in types_list else set()

    deploy: str | None = next((name for name in _DEPLOY_PRIORITY if found[name]), None)

    return {
        "root": str(root),
        "types": types_list,
        "framework": _detect_framework(found, node_deps, python_deps),
        "deploy": deploy,
        "package_manager": _detect_package_manager(path, found),
        "test_runners": _detect_test_runners(path, types_list, node_deps, python_deps),
        "signals": found,
        "workspaces": [],
    }


def _ignore_spec(root: Path) -> pathspec.PathSpec[Any] | None:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return None
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _iter_candidate_dirs(root: Path, depth: int) -> list[Path]:
    """Breadth-first walk of subdirectories, honouring .gitignore and _ALWAYS_SKIP."""
    spec = _ignore_spec(root)
    results: list[Path] = []
    frontier = [root]

    for _ in range(depth):
        next_frontier: list[Path] = []
        for parent in frontier:
            try:
                entries = sorted(parent.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if entry.name in _ALWAYS_SKIP or entry.name.startswith("."):
                    continue
                if spec is not None:
                    relative = entry.relative_to(root).as_posix() + "/"
                    if spec.match_file(relative):
                        continue
                results.append(entry)
                next_frontier.append(entry)
        frontier = next_frontier

    return results


def scan(root: str = ".", depth: int = 2) -> Detection:
    """
    Scan a directory for technology indicators and detect project configuration.

    Nested projects are reported under ``workspaces`` so that monorepos are not
    reduced to whatever happens to sit at the top level. ``depth`` bounds how far
    the search descends; pass 0 to inspect only ``root``.
    """
    detection = detect_at(root)
    if depth <= 0:
        return detection

    base = Path(root)
    workspaces: list[Detection] = []
    for candidate in _iter_candidate_dirs(base, depth):
        nested = detect_at(candidate)
        if not nested["types"]:
            continue
        nested["root"] = candidate.relative_to(base).as_posix()
        workspaces.append(nested)

    detection["workspaces"] = workspaces
    return detection


def write_report(data: Detection, path: str = ".pipeline/detection.json") -> str:
    """
    Write detection results as a JSON report to the given path.

    Creates parent directories as needed and returns the output path.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
