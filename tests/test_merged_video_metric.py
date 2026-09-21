import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

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
        finally:
            conn.rollback()
