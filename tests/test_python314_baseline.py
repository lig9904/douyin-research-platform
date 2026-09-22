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


def test_all_windmill_python_script_metadata_reference_their_lock() -> None:
    root = ROOT / "windmill"
    metadata_files = list((root / "f/content_research").rglob("*.script.yaml"))
    assert metadata_files
    for metadata in metadata_files:
        stem = str(metadata)[: -len(".script.yaml")]
        script = Path(f"{stem}.py")
        lock = Path(f"{stem}.script.lock")
        if not script.exists():
            continue
        assert lock.is_file() and lock.stat().st_size > 0, metadata
        relative_lock = lock.relative_to(root).as_posix()
        source = metadata.read_text(encoding="utf-8")
        assert f"lock: '!inline {relative_lock}'" in source, metadata
