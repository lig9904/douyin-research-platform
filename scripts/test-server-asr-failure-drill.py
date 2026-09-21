"""Run only the synthetic SIGKILL cases in a separately prepared empty database.

Does not create/drop databases, stop workers, or access supplier credentials.
The operator must explicitly authorize provisioning and cleanup separately.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

DATABASE = "test_server_asr_failure_drill"
ROOT = Path(__file__).resolve().parents[1]


def checked_dsn(raw: str) -> str:
    values = conninfo_to_dict(raw)
    if values.get("dbname") != DATABASE or values.get("service"):
        raise ValueError("dedicated drill database is required")
    # Tests use unqualified table names; never inherit a different search path.
    return make_conninfo(raw, options="-c search_path=public", connect_timeout=10)


def preflight(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        if conn.execute("select current_database()").fetchone() != (DATABASE,):
            raise ValueError("database identity mismatch")
        for table in ("source_video", "research_task_cost", "daily_budget"):
            # Fixed names only: these are exactly the tables the fixture clears.
            if conn.execute(f"select count(*) from public.{table}").fetchone() != (0,):
                raise ValueError("drill database must start empty")
        for table in ("asr_execution_job", "transcript"):
            if conn.execute("select to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
                raise ValueError("drill schema is incomplete")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-isolated", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_isolated:
        print("ISOLATED_DRILL_EXECUTION_SWITCH_REQUIRED", file=sys.stderr)
        return 2
    if os.name != "posix":
        print("ISOLATED_DRILL_REQUIRES_POSIX", file=sys.stderr)
        return 2
    try:
        dsn = checked_dsn(os.environ.get("TEST_DATABASE_URL", ""))
        preflight(dsn)
    except Exception:
        # Neither connection strings nor libpq exception messages enter logs.
        print("ISOLATED_DRILL_PREFLIGHT_FAILED", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = dsn
    env["PYTEST_ADDOPTS"] = ""
    with tempfile.TemporaryDirectory(prefix="asr-drill-report-") as directory:
        report = Path(directory) / "results.xml"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_asr_execution.py",
             "-k", "test_sigkill_subprocess_preserves_no_resubmit_boundary", "-v", "--tb=short",
             f"--junitxml={report}"],
            cwd=ROOT, env=env, check=False,
        )
        try:
            cases = ET.parse(report).getroot().findall(".//testcase")
            passed = len(cases) == 2 and all(
                "test_sigkill_subprocess_preserves_no_resubmit_boundary" in case.attrib.get("name", "")
                and not any(case.find(tag) is not None for tag in ("skipped", "failure", "error"))
                for case in cases
            )
        except (OSError, ET.ParseError):
            passed = False
    if result.returncode != 0 or not passed:
        print("ISOLATED_DRILL_TESTS_FAILED", file=sys.stderr)
        return 1
    print("ISOLATED_ASR_PROCESS_DRILL_PASSED: synthetic provider; not scheduler recovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
