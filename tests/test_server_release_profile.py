from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-release.sh"


def _section(source: str, name: str) -> str:
    start = source.index(f"{name}()")
    if source.startswith(f"{name}() {{", start):
        end = source.find("\n}\n", start)
        return source[start:end + 2] if end >= 0 else source[start:source.index("\n", start)]
    if source.startswith(f"{name}() (", start):
        return source[start:source.index("\n)\n\ncase ", start) + 2]
    return source[start:source.index("\n", start)]


def test_release_gate_requires_reviewed_030_profile_shape_and_ownership() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    contract = _section(source, "verify_subject_profile_contract")
    for marker in (
        "research_subject_profile_version",
        "uq_research_subject_profile_one_approved",
        "idx_research_subject_profile_subject_kind_version",
        "030.project_fk_restrict",
        "030.subject_fk_restrict",
        "ON DELETE RESTRICT",
        "trg_research_subject_profile_version_lifecycle",
        "trg_research_subject_profile_version_no_delete",
        "030.owner_grants",
        "030.no_nonowner_grants",
        "has_table_privilege(current_user",
        "grant_row.grantee<>relation_row.relowner",
    ):
        assert marker in contract
    assert "subject profile migration contract verification failed" in contract
    assert "exit 1" in contract
    assert "verify_subject_profile_contract" in _section(source, "cmd_migrate")
    assert "verify_subject_profile_contract" in _section(source, "cmd_verify")


def test_restore_drill_accepts_029_prefix_and_verifies_030_profile_counts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    restore = _section(source, "cmd_restore_drill")
    assert "'9|43')" in restore
    prefix_start = restore.index("'9|43')")
    prefix_end = restore.index(";;", prefix_start)
    assert 'verify_migration_ledger prefix "$RESTORE_DATABASE"' in restore[prefix_start:prefix_end]
    assert "'10|48')" in restore
    current_start = restore.index("'10|48')")
    current_end = restore.index(";;", current_start)
    current = restore[current_start:current_end]
    assert 'verify_migration_ledger required "$RESTORE_DATABASE"' in current
    assert "verify_subject_profile_contract" in current
    assert "archive_profile_count" in current
    assert "research_subject_profile_version" in current
    assert "subject_profile_contract=present" in current
    assert "subject_profile_contract=%s" in restore
    assert "030_subject_profile_version.sql" in restore


def test_release_script_syntax_is_valid_without_docker() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def _archive_counter() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("archive_profile_count() {")
    marker = "python3 -c '"
    code_start = source.index(marker, start) + len(marker)
    code_end = source.index("\n'\n}", code_start)
    return source[code_start:code_end]


def test_profile_archive_counter_counts_only_profile_copy_and_fails_closed() -> None:
    code = _archive_counter()
    valid = (
        "COPY public.source_video (id) FROM stdin;\nignored\n\\.\n"
        "COPY public.research_subject_profile_version (id) FROM stdin;\nprofile-a\nprofile-b\n\\.\n"
    )
    result = subprocess.run(["python3", "-c", code], input=valid, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "2\n"
    missing = subprocess.run(["python3", "-c", code], input="COPY public.source_video (id) FROM stdin;\n\\.\n", text=True, capture_output=True, check=False)
    assert missing.returncode != 0
    duplicate = subprocess.run(
        ["python3", "-c", code],
        input=valid + "COPY public.research_subject_profile_version (id) FROM stdin;\n\\.\n",
        text=True, capture_output=True, check=False,
    )
    assert duplicate.returncode != 0
