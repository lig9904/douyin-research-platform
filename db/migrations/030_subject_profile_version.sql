-- A profile is a short, rights-aware subject brief.  It is project-local:
-- public evidence may be shared, but the research interpretation must never
-- be attached to another project or subject by an application-side mistake.

create table if not exists research_subject_profile_version (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  subject_id uuid not null,
  profile_kind text not null check (profile_kind in (
    'ip_narrative', 'destination_experience', 'activity_conversion', 'other'
  )),
  version_no integer not null check (version_no > 0),
  status text not null default 'draft' check (status in ('draft', 'approved', 'superseded', 'revoked')),
  summary jsonb not null check (
    jsonb_typeof(summary) = 'object'
    and summary <> '{}'::jsonb
    and summary - array[
      'target_audience', 'shootable_scenes', 'narrative_constraints',
      'forbidden_expressions', 'current_facts'
    ]::text[] = '{}'::jsonb
    and pg_column_size(summary) <= 8192
  ),
  rights_status text not null check (rights_status in (
    'unknown', 'pending', 'cleared', 'restricted', 'prohibited'
  )),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  source_digest text not null check (source_digest ~ '^[0-9a-f]{64}$'),
  content_fingerprint text not null check (content_fingerprint ~ '^[0-9a-f]{64}$'),
  approved_by text check (approved_by is null or char_length(approved_by) between 3 and 254),
  approved_at timestamptz,
  superseded_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  unique (project_id, subject_id, profile_kind, version_no),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (status not in ('approved', 'superseded') or (approved_by is not null and approved_at is not null)),
  check ((status = 'superseded') = (superseded_at is not null)),
  check ((status = 'revoked') = (revoked_at is not null))
);

create unique index if not exists uq_research_subject_profile_one_approved
  on research_subject_profile_version(project_id, subject_id)
  where status = 'approved';
create index if not exists idx_research_subject_profile_subject_kind_version
  on research_subject_profile_version(project_id, subject_id, profile_kind, version_no desc);

create or replace function enforce_research_subject_profile_version()
returns trigger language plpgsql as $$
declare
  computed_fingerprint text;
begin
  computed_fingerprint := encode(digest(
    new.profile_kind || E'\n' || new.summary::text || E'\n' || new.rights_status
      || E'\n' || new.source_reference || E'\n' || new.source_digest,
    'sha256'
  ), 'hex');

  if tg_op = 'INSERT' then
    if new.status <> 'draft' then
      raise exception 'research_subject_profile_version must begin as draft';
    end if;
    if new.approved_by is not null or new.approved_at is not null
       or new.superseded_at is not null or new.revoked_at is not null then
      raise exception 'draft research_subject_profile_version cannot carry lifecycle timestamps';
    end if;
  else
    if old.status = 'draft' then
      if new.status not in ('draft', 'approved', 'revoked') then
        raise exception 'draft research_subject_profile_version may only become approved or revoked';
      end if;
    elsif old.status = 'approved' then
      if new.status not in ('superseded', 'revoked') then
        raise exception 'approved research_subject_profile_version may only become superseded or revoked';
      end if;
    else
      raise exception 'superseded or revoked research_subject_profile_version is immutable';
    end if;

    if old.status <> 'draft' and (
      new.project_id, new.subject_id, new.profile_kind, new.version_no,
      new.summary, new.rights_status, new.source_reference, new.source_digest,
      new.content_fingerprint, new.approved_by, new.approved_at
    ) is distinct from (
      old.project_id, old.subject_id, old.profile_kind, old.version_no,
      old.summary, old.rights_status, old.source_reference, old.source_digest,
      old.content_fingerprint, old.approved_by, old.approved_at
    ) then
      raise exception 'approved research_subject_profile_version content is immutable';
    end if;
  end if;

  new.content_fingerprint := computed_fingerprint;
  if new.status = 'approved' then
    if new.approved_by is null then
      raise exception 'approved research_subject_profile_version requires approved_by';
    end if;
    new.approved_at := coalesce(new.approved_at, clock_timestamp());
  elsif tg_op = 'INSERT' or old.status = 'draft' then
    new.approved_by := null;
    new.approved_at := null;
  end if;
  if new.status = 'superseded' then
    new.superseded_at := coalesce(new.superseded_at, clock_timestamp());
    new.revoked_at := null;
  elsif new.status = 'revoked' then
    new.revoked_at := coalesce(new.revoked_at, clock_timestamp());
    new.superseded_at := null;
  else
    new.superseded_at := null;
    new.revoked_at := null;
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;

drop trigger if exists trg_research_subject_profile_version_lifecycle on research_subject_profile_version;
create trigger trg_research_subject_profile_version_lifecycle
before insert or update on research_subject_profile_version
for each row execute function enforce_research_subject_profile_version();

create or replace function reject_research_subject_profile_version_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'research_subject_profile_version must be revoked, not deleted';
end;
$$;
drop trigger if exists trg_research_subject_profile_version_no_delete on research_subject_profile_version;
create trigger trg_research_subject_profile_version_no_delete
before delete on research_subject_profile_version
for each row execute function reject_research_subject_profile_version_delete();

comment on table research_subject_profile_version is
  'Versioned, project-local short subject brief. Approved content and its deterministic fingerprint are immutable; later changes require a new draft.';
