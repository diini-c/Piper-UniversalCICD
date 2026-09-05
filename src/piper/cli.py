from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import click

from .deps import install_for, plan_install, plan_tests, run_tests
from .detect import scan as do_scan
from .detect import write_report
from .errors import PiperError
from .generate import PROVIDERS, default_output_path, generate, render
from .report import pretty, print_plan

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(func: F) -> F:
    """Turn PiperError into a clean CLI message instead of a traceback."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except PiperError as exc:
            raise click.ClickException(str(exc)) from exc

    return cast(F, wrapper)


@click.group()
@click.version_option(package_name="piper")
def main() -> None:
    """Piper: detect a project's stack and build the pipeline that fits it."""


@main.command()
@click.option("--root", default=".", help="Root directory to scan")
@click.option("--report", default=".pipeline/detection.json", help="JSON report output path")
@click.option(
    "--depth", default=2, show_default=True, help="How deep to search for nested projects"
)
@handle_errors
def scan(root: str, report: str, depth: int) -> None:
    """Scan a directory and write a detection report."""
    detection = do_scan(root, depth=depth)
    write_report(detection, report)
    pretty(detection)
    click.echo(f"\nReport written to {report}")


@main.command()
@click.option("--root", default=".", help="Project directory")
@click.option("--dry-run", is_flag=True, help="Show the commands without running them")
@handle_errors
def install(root: str, dry_run: bool) -> None:
    """Install dependencies for every detected technology."""
    detection = do_scan(root, depth=0)
    if dry_run:
        print_plan("Would run", plan_install(detection, root))
        return
    executed = install_for(detection, root)
    if not executed:
        click.echo("Nothing to install for this project.")


@main.command("test")
@click.option("--root", default=".", help="Project directory")
@click.option("--dry-run", is_flag=True, help="Show the commands without running them")
@handle_errors
def test_command(root: str, dry_run: bool) -> None:
    """Run the test suites piper detected."""
    detection = do_scan(root, depth=0)
    if dry_run:
        print_plan("Would run", plan_tests(detection, root))
        return
    executed = run_tests(detection, root)
    if not executed:
        click.echo("No test runner detected for this project.")


@main.command("generate")
@click.option("--root", default=".", help="Project directory")
@click.option(
    "--provider",
    default="github",
    type=click.Choice(sorted(PROVIDERS)),
    show_default=True,
    help="CI provider to generate for",
)
@click.option("--out", default=None, help="Output path (defaults to the provider's location)")
@click.option("--name", "workflow_name", default="CI", show_default=True, help="Workflow name")
@click.option("--branch", default="main", show_default=True, help="Default branch to trigger on")
@click.option(
    "--stdout", "to_stdout", is_flag=True, help="Print the pipeline instead of writing it"
)
@click.option(
    "--check",
    is_flag=True,
    help="Exit non-zero if the file on disk differs from what piper would generate",
)
@handle_errors
def generate_command(
    root: str,
    provider: str,
    out: str | None,
    workflow_name: str,
    branch: str,
    to_stdout: bool,
    check: bool,
) -> None:
    """Generate a CI pipeline that matches the detected stack."""
    detection = do_scan(root, depth=0)

    if to_stdout:
        click.echo(render(detection, provider, root, workflow_name, branch), nl=False)
        return

    target = Path(root) / (out or default_output_path(provider))

    if check:
        expected = render(detection, provider, root, workflow_name, branch)
        actual = target.read_text(encoding="utf-8") if target.exists() else None
        if actual == expected:
            click.echo(f"{target} is up to date.")
            return
        reason = "does not exist" if actual is None else "is out of date"
        raise click.ClickException(f"{target} {reason}. Run `piper generate` to refresh it.")

    path, _ = generate(detection, provider, root, out, workflow_name, branch)
    click.echo(f"Pipeline written to {path}")
