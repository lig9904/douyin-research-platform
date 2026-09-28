-- A subject-scoped L1 score is an immutable project interpretation of
-- canonical public evidence. It must never be written to video_score or
-- source_video, which remain global monitoring state.

create table if not exists project_video_subject_score (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  source_run_id uuid not null,
  score_type text not null default 'priority'
    check (score_type = 'priority'),
  score numeric(5,2) not null check (score >= 0 and score <= 100),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  components jsonb not null default '{}'::jsonb
    check (jsonb_typeof(components) = 'object'),
  created_at timestamptz not null default now(),
  unique (project_id, video_id, subject_id, source_run_id, score_type),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  foreign key (source_run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict
);
create index if not exists idx_project_video_subject_score_project_subject_score
  on project_video_subject_score(project_id, subject_id, score desc, created_at desc);
create index if not exists idx_project_video_subject_score_project_video
  on project_video_subject_score(project_id, video_id, created_at desc);

-- The Python scorer locks the current relevance row before inserting. Keep
-- the same rule below the application boundary: direct SQL cannot create an
-- actionable project score for a pending, excluded, or archived subject, a
-- rejected/archived project inclusion, or a video absent from the source run.
create or replace function enforce_project_video_subject_score_eligible()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1
    from project_video_subject_relevance relevance
    join project_video_inclusion inclusion
      on inclusion.project_id=relevance.project_id and inclusion.video_id=relevance.video_id
    join research_subject subject
      on subject.id=relevance.subject_id and subject.project_id=relevance.project_id
    join source_video video on video.id=relevance.video_id
    where relevance.project_id=new.project_id
      and relevance.video_id=new.video_id
      and relevance.subject_id=new.subject_id
      and relevance.decision='relevant'
      and inclusion.status in ('candidate', 'shortlisted', 'accepted')
      and subject.status='active'
      and video.availability_status='available'
      and exists (
        select 1 from pipeline_run_item item
        where item.run_id=new.source_run_id
          and item.entity_type='video'
          and item.entity_id=new.video_id
      )
  ) then
    raise exception 'project_video_subject_score requires active relevant subject evidence';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_video_subject_score_eligible on project_video_subject_score;
create trigger trg_project_video_subject_score_eligible
before insert on project_video_subject_score
for each row execute function enforce_project_video_subject_score_eligible();

-- Scores are evidence of one deterministic run. Re-running creates a new
-- run-scoped record; old scores are not rewritten to match later opinions.
create or replace function reject_project_video_subject_score_change()
returns trigger language plpgsql as $$
begin
  raise exception 'project_video_subject_score is immutable';
end;
$$;
drop trigger if exists trg_project_video_subject_score_immutable on project_video_subject_score;
create trigger trg_project_video_subject_score_immutable
before update or delete on project_video_subject_score
for each row execute function reject_project_video_subject_score_change();

comment on table project_video_subject_score is
  'Run-scoped deterministic L1 score for a locally included, currently relevant project subject. It never changes global monitoring state.';
