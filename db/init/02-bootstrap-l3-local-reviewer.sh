#!/usr/bin/env bash
# Creates the local-only account used by the host-side reviewer page.
# The script is safe to run repeatedly. Docker executes it automatically only
# for a new PostgreSQL volume; scripts/local-l3-env.sh provision reruns it for
# an existing isolated volume.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${RESEARCH_DB_NAME:?RESEARCH_DB_NAME is required}"
: "${RESEARCH_DB_USER:?RESEARCH_DB_USER is required}"
: "${L3_LOCAL_REVIEWER_USER:?L3_LOCAL_REVIEWER_USER is required}"
: "${L3_LOCAL_REVIEWER_PASSWORD:?L3_LOCAL_REVIEWER_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=postgres_db="$POSTGRES_DB" \
  --set=postgres_user="$POSTGRES_USER" \
  --set=research_db="$RESEARCH_DB_NAME" \
  --set=research_user="$RESEARCH_DB_USER" \
  --set=reviewer_user="$L3_LOCAL_REVIEWER_USER" \
  --set=reviewer_password="$L3_LOCAL_REVIEWER_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'reviewer_user', :'reviewer_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reviewer_user')
\gexec

SELECT format(
  'ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS',
  :'reviewer_user',
  :'reviewer_password'
)
\gexec

-- A reused local volume may contain stale memberships from an earlier manual
-- experiment.  NOINHERIT alone is insufficient because a member can SET ROLE;
-- remove every membership before restoring the direct-grant allowlist below.
SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = :'reviewer_user'
\gexec

SELECT format('ALTER ROLE %I SET default_transaction_read_only = on', :'reviewer_user')
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', :'postgres_db')
\gexec

SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'postgres_db', :'postgres_user')
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', :'research_db')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'research_db', :'research_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'research_db', :'reviewer_user')
\gexec
EOSQL

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$RESEARCH_DB_NAME" \
  --set=reviewer_user="$L3_LOCAL_REVIEWER_USER" <<'EOSQL'
SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', :'reviewer_user')
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', :'reviewer_user')
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'reviewer_user')
\gexec

SELECT format(
  'GRANT SELECT ON TABLE public.source_video, public.source_account, public.research_promotion_decision, public.video_comment_feature_snapshot, public.transcript, public.metric_snapshot, public.merged_video_metric, public.video_score, public.discovery_event, public.analysis_run, public.research_task_cost, public.external_api_call, public.supplier_daily_spend, public.daily_budget, public.research_brief, public.research_brief_run TO %I',
  :'reviewer_user'
)
\gexec
EOSQL
