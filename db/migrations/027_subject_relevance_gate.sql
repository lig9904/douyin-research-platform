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
  event_type text not null check (event_type in ('rule_evaluated', 'manual_override')),
  actor text check (actor is null or char_length(actor) between 3 and 254),
  prior_decision text check (prior_decision is null or prior_decision in ('pending', 'relevant', 'irrelevant')),
  decision text not null check (decision in ('pending', 'relevant', 'irrelevant')),
  reason text check (reason is null or char_length(reason) between 1 and 500),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  match_detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (project_id, video_id, subject_id)
    references project_video_subject_relevance(project_id, video_id, subject_id) on delete cascade,
  check ((event_type = 'manual_override') = (actor is not null)),
  check ((event_type = 'manual_override') = (reason is not null))
);

create index if not exists idx_project_video_subject_relevance_audit_video
  on project_video_subject_relevance_audit(project_id, video_id, subject_id, created_at desc);

alter table research_brief add column if not exists subject_id uuid;
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
create index if not exists idx_research_brief_project_subject_due
  on research_brief(project_id, subject_id, next_due_at, id)
  where project_id is not null and subject_id is not null and status='active';

comment on table project_video_subject_relevance is
  'Per-project, per-subject relevance gate. Pending/irrelevant candidates retain canonical evidence but do not enter subject L1 scoring.';
