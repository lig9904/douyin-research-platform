from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sync_preserves_local_only_secrets_and_requires_reviewable_tty() -> None:
    source = (ROOT / "scripts/local-windmill-sync.sh").read_text(encoding="utf-8")

    assert '[[ -t 0 && -t 1 ]]' in source
    assert "--skip-secrets --keep-deleted" in source
    assert "sync push --workspace" in source
    assert "sync push --yes" not in source
    assert "--token" not in source


def test_local_reviewer_bootstrap_repairs_role_privilege_drift() -> None:
    bootstrap = (ROOT / "db/init/02-bootstrap-l3-local-reviewer.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts/local-l3-env.sh").read_text(encoding="utf-8")

    assert "NOINHERIT NOBYPASSRLS" in bootstrap
    assert "FROM pg_auth_members membership" in bootstrap
    assert "REVOKE %I FROM %I" in bootstrap
    assert 'role_row" == "f|f|0"' in verifier
