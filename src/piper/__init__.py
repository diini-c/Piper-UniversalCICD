from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("piper")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0.dev0"
