"""Tests for AEGIS version and project metadata."""

from __future__ import annotations

import sys

import aegis


def test_python_version_is_3_11_plus() -> None:
    """AC-01.01: Python 3.11+ is used by the project."""
    version = sys.version_info
    assert version.major == 3
    assert version.minor >= 11


def test_project_version() -> None:
    """AC-01.01: Project version matches V1.3."""
    assert aegis.__version__ == "1.3.0"
