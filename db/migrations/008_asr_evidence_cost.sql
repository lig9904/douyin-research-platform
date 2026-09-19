-- 008_asr_evidence_cost.sql
-- Versioned ASR evidence with nullable per-task component costs.

create table if not exists research_task_cost (
  id uuid primary key default gen_random_uuid(),
  task_key text not null unique,
  task_type text not null,
  task_version text not null,
  pipeline_run_id uuid references pipeline_run(id) on delete set null,
  video_id uuid references source_video(id) on delete cascade,
  status text not null
    check (status in ('completed', 'failed', 'cancelled')),
  input_fingerprint text not null,
  output_fingerprint text,
  api_cost numeric(14,6) check (api_cost >= 0),
  asr_cost numeric(14,6) check (asr_cost >= 0),
  llm_cost numeric(14,6) check (llm_cost >= 0),
  total_cost numeric(14,6) generated always as (
    case
      when api_cost is null or asr_cost is null or llm_cost is null
        then null
      else api_cost + asr_cost + llm_cost
    end
  ) stored,
  cost_currency text not null,
  cost_basis text not null
    check (cost_basis in ('actual', 'estimated', 'mixed', 'unknown')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_research_task_cost_video_time
  on research_task_cost(video_id, created_at desc);

alter table transcript
  add column if not exists task_cost_id uuid
    references research_task_cost(id) on delete restrict;

create unique index if not exists uq_transcript_task_cost
  on transcript(task_cost_id)
  where task_cost_id is not null;

drop index if exists uq_transcript_version;
create unique index uq_transcript_version
  on transcript(
    video_id,
    asr_provider,
    coalesce(model_id, ''),
    coalesce(model_revision, ''),
    coalesce(engine_version, ''),
    coalesce(language, ''),
    coalesce(hotword_version, ''),
    coalesce(source_provider, ''),
    coalesce(source_fingerprint, '')
  );
