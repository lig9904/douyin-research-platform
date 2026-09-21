"""Exercise shared SQL predicates with session-local PostgreSQL rows only."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


def test_real_pg_chain_requires_audio_l3_and_matching_task_fingerprints():
    dsn = os.getenv('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('isolated TEST_DATABASE_URL not configured')
    path = Path(__file__).resolve().parents[1] / 'scripts/test-server-restored-business-audit.py'
    spec = importlib.util.spec_from_file_location('audit_chain', path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    with psycopg.connect(dsn) as connection:
        try:
            # Derive real column types, but no production rows or constraints.
            # Unqualified shared SQL resolves these session-local tables.
            for table in sorted(audit.REQUIRED_TABLES):
                connection.execute(psycopg.sql.SQL('create temp table {} as select * from public.{} with no data').format(
                    psycopg.sql.Identifier(table), psycopg.sql.Identifier(table)))
            video, asset, ac, lc = [uuid4() for _ in range(4)]
            def insert(table, **values):
                connection.execute(psycopg.sql.SQL('insert into {} ({}) values ({})').format(
                    psycopg.sql.Identifier(table),
                    psycopg.sql.SQL(',').join(map(psycopg.sql.Identifier, values)),
                    psycopg.sql.SQL(',').join(psycopg.sql.Placeholder() for _ in values)), list(values.values()))
            insert('source_video', id=video)
            insert('media_asset', id=asset, video_id=video, kind='audio', content_sha256='a'*64)
            insert('asr_media_review', asset_id=asset, asset_fingerprint='e'*64, active=True, identity_source='windmill_end_user_email_allowlist_v1')
            insert('transcript', video_id=video, task_cost_id=ac, source_fingerprint='a'*64)
            insert('research_task_cost', id=ac, video_id=video, task_key='asr', task_type='asr_transcription', status='completed', input_fingerprint='a'*64)
            insert('research_task_cost', id=lc, video_id=video, task_key='l3', task_type='l3_structured_research', status='completed', input_fingerprint='b'*64)
            insert('asr_execution_job', video_id=video, task_cost_id=ac, task_key='asr', status='completed', source_fingerprint='a'*64, media_ref_fingerprint='e'*64)
            insert('analysis_run', video_id=video, task_cost_id=lc, analysis_level='L3', analysis_type='l3_structured_research', status='completed', input_fingerprint='b'*64)
            insert('l3_execution_job', video_id=video, task_cost_id=lc, task_key='l3', status='completed', input_fingerprint='b'*64)
            def count():
                return connection.execute(audit.FULL_CHAIN_SQL).fetchone()[0]
            assert count() == 1
            for table, column, wrong in [('media_asset','kind','image'), ('analysis_run','analysis_level','L1'),
                                         ('l3_execution_job','task_key','wrong'), ('l3_execution_job','input_fingerprint','c'*64),
                                         ('asr_execution_job','source_fingerprint','d'*64),
                                         ('asr_execution_job','media_ref_fingerprint','f'*64)]:
                connection.execute('savepoint negative_case')
                connection.execute(psycopg.sql.SQL('update {} set {}=%s').format(psycopg.sql.Identifier(table),psycopg.sql.Identifier(column)), (wrong,))
                assert count() == 0, (table, column)
                connection.execute('rollback to savepoint negative_case')
                assert count() == 1
        finally:
            connection.rollback()
