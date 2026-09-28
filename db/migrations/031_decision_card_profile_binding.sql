-- An adopted action must say exactly which approved, rights-cleared subject
-- brief justified it.  The binding is a point-in-time evidence record: later
-- profile replacement or revocation does not silently rewrite the decision.
-- Existing cards remain legacy records and are not backfilled or invalidated.

alter table project_decision_card
  add column if not exists profile_binding_required_at timestamptz;

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'project_decision_card'::regclass
      and conname = 'uq_project_decision_card_subject_scope'
  ) then
    alter table project_decision_card
      add constraint uq_project_decision_card_subject_scope
      unique (id, project_id, subject_id);
  end if;
end $$;

create or replace function enforce_project_decision_card_profile_requirement()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.decision = 'adopt' then
      if new.subject_id is null then
        raise exception 'adopt project_decision_card requires a subject profile binding';
      end if;
      -- Never accept a caller-supplied historical cutover marker.
      new.profile_binding_required_at := clock_timestamp();
    elsif new.profile_binding_required_at is not null then
      raise exception 'only adopt project_decision_card may require a subject profile binding';
    end if;
  elsif old.profile_binding_required_at is not null then
    if new.decision <> 'adopt' or new.subject_id is null then
      raise exception 'bound adopt project_decision_card cannot remove its subject profile requirement';
    end if;
    if new.profile_binding_required_at is distinct from old.profile_binding_required_at then
      raise exception 'project_decision_card profile binding requirement is immutable';
    end if;
  elsif old.decision = 'adopt' then
    -- Pre-031 adopted cards are historical evidence.  Only their status and
    -- review may finish an old cycle; their source and action cannot change.
    if (new.project_id, new.source_video_id, new.subject_id, new.hypothesis,
        new.reference_point, new.adaptation_difference, new.owner_actor,
        new.decision, new.created_by) is distinct from
       (old.project_id, old.source_video_id, old.subject_id, old.hypothesis,
        old.reference_point, old.adaptation_difference, old.owner_actor,
        old.decision, old.created_by) then
      raise exception 'legacy adopt project_decision_card evidence is immutable';
    end if;
    if new.profile_binding_required_at is not null then
      raise exception 'legacy project_decision_card profile binding requirement cannot be forged';
    end if;
  elsif old.decision <> 'adopt' and new.decision = 'adopt' then
    if new.subject_id is null then
      raise exception 'adopt project_decision_card requires a subject profile binding';
    end if;
    new.profile_binding_required_at := clock_timestamp();
  elsif new.profile_binding_required_at is not null then
    raise exception 'legacy project_decision_card profile binding requirement cannot be forged';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_project_decision_card_profile_requirement on project_decision_card;
create trigger trg_project_decision_card_profile_requirement
before insert or update
on project_decision_card
for each row execute function enforce_project_decision_card_profile_requirement();

create table if not exists project_decision_card_profile_binding (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  subject_id uuid not null,
  profile_id uuid not null,
  profile_content_fingerprint text not null check (profile_content_fingerprint ~ '^[0-9a-f]{64}$'),
  profile_source_digest text not null check (profile_source_digest ~ '^[0-9a-f]{64}$'),
  profile_kind text not null check (profile_kind in (
    'ip_narrative', 'destination_experience', 'activity_conversion', 'other'
  )),
  profile_version_no integer not null check (profile_version_no > 0),
  rights_status_at_binding text not null check (rights_status_at_binding = 'cleared'),
  profile_approved_at timestamptz not null,
  bound_by text not null check (char_length(bound_by) between 3 and 254 and bound_by = lower(bound_by)),
  bound_at timestamptz not null default now(),
  unique (decision_card_id, project_id),
  foreign key (decision_card_id, project_id, subject_id)
    references project_decision_card(id, project_id, subject_id) on delete restrict,
  foreign key (profile_id, project_id)
    references research_subject_profile_version(id, project_id) on delete restrict
);
create index if not exists idx_project_decision_card_profile_binding_profile
  on project_decision_card_profile_binding(project_id, subject_id, profile_id);

