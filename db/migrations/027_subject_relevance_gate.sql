-- Subject-specific relevance is an interpretation layer over canonical public
-- evidence.  It never changes source_video/source_account or project
-- inclusion provenance, so a video may remain reusable by other projects.

create table if not exists research_subject_term (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  subject_id uuid not null,
  term text not null check (char_length(btrim(term)) between 1 and 160 and term = btrim(term)),
  term_type text not null check (term_type in ('alias', 'geographic_context', 'exclusion')),
  status text not null default 'active' check (status in ('active', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete cascade
);

create unique index if not exists uq_research_subject_term_active
  on research_subject_term(subject_id, term_type, lower(term)) where status='active';
create index if not exists idx_research_subject_term_project_subject
  on research_subject_term(project_id, subject_id) where status='active';

create table if not exists project_video_subject_relevance (
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  run_id uuid,
  decision text not null check (decision in ('pending', 'relevant', 'irrelevant')),
  decision_source text not null check (decision_source in ('rule', 'manual')),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  match_detail jsonb not null default '{}'::jsonb,
  reviewed_by text check (reviewed_by is null or char_length(reviewed_by) between 3 and 254),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, video_id, subject_id),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete cascade,
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete cascade,
  constraint fk_subject_relevance_run_project foreign key (run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  check ((decision_source = 'manual') = (reviewed_by is not null)),
  check ((decision_source = 'manual') = (reviewed_at is not null))
);

create index if not exists idx_project_video_subject_relevance_gate
  on project_video_subject_relevance(project_id, subject_id, decision, updated_at desc);

create table if not exists project_video_subject_relevance_audit (
  id bigserial primary key,
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  run_id uuid,
  event_type text not null check (event_type in ('rule_evaluated', 'manual_override')),
  actor text check (actor is null or char_length(actor) between 3 and 254),
  prior_decision text check (prior_decision is null or prior_decision in ('pending', 'relevant', 'irrelevant')),
  decision text not null check (decision in ('pending', 'relevant', 'irrelevant')),
  reason text check (reason is null or char_length(reason) between 1 and 500),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  match_detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  -- Historical audit intentionally has no FK to the mutable current-decision
  -- row. Project/video/subject removals must not erase a decision record.
  constraint fk_subject_relevance_audit_run_project foreign key (run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  check ((event_type = 'manual_override') = (actor is not null)),
  check ((event_type = 'manual_override') = (reason is not null))
);

create index if not exists idx_project_video_subject_relevance_audit_video
  on project_video_subject_relevance_audit(project_id, video_id, subject_id, created_at desc);

-- Keep replay idempotent for an installation that received an early 027 draft.
alter table project_video_subject_relevance add column if not exists run_id uuid;
alter table project_video_subject_relevance_audit add column if not exists run_id uuid;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_video_subject_relevance'::regclass and conname='fk_subject_relevance_run_project') then
    alter table project_video_subject_relevance add constraint fk_subject_relevance_run_project
      foreign key (run_id, project_id) references pipeline_run(id, project_id) on delete restrict;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='project_video_subject_relevance_audit'::regclass and conname='fk_subject_relevance_audit_run_project') then
    alter table project_video_subject_relevance_audit add constraint fk_subject_relevance_audit_run_project
      foreign key (run_id, project_id) references pipeline_run(id, project_id) on delete restrict;
  end if;
end $$;
-- Early 027 drafts referenced the mutable relevance row. PostgreSQL may have
-- truncated the generated FK name, so discover exactly that relationship from
-- the catalog instead of guessing a literal constraint name.
do $$
declare legacy_constraint text;
begin
  for legacy_constraint in
    select constraint_row.conname
    from pg_constraint constraint_row
    join pg_class audit_table on audit_table.oid=constraint_row.conrelid
    join pg_namespace audit_schema on audit_schema.oid=audit_table.relnamespace
    join pg_class referenced_table on referenced_table.oid=constraint_row.confrelid
    where constraint_row.contype='f'
      and audit_schema.nspname=current_schema()
      and audit_table.relname='project_video_subject_relevance_audit'
      and referenced_table.relname='project_video_subject_relevance'
  loop
    execute format('alter table project_video_subject_relevance_audit drop constraint %I', legacy_constraint);
  end loop;
end $$;

alter table research_brief add column if not exists subject_id uuid;
alter table research_brief add column if not exists subject_gate_status text not null default 'not_applicable';
alter table research_brief drop constraint if exists research_brief_subject_gate_status_check;
alter table research_brief add constraint research_brief_subject_gate_status_check
  check (subject_gate_status in ('not_applicable', 'ready', 'subject_required'));
do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid='research_brief'::regclass and conname='fk_research_brief_subject_project'
  ) then
    alter table research_brief add constraint fk_research_brief_subject_project
      foreign key (subject_id, project_id)
      references research_subject(id, project_id) on delete restrict;
  end if;
end $$;
alter table research_brief drop constraint if exists research_brief_subject_scope_check;
alter table research_brief add constraint research_brief_subject_scope_check
  check (subject_id is null or project_id is not null);
-- A pre-027 project brief has no safe subject interpretation.  Pause an active
-- one explicitly rather than leaving it active and silently unclaimable.
update research_brief
set subject_gate_status = case
      when project_id is null then 'not_applicable'
      when subject_id is null then 'subject_required'
      else 'ready'
    end,
    status = case
      when project_id is not null and subject_id is null and status='active' then 'paused'
      else status
    end,
    next_due_at = case
      when project_id is not null and subject_id is null and status='active' then null
      else next_due_at
    end,
    updated_at = case
      when project_id is not null and subject_id is null and status='active' then now()
      else updated_at
    end;
create index if not exists idx_research_brief_project_subject_due
  on research_brief(project_id, subject_id, next_due_at, id)
  where project_id is not null and subject_id is not null and status='active';

comment on table project_video_subject_relevance is
  'Per-project, per-subject relevance gate. Pending/irrelevant candidates retain canonical evidence but do not enter subject L1 scoring.';
