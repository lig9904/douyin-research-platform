-- A project-owned, human-operated loop from an accepted public reference to a
-- bounded action and non-personal outcome evidence.  It deliberately does not
-- attach to project_video_share_grant: cross-project shares are read-only
-- reference material and cannot become another project's action card.

create table if not exists project_decision_card (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  source_video_id uuid not null references source_video(id) on delete restrict,
  subject_id uuid,
  hypothesis text not null check (char_length(hypothesis) between 1 and 1200),
  reference_point text not null check (char_length(reference_point) between 1 and 1200),
  adaptation_difference text not null check (char_length(adaptation_difference) between 1 and 1200),
  owner_actor text not null check (char_length(owner_actor) between 3 and 254 and owner_actor = lower(owner_actor)),
  decision text not null check (decision in ('adopt', 'observe', 'exclude')),
  status text not null default 'draft'
    check (status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  review_conclusion text check (review_conclusion is null or char_length(review_conclusion) between 1 and 2000),
  review_evidence text check (review_evidence is null or char_length(review_evidence) between 1 and 1200),
  next_action text check (next_action is null or char_length(next_action) between 1 and 800),
  reviewed_by text check (reviewed_by is null or (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by))),
  reviewed_at timestamptz,
  review_observation_id uuid,
  review_observation_version integer check (review_observation_version is null or review_observation_version > 0),
  review_metric_snapshot jsonb,
  created_by text not null check (char_length(created_by) between 3 and 254 and created_by = lower(created_by)),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (
    status = 'draft' or status = 'reviewed' or status = 'archived'
    or (decision = 'adopt' and status in ('active', 'adopted'))
    or (decision = 'observe' and status = 'observing')
    or (decision = 'exclude' and status = 'excluded')
  ),
  check ((status = 'reviewed') = (review_conclusion is not null and review_evidence is not null and next_action is not null and reviewed_by is not null and reviewed_at is not null and review_observation_id is not null and review_observation_version is not null and review_metric_snapshot is not null))
);
create index if not exists idx_project_decision_card_project_status
  on project_decision_card(project_id, status, updated_at desc);
create index if not exists idx_project_decision_card_project_source_video
  on project_decision_card(project_id, source_video_id, updated_at desc);

-- The source must be an explicit, local project decision.  A canonical video
-- or a shared read-only video is insufficient.
create or replace function enforce_project_decision_card_accepted_source()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    where inclusion_row.project_id = new.project_id
      and inclusion_row.video_id = new.source_video_id
      and inclusion_row.status = 'accepted'
      and exists (
        select 1 from source_video video_row
        where video_row.id = new.source_video_id
          and video_row.availability_status = 'available'
      )
  ) then
    raise exception 'project_decision_card requires locally accepted project video';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_accepted_source on project_decision_card;
create trigger trg_project_decision_card_accepted_source
before insert or update of project_id, source_video_id on project_decision_card
for each row execute function enforce_project_decision_card_accepted_source();
drop trigger if exists trg_project_decision_card_project_id_immutable on project_decision_card;
create trigger trg_project_decision_card_project_id_immutable
before update of project_id on project_decision_card
for each row execute function enforce_project_id_immutable();

-- The assigned owner is a current member at assignment time.  This does not
-- attach a live FK to membership history: later membership revocation must not
-- erase or block the project's already-created historical review.
create or replace function enforce_project_decision_card_owner_member()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from research_project_member member
    where member.project_id = new.project_id
      and member.actor_id = new.owner_actor
      and member.effective_from <= now()
      and member.status = 'active'
      and (member.effective_until is null or member.effective_until > now())
      and not exists (
        select 1 from research_project_member newer
        where newer.project_id=member.project_id and newer.actor_id=member.actor_id
          and newer.effective_from <= now() and newer.effective_from>member.effective_from
      )
  ) then
    raise exception 'project_decision_card owner_actor must be an active project member';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_owner_member on project_decision_card;
create trigger trg_project_decision_card_owner_member
before insert or update of owner_actor, project_id on project_decision_card
for each row execute function enforce_project_decision_card_owner_member();

-- A completed review is evidence, not an editable draft.  This is enforced
-- below the application layer so a direct SQL update cannot rewrite history.
create or replace function reject_reviewed_project_decision_card_change()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' or old.status = 'reviewed' then
    raise exception 'reviewed project_decision_card is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_reviewed_immutable on project_decision_card;
create trigger trg_project_decision_card_reviewed_immutable
before update or delete on project_decision_card
for each row execute function reject_reviewed_project_decision_card_change();

