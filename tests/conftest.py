from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ProjectBuilder = Callable[..., Path]


@pytest.fixture
def make_project(tmp_path: Path) -> ProjectBuilder:
    """
    Build a throwaway project tree.

    Files are given as a {relative path: content} mapping; dict and list values are
    written as JSON so tests can describe a package.json inline.
    """

    def _build(files: dict[str, Any], name: str = "project") -> Path:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, (dict, list)):
                target.write_text(json.dumps(content), encoding="utf-8")
            else:
                target.write_text(str(content), encoding="utf-8")
        return root

    return _build


@pytest.fixture
def node_package() -> dict[str, Any]:
    return {"name": "app", "dependencies": {}, "devDependencies": {}}
