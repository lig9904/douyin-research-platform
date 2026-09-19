-- 009_asr_execution_control.sql
-- Submit-once ASR job state without storing media references in plaintext.

alter table daily_budget
  add column if not exists cost_currency text not null default 'USD';

create table if not exists asr_execution_job (
  id uuid primary key,
  task_key text not null unique,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  model_id text not null,
  model_revision text not null,
  engine_version text not null,
  source_fingerprint text not null,
  media_ref_fingerprint text not null,
  status text not null check (
    status in ('submitting', 'submitted', 'running', 'completed', 'failed')
  ),
  provider_task_ref text,
  submission_count integer not null default 0
    check (submission_count between 0 and 1),
  poll_count integer not null default 0 check (poll_count >= 0),
  estimated_api_cost numeric(14,6) check (estimated_api_cost >= 0),
  estimated_asr_cost numeric(14,6) check (estimated_asr_cost >= 0),
  cost_currency text not null,
  budget_date date not null,
  budget_key text not null,
  task_cost_id uuid unique
    references research_task_cost(id) on delete set null,
  error_code text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_asr_execution_video_time
  on asr_execution_job(video_id, created_at desc);
