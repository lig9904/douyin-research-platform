-- Project ownership for new research control-plane records and candidate
-- inclusions.  Legacy rows deliberately remain project_id NULL: canonical
-- source_video/source_account are shared observations, never project-owned.

alter table research_brief
  add column if not exists project_id uuid references research_project(id) on delete restrict;
-- The prior owner/name index was global.  Keep that exact rule for legacy
-- rows, but make project-owned task names project-local.
drop index if exists uq_research_brief_owner_name;
create unique index if not exists uq_research_brief_owner_name
  on research_brief(owner_actor, lower(name))
  where status <> 'archived' and project_id is null;
create unique index if not exists uq_research_brief_project_name
  on research_brief(project_id, lower(name))
  where status <> 'archived' and project_id is not null;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'research_brief'::regclass and conname = 'uq_research_brief_id_project') then
    alter table research_brief add constraint uq_research_brief_id_project unique (id, project_id);
  end if;
end $$;
create index if not exists idx_research_brief_project_due
  on research_brief(project_id, next_due_at, id)
  where project_id is not null and status = 'active';

alter table pipeline_run
  add column if not exists project_id uuid references research_project(id) on delete restrict;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'pipeline_run'::regclass and conname = 'uq_pipeline_run_id_project') then
    alter table pipeline_run add constraint uq_pipeline_run_id_project unique (id, project_id);
  end if;
end $$;
create index if not exists idx_pipeline_run_project_time
  on pipeline_run(project_id, started_at desc)
  where project_id is not null;

alter table research_brief_run
  add column if not exists project_id uuid references research_project(id) on delete restrict;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'research_brief_run'::regclass and conname = 'uq_research_brief_run_id_project') then
    alter table research_brief_run add constraint uq_research_brief_run_id_project unique (id, project_id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'research_brief_run'::regclass and conname = 'fk_research_brief_run_project_brief') then
    alter table research_brief_run add constraint fk_research_brief_run_project_brief
      foreign key (brief_id, project_id) references research_brief(id, project_id) on delete cascade;
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'research_brief_run'::regclass and conname = 'fk_research_brief_run_project_source_run') then
    alter table research_brief_run add constraint fk_research_brief_run_project_source_run
      foreign key (source_run_id, project_id) references pipeline_run(id, project_id) on delete set null (source_run_id);
  end if;
end $$;
create index if not exists idx_research_brief_run_project_time
  on research_brief_run(project_id, started_at desc)
  where project_id is not null;

-- Nullable composite FKs use MATCH SIMPLE.  These checks therefore enforce
-- equality even when a legacy project_id is NULL, while retaining NULL/NULL.
create or replace function enforce_research_brief_run_project_scope()
returns trigger language plpgsql as $$
declare
  brief_project_id uuid;
  source_project_id uuid;
begin
  select project_id into brief_project_id from research_brief where id = new.brief_id;
  if brief_project_id is distinct from new.project_id then
    raise exception 'research_brief_run project_id must match research_brief project_id';
  end if;
  if new.source_run_id is not null then
    select project_id into source_project_id from pipeline_run where id = new.source_run_id;
    if source_project_id is distinct from new.project_id then
      raise exception 'research_brief_run project_id must match source pipeline_run project_id';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_research_brief_run_project_scope on research_brief_run;
create trigger trg_research_brief_run_project_scope
before insert or update of brief_id, source_run_id, project_id on research_brief_run
for each row execute function enforce_research_brief_run_project_scope();

-- Project scope is a creation-time security boundary.  Reassignment must be a
-- separately reviewed copy operation, never an UPDATE that repurposes history.
create or replace function enforce_project_id_immutable()
returns trigger language plpgsql as $$
begin
  if old.project_id is distinct from new.project_id then
    raise exception '% project_id is immutable after insert', tg_table_name;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_research_brief_project_id_immutable on research_brief;
create trigger trg_research_brief_project_id_immutable
before update of project_id on research_brief
for each row execute function enforce_project_id_immutable();
drop trigger if exists trg_pipeline_run_project_id_immutable on pipeline_run;
create trigger trg_pipeline_run_project_id_immutable
before update of project_id on pipeline_run
for each row execute function enforce_project_id_immutable();
drop trigger if exists trg_research_brief_run_project_id_immutable on research_brief_run;
create trigger trg_research_brief_run_project_id_immutable
before update of project_id on research_brief_run
for each row execute function enforce_project_id_immutable();

-- This is the sole project-specific candidate state.  The public video stays
-- canonical and can be deliberately included in several projects.
create table if not exists project_video_inclusion (
  project_id uuid not null references research_project(id) on delete cascade,
  video_id uuid not null references source_video(id) on delete restrict,
  source_run_id uuid,
  brief_run_id uuid,
  source_type text not null default 'pipeline_run'
    check (source_type in ('pipeline_run', 'research_brief_run', 'manual', 'import')),
  source_ref text check (source_ref is null or char_length(source_ref) between 1 and 512),
  status text not null default 'candidate'
    check (status in ('candidate', 'shortlisted', 'accepted', 'rejected', 'archived')),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, video_id),
  foreign key (source_run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  foreign key (brief_run_id, project_id)
    references research_brief_run(id, project_id) on delete restrict,
  check (last_seen_at >= first_seen_at),
  check (
    (source_type = 'pipeline_run' and source_run_id is not null) or
    (source_type = 'research_brief_run' and brief_run_id is not null) or
    (source_type in ('manual', 'import') and source_run_id is null and brief_run_id is null)
  )
);

create index if not exists idx_project_video_inclusion_project_status_seen
  on project_video_inclusion(project_id, status, last_seen_at desc);
create index if not exists idx_project_video_inclusion_video
  on project_video_inclusion(video_id, project_id);

drop trigger if exists trg_project_video_inclusion_project_id_immutable on project_video_inclusion;
create trigger trg_project_video_inclusion_project_id_immutable
before update of project_id on project_video_inclusion
for each row execute function enforce_project_id_immutable();

comment on table project_video_inclusion is
  'Project-scoped candidate state and provenance only. source_video remains canonical and is never backfilled or project-owned.';
