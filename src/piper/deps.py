from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import CommandFailedError, MissingToolError

__all__ = ["run", "plan_install", "install_for", "plan_tests", "run_tests"]

# Shown alongside "tool not found" so the message is actionable rather than just a complaint.
_INSTALL_HINTS: dict[str, str] = {
    "npm": "install Node.js from https://nodejs.org",
    "pnpm": "run `corepack enable pnpm`",
    "yarn": "run `corepack enable`",
    "cargo": "install Rust from https://rustup.rs",
    "go": "install Go from https://go.dev/dl",
}


def run(cmd: list[str], cwd: str | None = None, dry_run: bool = False) -> None:
    """
    Execute a command, converting the two common failure modes into PiperErrors.

    Without the PATH check a missing package manager surfaces as a bare
    FileNotFoundError traceback, which tells the user nothing about what to install.
    """
    if dry_run:
        return

    executable = cmd[0]
    # sys.executable is an absolute path to the running interpreter, so it needs no lookup.
    if executable != sys.executable and shutil.which(executable) is None:
        raise MissingToolError(executable, _INSTALL_HINTS.get(executable))

    completed = subprocess.run(cmd, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise CommandFailedError(cmd, completed.returncode)


def _node_install_command(root: Path, package_manager: str | None) -> list[str] | None:
    """Prefer a reproducible frozen-lockfile install; fall back to a plain install."""
    if (root / "pnpm-lock.yaml").exists():
        return ["pnpm", "install", "--frozen-lockfile"]
    if (root / "yarn.lock").exists():
        return ["yarn", "install", "--frozen-lockfile"]
    if (root / "package-lock.json").exists():
        return ["npm", "ci"]
    if (root / "package.json").exists():
        return [package_manager or "npm", "install"]
    return None


def plan_install(detection: Mapping[str, Any], cwd: str = ".") -> list[list[str]]:
    """
    Build the list of commands that ``install_for`` would run.

    Keeping planning separate from execution means the interesting logic is testable
    without spawning a single subprocess, and lets the CLI show a --dry-run preview.
    """
    types = detection.get("types") or []
    root = Path(cwd)
    plan: list[list[str]] = []

    if "python" in types:
        if (root / "requirements.txt").exists():
            plan.append(
                [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
            )
            plan.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            plan.append([sys.executable, "-m", "pip", "install", "-e", "."])

    if "node" in types:
        command = _node_install_command(root, detection.get("package_manager"))
        if command is not None:
            plan.append(command)

    if "rust" in types and (root / "Cargo.toml").exists():
        plan.append(["cargo", "fetch"])

    if "go" in types and (root / "go.mod").exists():
        plan.append(["go", "mod", "download"])

    return plan


def install_for(
    detection: Mapping[str, Any], cwd: str = ".", dry_run: bool = False
) -> list[list[str]]:
    """Install dependencies for every detected technology. Returns the commands used."""
    plan = plan_install(detection, cwd)
    for command in plan:
        run(command, cwd=cwd, dry_run=dry_run)
    return plan


def plan_tests(detection: Mapping[str, Any], cwd: str = ".") -> list[list[str]]:
    """Build the list of test commands implied by the detected test runners."""
    runners = detection.get("test_runners") or []
    package_manager = detection.get("package_manager") or "npm"
    root = Path(cwd)
    plan: list[list[str]] = []

    if "pytest" in runners:
        plan.append([sys.executable, "-m", "pytest"])

    for runner in ("vitest", "jest", "mocha"):
        if runner in runners:
            # `npm test` respects whatever the project wired up in its scripts block.
            plan.append([package_manager, "test"] if package_manager != "npm" else ["npm", "test"])
            break

    if "playwright" in runners:
        plan.append(["npx", "playwright", "test"])

    if "cargo-test" in runners and (root / "Cargo.toml").exists():
        plan.append(["cargo", "test"])

    if "go-test" in runners and (root / "go.mod").exists():
        plan.append(["go", "test", "./..."])

    return plan


def run_tests(
    detection: Mapping[str, Any], cwd: str = ".", dry_run: bool = False
) -> list[list[str]]:
    """Run every detected test suite. Returns the commands used."""
    plan = plan_tests(detection, cwd)
    for command in plan:
        run(command, cwd=cwd, dry_run=dry_run)
    return plan
