from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_test_runner_is_python_314() -> None:
    assert sys.version_info[:2] == (3, 14)


def test_project_and_ci_pin_python_314() -> None:
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14.7"
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.14,<3.15"' in project
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        if "actions/setup-python" in source:
            assert 'python-version: "3.14"' in source, workflow


def test_all_windmill_python_and_lock_files_select_python_314() -> None:
    root = ROOT / "windmill/f/content_research"
    scripts = list(root.rglob("*.py"))
    locks = list(root.rglob("*.lock"))
    assert scripts
    assert locks
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert (
            '# py: ==3.14.*' in source[:200]
            or '# requires-python = "==3.14.*"' in source[:300]
        ), script
    for lock in locks:
        source = lock.read_text(encoding="utf-8")
        assert "# py: 3.14" in source[:200], lock
