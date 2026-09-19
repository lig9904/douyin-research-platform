-- 011_l3_execution_control.sql
-- One-shot L3 model execution state without persisting the evidence bundle.

create table if not exists l3_execution_job (
  id uuid primary key,
  task_key text not null unique,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  model_id text not null,
  model_revision text not null,
  prompt_version text not null,
  schema_version text not null,
  input_fingerprint text not null,
  status text not null check (
    status in ('running', 'completed', 'failed')
  ),
  attempt_count integer not null default 0
    check (attempt_count between 0 and 1),
  estimated_llm_cost numeric(14,6)
    check (estimated_llm_cost >= 0),
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

create index if not exists idx_l3_execution_video_time
  on l3_execution_job(video_id, created_at desc);
