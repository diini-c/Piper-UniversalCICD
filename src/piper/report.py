from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import Console
from rich.table import Table

__all__ = ["pretty", "print_plan"]


def _join(values: Any) -> str:
    if isinstance(values, list) and values:
        return ", ".join(str(value) for value in values)
    return "-"


def pretty(detection: Mapping[str, Any], console: Console | None = None) -> None:
    """
    Display detection results as a table.

    Args:
        detection: the mapping returned by ``scan()``.
        console: optional Console, injected by tests to capture output.
    """
    console = console or Console()

    table = Table(title="Piper Detection Report")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Types", _join(detection.get("types")))
    table.add_row("Framework", str(detection.get("framework") or "-"))
    table.add_row("Deploy", str(detection.get("deploy") or "-"))
    table.add_row("Package manager", str(detection.get("package_manager") or "-"))
    table.add_row("Test runners", _join(detection.get("test_runners")))

    signals = detection.get("signals") or {}
    matched = sorted(name for name, files in signals.items() if files)
    table.add_row("Signals", _join(matched))

    console.print(table)

    workspaces = detection.get("workspaces") or []
    if workspaces:
        nested = Table(title="Detected Workspaces")
        nested.add_column("Path", style="bold")
        nested.add_column("Types")
        nested.add_column("Framework")
        for workspace in workspaces:
            nested.add_row(
                str(workspace.get("root", "?")),
                _join(workspace.get("types")),
                str(workspace.get("framework") or "-"),
            )
        console.print(nested)


def print_plan(title: str, plan: list[list[str]], console: Console | None = None) -> None:
    """Show the commands piper intends to run, one per line."""
    console = console or Console()
    if not plan:
        console.print(f"[yellow]{title}: nothing to do for this project.[/yellow]")
        return

    console.print(f"[bold]{title}[/bold]")
    for command in plan:
        console.print(f"  $ {' '.join(command)}")
