"""The dependencies are pinned to the exact versions the code was tested with."""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
PINNED = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>\d+(\.\d+)*)$")


def declared() -> dict[str, list[str]]:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    return {"runtime": list(project["dependencies"]), **{f"extra {k}": list(v) for k, v in project.get("optional-dependencies", {}).items()}}


def test_every_dependency_is_pinned_to_one_exact_version() -> None:
    unpinned = [spec for group in declared().values() for spec in group if not PINNED.match(spec)]
    assert unpinned == []


def test_the_only_extra_is_the_development_one() -> None:
    # An optional extra that switched a protection off when absent would be a
    # way to run the server without it.
    assert [name for name in declared() if name.startswith("extra ")] == ["extra dev"]


def test_the_installed_versions_are_the_pinned_ones() -> None:
    for group in declared().values():
        for spec in group:
            match = PINNED.match(spec)
            assert match is not None
            assert metadata.version(match["name"]) == match["version"], spec


@pytest.mark.parametrize("spec", ["mcp>=1.25", "websockets", "pillow~=12.0", "pydantic==2.*", "pytest<10"])
def test_pin_check_catches_a_loose_specifier(spec: str) -> None:
    assert PINNED.match(spec) is None
