import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-windmill-preflight.sh"


def _write_mock_wmill(directory: Path, responses: dict[str, object]) -> Path:
    mock = directory / "wmill"
    mock.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["WMILL_CALL_LOG"]).open("a").write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
kind = args[args.index("--workspace") + 2]
if args[-2:] != ["list", "--json"]:
    raise SystemExit(17)
print(json.dumps(json.loads(os.environ["WMILL_RESPONSES"])[kind]))
""",
        encoding="utf-8",
    )
    mock.chmod(0o755)
    return mock


def _run(tmp_path: Path, metadata: dict[str, object], responses: dict[str, object]):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_mock_wmill(bin_dir, responses)
    profile = tmp_path / "profile"
    profile.mkdir()
    metadata_path = tmp_path / "variables.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WMILL_CALL_LOG": str(log),
        "WMILL_RESPONSES": json.dumps(responses),
    }
    result = subprocess.run(
        [
            str(SCRIPT),
            "--workspace", "test-research",
            "--profile", str(profile),
            "--app", "f/content_research/research_dashboard",
            "--script", "f/content_research/analysis/manual_l3_preview",
            "--resource", "f/content_research/research_db",
            "--secret-variable", "f/content_research/research_db_password",
            "--nonsecret-variable", "f/content_research/l3_privacy_reviewers",
            "--variable-metadata", str(metadata_path),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return result, calls


def test_preflight_uses_only_read_only_lists_and_never_prints_variable_values(tmp_path: Path) -> None:
    responses = {
        "app": [{"path": "f/content_research/research_dashboard"}],
        "script": [{"path": "f/content_research/analysis/manual_l3_preview"}],
        "resource": [{"path": "f/content_research/research_db"}],
    }
    metadata = {
        "variables": [
            {"path": "f/content_research/research_db_password", "is_secret": True},
            {"path": "f/content_research/l3_privacy_reviewers", "is_secret": False},
        ]
    }

    result, calls = _run(tmp_path, metadata, responses)

    assert result.returncode == 0, result.stderr
    assert "READY secret-variable path=f/content_research/research_db_password is_secret=true" in result.stdout
    assert "READY nonsecret-variable path=f/content_research/l3_privacy_reviewers is_secret=false" in result.stdout
    assert len(calls) == 3
    assert {call[-3] for call in calls} == {"app", "script", "resource"}
    for call in calls:
        assert "get" not in call
        assert "sync" not in call
        assert "push" not in call
        assert "variable" not in call
        assert "--workspace" in call
        assert call[-2:] == ["list", "--json"]


def test_preflight_reports_missing_remote_object_without_variable_value_output(tmp_path: Path) -> None:
    sentinel = "DO_NOT_PRINT_A_SECRET_VALUE"
    responses = {
        "app": [{"path": "f/content_research/research_dashboard"}],
        "script": [],
        "resource": [{"path": "f/content_research/research_db"}],
    }
    metadata = {
        "variables": [
            {"path": "f/content_research/research_db_password", "is_secret": True},
            {"path": "f/content_research/l3_privacy_reviewers", "is_secret": False},
        ],
        "value": sentinel,
    }

    result, _ = _run(tmp_path, metadata, responses)

    assert result.returncode == 1
    assert "MISSING script path=f/content_research/analysis/manual_l3_preview" in result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr


def test_preflight_rejects_value_bearing_metadata_without_echoing_it(tmp_path: Path) -> None:
    sentinel = "DO_NOT_PRINT_A_SECRET_VALUE"
    responses = {
        "app": [{"path": "f/content_research/research_dashboard"}],
        "script": [{"path": "f/content_research/analysis/manual_l3_preview"}],
        "resource": [{"path": "f/content_research/research_db"}],
    }
    metadata = {
        "variables": [
            {
                "path": "f/content_research/research_db_password",
                "is_secret": True,
                "value": sentinel,
            },
            {"path": "f/content_research/l3_privacy_reviewers", "is_secret": False},
        ]
    }

    result, _ = _run(tmp_path, metadata, responses)

    assert result.returncode == 1
    assert "ERROR: variable metadata contains non-metadata fields" in result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr


def test_preflight_source_has_no_secret_variable_cli_read_or_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"$kind" list --json' in source
    assert "wmill variable" not in source
    assert " sync " not in source
    assert " push " not in source
    assert "--token" not in source
