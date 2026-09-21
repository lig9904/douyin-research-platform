import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_emitter_has_fixed_target_read_only_and_shared_queries():
    spec = importlib.util.spec_from_file_location('emitter', ROOT / 'scripts/test-server-restored-business-sql.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module.build_sql()
    assert sql.startswith('begin transaction isolation level repeatable read read only;')
    assert "current_database() <> 'test_server_research_restore'" in sql
    assert 'raise exception' in sql
    assert "'v1_release_accepted', false" in sql
    assert sql.rstrip().endswith('rollback;')
    assert 'invalid_associations' in sql


def test_release_requires_business_audit_before_cleanup_and_success():
    source = (ROOT / 'scripts/test-server-release.sh').read_text()
    body = source[source.index('cmd_restore_drill()'):]
    assert body.index('test-server-restored-business-sql.py') < body.index('\n  cleanup\n')
    assert body.index('restored business association audit did not pass') < body.index('RESTORE_DRILL_VALID')
    assert 'research_query "$RESTORE_DATABASE" "$business_sql"' in body