create or replace function enforce_project_decision_card_profile_binding()
returns trigger language plpgsql as $$
declare
  card_row project_decision_card%rowtype;
  profile_row research_subject_profile_version%rowtype;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_profile_binding is immutable';
  end if;

  -- Lock both rows while deriving the snapshot.  An approval/revocation that
  -- races this insert either happens first (and rejects the bind) or waits
  -- until this exact approved-and-cleared evidence has been committed.
  select * into card_row
  from project_decision_card
  where id = new.decision_card_id and project_id = new.project_id
  for update;
  if not found then
    raise exception 'project_decision_card_profile_binding requires a local decision card';
  end if;
  if card_row.decision <> 'adopt'
     or card_row.profile_binding_required_at is null
     or card_row.subject_id is null
     or card_row.subject_id <> new.subject_id then
    raise exception 'project_decision_card_profile_binding requires a bound adopt decision card subject';
  end if;

  select * into profile_row
  from research_subject_profile_version
  where id = new.profile_id and project_id = new.project_id
  for update;
  if not found
     or profile_row.subject_id <> new.subject_id
     or profile_row.status <> 'approved'
     or profile_row.rights_status <> 'cleared'
     or profile_row.approved_at is null then
    raise exception 'project_decision_card_profile_binding requires an approved, rights-cleared local subject profile';
  end if;

  if not exists (
    select 1 from research_project_member member
    where member.project_id = new.project_id
      and member.actor_id = new.bound_by
      and member.effective_from <= now()
      and member.status = 'active'
      and member.role in ('owner', 'admin')
      and (member.effective_until is null or member.effective_until > now())
      and not exists (
        select 1 from research_project_member newer
        where newer.project_id = member.project_id
          and newer.actor_id = member.actor_id
          and newer.effective_from <= now()
          and newer.effective_from > member.effective_from
      )
  ) then
    raise exception 'project_decision_card_profile_binding bound_by must be an active project owner or admin';
  end if;

  -- All evidence fields are DB-derived; callers cannot manufacture a better
  -- rights state, a different fingerprint, or an earlier binding time.
  new.profile_content_fingerprint := profile_row.content_fingerprint;
  new.profile_source_digest := profile_row.source_digest;
  new.profile_kind := profile_row.profile_kind;
  new.profile_version_no := profile_row.version_no;
  new.rights_status_at_binding := profile_row.rights_status;
  new.profile_approved_at := profile_row.approved_at;
  new.bound_at := clock_timestamp();
  return new;
end;
$$;

drop trigger if exists trg_project_decision_card_profile_binding_immutable on project_decision_card_profile_binding;
create trigger trg_project_decision_card_profile_binding_immutable
before insert or update or delete on project_decision_card_profile_binding
for each row execute function enforce_project_decision_card_profile_binding();

create or replace function enforce_project_decision_card_adopt_profile_binding()
returns trigger language plpgsql as $$
begin
  -- NULL means a pre-031 card.  It remains readable historical evidence, but
  -- cannot become a new adopted action without passing the new gate.
  if new.profile_binding_required_at is not null and not exists (
    select 1 from project_decision_card_profile_binding binding
    where binding.project_id = new.project_id
      and binding.decision_card_id = new.id
      and binding.subject_id = new.subject_id
  ) then
    raise exception 'adopt project_decision_card requires an immutable approved subject profile binding';
  end if;
  return null;
end;
$$;

drop trigger if exists trg_project_decision_card_adopt_profile_binding on project_decision_card;
create constraint trigger trg_project_decision_card_adopt_profile_binding
after insert or update of decision, subject_id, profile_binding_required_at
on project_decision_card
deferrable initially deferred
for each row execute function enforce_project_decision_card_adopt_profile_binding();

comment on table project_decision_card_profile_binding is
  'Immutable point-in-time binding from a post-031 adopted action card to one approved, rights-cleared local subject profile version.';
