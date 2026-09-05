from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from piper.report import pretty, print_plan

FULL: dict[str, Any] = {
    "root": ".",
    "types": ["node", "python"],
    "framework": "next",
    "deploy": "vercel",
    "package_manager": "pnpm",
    "test_runners": ["vitest", "pytest"],
    "signals": {"node": ["package.json"], "python": ["pyproject.toml"], "rust": []},
    "workspaces": [],
}


def _render(detection: dict[str, Any]) -> str:
    # A wide console keeps rich from wrapping values mid-word and breaking assertions.
    buffer = io.StringIO()
    pretty(detection, console=Console(file=buffer, width=200, no_color=True))
    return buffer.getvalue()


class TestPretty:
    def test_shows_every_field(self) -> None:
        output = _render(FULL)
        for expected in ("next", "vercel", "pnpm", "vitest", "pytest", "node", "python"):
            assert expected in output

    def test_lists_only_matched_signals(self) -> None:
        output = _render(FULL)
        assert "rust" not in output

    def test_missing_values_render_as_dash(self) -> None:
        empty: dict[str, Any] = {
            "types": [],
            "framework": None,
            "deploy": None,
            "package_manager": None,
            "test_runners": [],
            "signals": {},
            "workspaces": [],
        }
        assert "-" in _render(empty)

    def test_absent_keys_do_not_raise(self) -> None:
        # pretty() is handed raw JSON from older reports, so it must tolerate gaps.
        assert _render({}) != ""

    def test_workspace_table_appears_when_present(self) -> None:
        detection = {
            **FULL,
            "workspaces": [
                {"root": "apps/web", "types": ["node"], "framework": "react"},
                {"root": "services/api", "types": ["python"], "framework": "fastapi"},
            ],
        }
        output = _render(detection)
        assert "Detected Workspaces" in output
        assert "apps/web" in output
        assert "services/api" in output

    def test_workspace_table_hidden_when_empty(self) -> None:
        assert "Detected Workspaces" not in _render(FULL)


class TestPrintPlan:
    def test_lists_each_command(self) -> None:
        buffer = io.StringIO()
        console = Console(file=buffer, width=200, no_color=True)
        print_plan("Would run", [["npm", "ci"], ["npm", "test"]], console=console)
        output = buffer.getvalue()
        assert "$ npm ci" in output
        assert "$ npm test" in output

    def test_empty_plan_says_nothing_to_do(self) -> None:
        buffer = io.StringIO()
        console = Console(file=buffer, width=200, no_color=True)
        print_plan("Would run", [], console=console)
        assert "nothing to do" in buffer.getvalue().lower()
