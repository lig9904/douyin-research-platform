import json
from pathlib import Path
import tomllib

import douyin_research
from douyin_research.mcp import server


def test_public_package_and_mcp_versions_match_project_metadata() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == douyin_research.__version__
    assert project["version"] == server._SERVER_VERSION


def test_dashboard_package_and_lock_versions_match_release_candidate() -> None:
    dashboard = Path("windmill/f/content_research/research_dashboard.raw_app")
    package = json.loads((dashboard / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((dashboard / "package-lock.json").read_text(encoding="utf-8"))
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    npm_version = project["version"].replace("rc", "-rc.")

    assert package["version"] == npm_version
    assert package_lock["version"] == npm_version
    assert package_lock["packages"][""]["version"] == npm_version
