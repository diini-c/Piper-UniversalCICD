from __future__ import annotations

__all__ = ["PiperError", "MissingToolError", "CommandFailedError"]


class PiperError(Exception):
    """Base class for errors that should reach the user as a message, not a traceback."""


class MissingToolError(PiperError):
    """A required executable is not on PATH."""

    def __init__(self, tool: str, hint: str | None = None) -> None:
        message = f"required tool {tool!r} was not found on PATH"
        if hint:
            message = f"{message} ({hint})"
        super().__init__(message)
        self.tool = tool
        self.hint = hint


class CommandFailedError(PiperError):
    """A subprocess exited with a non-zero status."""

    def __init__(self, cmd: list[str], returncode: int) -> None:
        super().__init__(f"command failed with exit code {returncode}: {' '.join(cmd)}")
        self.cmd = cmd
        self.returncode = returncode
