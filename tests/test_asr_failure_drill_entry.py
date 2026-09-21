import importlib.util
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

spec = importlib.util.spec_from_file_location("asr_drill", Path(__file__).resolve().parents[1] / "scripts/test-server-asr-failure-drill.py")
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


@pytest.mark.parametrize("dsn", ["", "dbname=douyin_research", "dbname=windmill", "dbname=test_server_research_restore", "dbname=test_server_asr_failure_drill service=unexpected"])
def test_rejects_any_non_dedicated_database(dsn):
    with pytest.raises(ValueError):
        drill.checked_dsn(dsn)


def test_forces_public_search_path_and_bounded_connect():
    values = conninfo_to_dict(drill.checked_dsn("dbname=test_server_asr_failure_drill options='-c search_path=other'"))
    assert values["options"] == "-c search_path=public"
    assert values["connect_timeout"] == "10"


def test_missing_switch_never_connects(monkeypatch):
    monkeypatch.setattr(drill, "preflight", lambda _: pytest.fail("must not connect"))
    assert drill.main([]) == 2


def test_preflight_error_is_sanitized_and_never_starts_tests(monkeypatch, capsys):
    monkeypatch.setenv("TEST_DATABASE_URL", "dbname=douyin_research password=do-not-print")
    monkeypatch.setattr(drill.subprocess, "run", lambda *a, **k: pytest.fail("must not run tests"))
    assert drill.main(["--execute-isolated"]) == 2
    output = capsys.readouterr()
    assert "do-not-print" not in output.err
    assert "ISOLATED_DRILL_PREFLIGHT_FAILED" in output.err


@pytest.mark.parametrize("actual_database,occupied", [("douyin_research", False), (drill.DATABASE, True)])
def test_actual_database_and_empty_tables_are_checked(monkeypatch, actual_database, occupied):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, *args):
            self.value = (actual_database,) if "current_database" in sql else (int(occupied),)
            return self
        def fetchone(self): return self.value
    monkeypatch.setattr(drill.psycopg, "connect", lambda _: Connection())
    with pytest.raises(ValueError):
        drill.preflight("unused")


@pytest.mark.parametrize("child_tag,expected", [("", 0), ("<skipped/>", 1), ("<failure/>", 1)])
def test_zero_exit_requires_two_non_skipped_cases(monkeypatch, child_tag, expected):
    from types import SimpleNamespace
    monkeypatch.setenv("TEST_DATABASE_URL", f"dbname={drill.DATABASE}")
    monkeypatch.setattr(drill, "preflight", lambda _: None)
    def run(argv, **kwargs):
        report = next(x.split("=", 1)[1] for x in argv if x.startswith("--junitxml="))
        prefix = "test_sigkill_subprocess_preserves_no_resubmit_boundary"
        Path(report).write_text(f'<testsuites><testsuite><testcase name="{prefix}[before_save]">{child_tag}</testcase><testcase name="{prefix}[during_poll]"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(drill.subprocess, "run", run)
    assert drill.main(["--execute-isolated"]) == expected
