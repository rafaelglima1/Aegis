"""Tests for AEGIS version and project metadata."""

from __future__ import annotations

import sys

import pytest

import aegis


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="Python 3.12+ required in production Docker image; local dev may use 3.11",
)
def test_python_version_is_3_12_plus() -> None:
    """AC-C9-20: Python 3.12+ is used by the project."""
    version = sys.version_info
    assert version.major == 3
    assert version.minor >= 12


def test_project_version() -> None:
    """AC-01.01: Project version matches V1.3."""
    assert aegis.__version__ == "1.3.0"
