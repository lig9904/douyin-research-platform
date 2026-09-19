-- 010_l3_result_boundary.sql
-- Link versioned L3 outputs to their per-task cost record.

alter table analysis_run
  add column if not exists model_revision text;

alter table analysis_run
  add column if not exists input_fingerprint text;

alter table analysis_run
  add column if not exists output_fingerprint text;

alter table analysis_run
  add column if not exists task_cost_id uuid
    references research_task_cost(id) on delete cascade;

create unique index if not exists uq_analysis_run_task_cost
  on analysis_run(task_cost_id)
  where task_cost_id is not null;

create unique index if not exists uq_l3_analysis_version_input
  on analysis_run(
    video_id,
    analysis_type,
    coalesce(model, ''),
    coalesce(model_revision, ''),
    coalesce(prompt_version, ''),
    coalesce(schema_version, ''),
    coalesce(input_fingerprint, '')
  )
  where analysis_level='L3' and status='completed';
