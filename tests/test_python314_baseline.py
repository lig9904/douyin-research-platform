from __future__ import annotations

import re
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
        assert re.search(
            rf"(?m)^lock: ['\"]!inline\s+{re.escape(relative_lock)}['\"]",
            source,
        ), metadata


def test_raw_app_python_backends_have_generated_dependency_and_workspace_locks() -> None:
    """Keep every inline Raw App backend deployable from a reproducible lock.

    Raw App backends are not ``*.script.yaml`` objects, so the generic script
    metadata check above deliberately cannot see them.  They still execute
    Python in Windmill and therefore need both the generated per-backend
    dependency lock and the generated workspace hash reference.  In
    particular, this covers private-project ASR/L3 endpoints added in 032.
    """
    raw_app = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
    backend_dir = raw_app / "backend"
    backends = sorted(backend_dir.glob("*.py"))
    assert backends

    workspace_lock = (ROOT / "windmill/wmill-lock.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"^  f/content_research/research_dashboard\.raw_app\+__app_hash: [0-9a-f]{64}$",
        workspace_lock,
        flags=re.MULTILINE,
    )

    for backend in backends:
        dependency_lock = backend.with_suffix(".lock")
        assert dependency_lock.is_file() and dependency_lock.stat().st_size > 0, backend
        assert "# py: 3.14" in dependency_lock.read_text(encoding="utf-8")[:200], dependency_lock

        workspace_key = (
            "f/content_research/research_dashboard.raw_app+" + backend.name
        )
        assert re.search(
            rf"^  {re.escape(workspace_key)}: [0-9a-f]{{64}}$",
            workspace_lock,
            flags=re.MULTILINE,
        ), backend
