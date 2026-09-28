-- Project-private original concepts can be prepared before a reference video
-- has a complete Case. They are proposals, not adopted decision cards or
-- publication evidence. Revisions are append-only and project-scoped.

create table if not exists project_creative_concept (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  created_by text not null check (char_length(created_by) between 3 and 254 and created_by = lower(created_by)),
  created_at timestamptz not null default now(),
  unique (id, project_id)
);
create index if not exists idx_project_creative_concept_project_time
  on project_creative_concept(project_id, created_at desc, id);

create table if not exists project_creative_concept_revision (
  concept_id uuid not null,
  project_id uuid not null,
  version_no integer not null check (version_no between 1 and 10000),
  title text not null check (char_length(btrim(title)) between 1 and 160),
  premise text not null check (char_length(btrim(premise)) between 1 and 1200),
  character_choice text not null check (char_length(btrim(character_choice)) between 1 and 1200),
  episode_payoff text not null check (char_length(btrim(episode_payoff)) between 1 and 1200),
  evidence_note text not null check (char_length(btrim(evidence_note)) between 1 and 1200),
  test_question text not null check (char_length(btrim(test_question)) between 1 and 800),
  production_constraints text not null check (char_length(btrim(production_constraints)) between 1 and 1200),
  status text not null check (status in ('draft', 'ready_for_internal_test', 'withdrawn')),
  recorded_by text not null check (char_length(recorded_by) between 3 and 254 and recorded_by = lower(recorded_by)),
  recorded_at timestamptz not null default now(),
  primary key (concept_id, version_no),
  foreign key (concept_id, project_id)
    references project_creative_concept(id, project_id) on delete restrict
);
create index if not exists idx_project_creative_concept_revision_project
  on project_creative_concept_revision(project_id, concept_id, version_no desc);

create or replace function enforce_project_creative_concept_revision_sequence()
returns trigger language plpgsql as $$
declare
  preceding record;
begin
  -- Serialize every revision, including direct SQL writes, on the parent.
  perform 1 from project_creative_concept
   where id=new.concept_id and project_id=new.project_id for update;
  if not found then
    raise exception 'creative concept is unavailable in this project';
  end if;
  select version_no, status into preceding
    from project_creative_concept_revision
   where concept_id=new.concept_id order by version_no desc limit 1;
  if preceding.version_no is null then
    if new.version_no <> 1 then
      raise exception 'first creative concept revision must be v1';
    end if;
  elsif preceding.status='withdrawn' or new.version_no <> preceding.version_no+1 then
    raise exception 'creative concept revision is withdrawn or out of sequence';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_creative_concept_revision_sequence on project_creative_concept_revision;
create trigger trg_project_creative_concept_revision_sequence
before insert on project_creative_concept_revision
for each row execute function enforce_project_creative_concept_revision_sequence();

create or replace function reject_project_creative_concept_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'creative concept history is append-only';
end;
$$;
drop trigger if exists trg_project_creative_concept_immutable on project_creative_concept;
create trigger trg_project_creative_concept_immutable
before update or delete on project_creative_concept
for each row execute function reject_project_creative_concept_rewrite();
drop trigger if exists trg_project_creative_concept_revision_immutable on project_creative_concept_revision;
create trigger trg_project_creative_concept_revision_immutable
before update or delete on project_creative_concept_revision
for each row execute function reject_project_creative_concept_rewrite();

comment on table project_creative_concept_revision is
  'Original project-private proposal history. A ready revision only means prepared for internal low-fidelity testing; it is never an approved Case, adopted action, rights clearance, or publication.';
