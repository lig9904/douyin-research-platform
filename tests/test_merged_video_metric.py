import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg import sql

DSN = os.getenv('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN, reason='TEST_DATABASE_URL not configured')


def test_merged_fields_keep_zero_null_precedence_and_provenance():
    video = uuid4()
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        # Roll back the entire fixture: no unrelated test data is deleted.
        try:
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)", (video,str(video)))
            def add(endpoint, play, likes, followers, hours):
                conn.execute("""insert into metric_snapshot(video_id,provider,source_endpoint,
                  play_count,like_count,author_follower_count,captured_at)
                  values (%s,'test',%s,%s,%s,%s,now()-(%s * interval '1 hour'))""",
                  (video,endpoint,play,likes,followers,hours))
            def read():
                return conn.execute('select * from merged_video_metric where video_id=%s',(video,)).fetchone()
            add('douyin.app.multi_video_v2', 12, 90, 4, 3)
            assert read()['play_count'] == 12  # no billboard fallback
            add('douyin.billboard.low_fan', 0, 100, 0, 2)
            add('douyin.app.multi_video_v2', 50, 0, 9, 1)
            row = read()
            assert (row['play_count'],row['author_follower_count'],row['like_count']) == (0,0,0)
            assert row['comment_count'] is None
            assert row['metric_provenance']['play_count']['source_kind'] == 'billboard'
            assert row['metric_provenance']['like_count']['source_kind'] == 'detail'
            assert row['oldest_field_captured_at'] < row['captured_at']
            add('unrecognized-private-endpoint', None, 3, None, 0)
            row = read()
            assert row['like_count'] == 3  # latest, not maximum
            assert row['play_count'] == 0
            assert row['metric_provenance']['like_count']['source_kind'] == 'other'
            assert 'unrecognized-private-endpoint' not in str(row)
            add('douyin.app.video_statistics', 120, 5, None, 0)
            row = read()
            assert row['play_count'] == 120  # verified statistics outrank older billboard zero
            assert row['author_follower_count'] == 0  # other fields retain their precedence
            assert row['metric_provenance']['play_count']['source_kind'] == 'statistics'
        finally:
            conn.rollback()


def test_039_upgrades_old_view_and_replays_without_rewriting_snapshots():
    assert DSN
    root = Path(__file__).resolve().parents[1]
    namespace = f"statistics_merge_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as conn:
                conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
                conn.execute((root / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
                conn.execute((root / "db/migrations/018_merged_video_metric.sql").read_text(encoding="utf-8"), prepare=False)
                video = conn.execute(
                    """insert into source_video(platform,platform_video_id)
                       values ('douyin','7658347686323555610') returning id"""
                ).fetchone()["id"]
                conn.execute(
                    """insert into metric_snapshot(video_id,provider,source_endpoint,play_count)
                       values (%s,'tikhub','douyin.billboard.low_fan',0),
                              (%s,'tikhub','douyin.app.video_statistics',120)""",
                    (video, video),
                )
                assert conn.execute(
                    "select play_count from merged_video_metric where video_id=%s", (video,)
                ).fetchone()["play_count"] == 0
                migration = (root / "db/migrations/039_video_statistics_precedence.sql").read_text(encoding="utf-8")
                conn.execute(migration, prepare=False)
                conn.execute(migration, prepare=False)
                row = conn.execute(
                    """select play_count,metric_provenance from merged_video_metric
                       where video_id=%s""", (video,),
                ).fetchone()
                assert row["play_count"] == 120
                assert row["metric_provenance"]["play_count"]["source_kind"] == "statistics"
                assert conn.execute("select count(*) from metric_snapshot").fetchone()["count"] == 2
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
