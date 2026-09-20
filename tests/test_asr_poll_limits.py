import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from douyin_research.l2 import asr_execution as execution

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL required")


@pytest.fixture
def persisted(monkeypatch):
    video, job = uuid4(), uuid4()
    key = "isolated-poll-limit-" + str(job)
    monkeypatch.setattr(execution, "ASR_BUDGET_KEY", key)
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)", (video, str(video)))
        conn.execute("""insert into asr_execution_job(id,task_key,video_id,provider,model_id,model_revision,
            engine_version,source_fingerprint,media_ref_fingerprint,status,provider_task_ref,submission_count,
            cost_currency,budget_date,budget_key,metadata)
            values (%s,%s,%s,'isolated','test','test','test','test','test','running','test-ref',1,
            'CNY',current_date,%s,%s)""", (job, str(job), video, key,
            Jsonb(dict(poll_limit=72, poll_max_age_seconds=86400))))
        conn.execute("""insert into daily_budget(budget_date,provider,budget_key,cost_currency)
            values (current_date,'isolated',%s,'CNY')""", (key,))
    request = SimpleNamespace(budget_date=date.today(), provider="isolated", cost_currency="CNY", trigger_source="schedule")
    yield execution.ASRExecutionCoordinator(DSN), request, job, key
    with psycopg.connect(DSN) as conn:
        conn.execute("delete from source_video where id=%s", (video,))
        conn.execute("delete from daily_budget where budget_key=%s", (key,))


@pytest.mark.parametrize("update", [
    "poll_count=72", "created_at=now()-interval '25 hours'",
    "poll_count=72,metadata='{}'::jsonb",  # Pre-policy jobs use the same safe defaults.
    "poll_count=2,metadata=metadata || '{\"poll_limit\":2}'::jsonb",
])
def test_limit_or_expiry_stops_before_budget_reservation(persisted, update):
    coordinator, request, job, key = persisted
    with psycopg.connect(DSN) as conn:
        conn.execute("update asr_execution_job set " + update + " where id=%s", (job,))
    with pytest.raises(RuntimeError, match="polling limit reached"):
        coordinator._reserve_poll(request, job)
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select used_requests from daily_budget where budget_key=%s", (key,)).fetchone() == (0,)
        assert conn.execute("select status,submission_count,provider_task_ref,error_code from asr_execution_job where id=%s", (job,)).fetchone() == ("running", 1, "test-ref", "poll_limit_reached")


def test_two_concurrent_reservations_cannot_cross_total_limit(persisted):
    coordinator, request, job, key = persisted
    with psycopg.connect(DSN) as conn:
        conn.execute("update asr_execution_job set poll_count=71 where id=%s", (job,))
    def reserve(_):
        try:
            coordinator._reserve_poll(request, job)
            return "reserved"
        except RuntimeError as error:
            assert "polling limit reached" in str(error)
            return "limited"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == ["limited", "reserved"]
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select poll_count from asr_execution_job where id=%s", (job,)).fetchone() == (72,)
        assert conn.execute("select used_requests from daily_budget where budget_key=%s", (key,)).fetchone() == (1,)