create table if not exists project_decision_card_event (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  actor_id text not null check (char_length(actor_id) between 3 and 254 and actor_id = lower(actor_id)),
  action text not null check (action in ('created', 'updated', 'status_changed', 'reviewed')),
  from_status text check (from_status is null or from_status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  to_status text check (to_status is null or to_status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  changed_fields text[] not null default '{}'::text[] check (cardinality(changed_fields) <= 8),
  created_at timestamptz not null default now(),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict,
  check ((action in ('status_changed', 'reviewed')) = (from_status is not null and to_status is not null))
);
create index if not exists idx_project_decision_card_event_card_time
  on project_decision_card_event(project_id, decision_card_id, created_at desc, id);

create table if not exists project_publication_record (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  decision_card_id uuid,
  publication_date date not null,
  title text not null check (char_length(title) between 1 and 160),
  content_reference text not null check (char_length(content_reference) between 1 and 512),
  status text not null default 'published' check (status = 'published'),
  created_by text not null check (char_length(created_by) between 3 and 254 and created_by = lower(created_by)),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict
);
create index if not exists idx_project_publication_record_project_date
  on project_publication_record(project_id, publication_date desc, id);

create table if not exists project_publication_metric_observation (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  publication_id uuid not null,
  metric_date date not null,
  version integer not null check (version > 0),
  impressions bigint check (impressions is null or impressions >= 0),
  engagements bigint check (engagements is null or engagements >= 0),
  likes bigint check (likes is null or likes >= 0),
  comments bigint check (comments is null or comments >= 0),
  shares bigint check (shares is null or shares >= 0),
  follows bigint check (follows is null or follows >= 0),
  conversions bigint check (conversions is null or conversions >= 0),
  source text not null check (source in ('manual', 'imported_aggregate')),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  source_reported_at timestamptz not null,
  source_version_or_digest text not null check (char_length(source_version_or_digest) between 1 and 256),
  measurement_scope text not null check (char_length(measurement_scope) between 1 and 300),
  recorded_by text not null check (char_length(recorded_by) between 3 and 254 and recorded_by = lower(recorded_by)),
  recorded_at timestamptz not null default now(),
  unique (id, project_id),
  unique (publication_id, metric_date, version),
  foreign key (publication_id, project_id)
    references project_publication_record(id, project_id) on delete restrict,
  check (num_nonnulls(impressions, engagements, likes, comments, shares, follows, conversions) > 0)
);
create index if not exists idx_project_publication_metric_observation_project_date
  on project_publication_metric_observation(project_id, metric_date desc, publication_id, version desc);

create or replace function reject_project_publication_metric_observation_change()
returns trigger language plpgsql as $$
begin
  raise exception 'project_publication_metric_observation is append-only';
end;
$$;
drop trigger if exists trg_project_publication_metric_observation_append_only on project_publication_metric_observation;
create trigger trg_project_publication_metric_observation_append_only
before update or delete on project_publication_metric_observation
for each row execute function reject_project_publication_metric_observation_change();

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'project_decision_card'::regclass
      and conname = 'fk_project_decision_card_review_observation'
  ) then
    alter table project_decision_card
      add constraint fk_project_decision_card_review_observation
      foreign key (review_observation_id, project_id)
      references project_publication_metric_observation(id, project_id) on delete restrict;
  end if;
end $$;

create or replace function enforce_project_decision_card_review_snapshot()
returns trigger language plpgsql as $$
declare
  expected_snapshot jsonb;
begin
  if new.status = 'reviewed' and (tg_op = 'INSERT' or old.status is distinct from 'reviewed') then
    select jsonb_build_object(
      'id', metric.id, 'version', metric.version, 'metric_date', metric.metric_date,
      'impressions', metric.impressions, 'engagements', metric.engagements,
      'likes', metric.likes, 'comments', metric.comments, 'shares', metric.shares,
      'follows', metric.follows, 'conversions', metric.conversions,
      'source', metric.source, 'source_reference', metric.source_reference,
      'source_reported_at', metric.source_reported_at,
      'source_version_or_digest', metric.source_version_or_digest,
      'measurement_scope', metric.measurement_scope, 'recorded_at', metric.recorded_at
    ) into expected_snapshot
    from project_publication_metric_observation metric
    join project_publication_record publication
      on publication.id=metric.publication_id and publication.project_id=metric.project_id
    where metric.id=new.review_observation_id
      and metric.project_id=new.project_id
      and metric.version=new.review_observation_version
      and publication.decision_card_id=new.id
      and publication.status='published';
    if expected_snapshot is null or new.review_metric_snapshot is distinct from expected_snapshot then
      raise exception 'reviewed project_decision_card requires exact local observation snapshot';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_review_snapshot on project_decision_card;
create trigger trg_project_decision_card_review_snapshot
before update of status, review_observation_id, review_observation_version, review_metric_snapshot
on project_decision_card for each row execute function enforce_project_decision_card_review_snapshot();
drop trigger if exists trg_project_decision_card_review_snapshot_insert on project_decision_card;
create trigger trg_project_decision_card_review_snapshot_insert
before insert on project_decision_card
for each row execute function enforce_project_decision_card_review_snapshot();

comment on table project_decision_card is
  'Project-local action hypothesis based only on a locally accepted public video.';
comment on table project_publication_metric_observation is
  'Append-only non-personal aggregate observations. NULL means unknown, never zero; source reference/version are required evidence.';
