-- Human Case viewing is project-local and independently versioned. Existing
-- versions survive changes to project_video_inclusion.
create table if not exists project_video_case_review (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  video_id uuid not null references source_video(id) on delete restrict,
  version_no integer not null check (version_no > 0),
  status text not null check (status in ('partial', 'complete', 'insufficient')),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  observed_at timestamptz not null,
  video_coverage text not null check (video_coverage in ('none', 'partial', 'complete')),
  audio_coverage text not null check (audio_coverage in ('none', 'partial', 'complete', 'not_applicable')),
  audio_not_applicable_reason text check (audio_not_applicable_reason is null or char_length(audio_not_applicable_reason) between 1 and 1000),
  key_event_complete boolean,
  verified_facts text not null default '' check (char_length(verified_facts) <= 4000),
  evidence_gaps text not null default '' check (char_length(evidence_gaps) <= 4000),
  counterevidence text not null default '' check (char_length(counterevidence) <= 4000),
  comparability_note text not null default '' check (char_length(comparability_note) <= 4000),
  reviewed_by text not null check (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by)),
  created_at timestamptz not null default now(),
  unique (project_id, video_id, version_no),
  check ((audio_coverage = 'not_applicable') = (audio_not_applicable_reason is not null)),
  check (key_event_complete is distinct from false or status = 'insufficient'),
  check (status <> 'complete' or (video_coverage = 'complete'
    and (audio_coverage = 'complete' or (audio_coverage = 'not_applicable' and audio_not_applicable_reason is not null))
    and key_event_complete is true and char_length(trim(verified_facts)) > 0
    and char_length(trim(comparability_note)) > 0))
);
create index if not exists idx_project_video_case_review_latest
  on project_video_case_review(project_id, video_id, version_no desc);

create or replace function reject_project_video_case_review_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'project_video_case_review is append-only';
end;
$$;
drop trigger if exists trg_project_video_case_review_append_only on project_video_case_review;
create trigger trg_project_video_case_review_append_only
before update or delete on project_video_case_review
for each row execute function reject_project_video_case_review_mutation();

comment on table project_video_case_review is
  'Project-private human Case viewing history. No accepted, ASR or L3 state is inferred from these rows.';

alter table project_decision_card
  add column if not exists source_case_review_id uuid references project_video_case_review(id) on delete restrict;
alter table project_decision_card_evidence_ref
  add column if not exists case_review_id_at_binding uuid references project_video_case_review(id) on delete restrict;
create or replace function reject_project_case_binding_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'bound project case review is immutable';
end;
$$;
drop trigger if exists trg_project_decision_card_case_binding_immutable on project_decision_card;
create trigger trg_project_decision_card_case_binding_immutable
before update of source_case_review_id on project_decision_card
for each row execute function reject_project_case_binding_mutation();

-- The trigger is scoped to insert/source changes. Historical cards remain
-- readable and status updates do not re-evaluate their former source.
create or replace function enforce_project_decision_card_accepted_source()
returns trigger language plpgsql as $$
declare
  bound_review uuid;
begin
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    where inclusion_row.project_id = new.project_id
      and inclusion_row.video_id = new.source_video_id
      and inclusion_row.status = 'accepted'
      and exists (select 1 from source_video video_row
                  where video_row.id=new.source_video_id and video_row.availability_status='available')
  ) then
    raise exception 'project_decision_card requires current complete project case review';
  end if;
  select review.id into bound_review from project_video_case_review review
  where review.project_id=new.project_id and review.video_id=new.source_video_id
  order by review.version_no desc limit 1;
  if bound_review is null or
     (select review.status from project_video_case_review review where review.id=bound_review) <> 'complete' then
    raise exception 'project_decision_card requires current complete project case review';
  end if;
  new.source_case_review_id := bound_review;
  return new;
end;
$$;

-- Continuing an action requires the same reviewed evidence versions and a
-- still-approved, rights-cleared profile when the action adopts an IP.
create or replace function project_action_evidence_is_current(p_project uuid, p_card uuid)
returns boolean language sql stable as $$
  select exists (
    select 1 from project_decision_card card
    left join project_decision_card_profile_binding binding
      on binding.project_id=card.project_id and binding.decision_card_id=card.id
    left join research_subject_profile_version profile
      on profile.project_id=card.project_id and profile.id=binding.profile_id
    where card.id=p_card and card.project_id=p_project
      and (card.decision <> 'adopt' or
           (profile.status='approved' and profile.rights_status='cleared'))
  ) and not exists (
    select 1 from (
      select card.source_video_id as video_id, card.source_case_review_id as bound_id
      from project_decision_card card where card.id=p_card and card.project_id=p_project
      union all
      select ref.video_id, ref.case_review_id_at_binding
      from project_decision_card_evidence_ref ref
      where ref.decision_card_id=p_card and ref.project_id=p_project
    ) source_row
    left join project_video_inclusion inclusion_row
      on inclusion_row.project_id=p_project and inclusion_row.video_id=source_row.video_id
    left join source_video video_row on video_row.id=source_row.video_id
    left join lateral (
      select review.id, review.status from project_video_case_review review
      where review.project_id=p_project and review.video_id=source_row.video_id
      order by review.version_no desc limit 1
    ) latest_review on true
    where inclusion_row.project_id is null or inclusion_row.status <> 'accepted'
       or video_row.availability_status is distinct from 'available'
       or source_row.bound_id is null or latest_review.id is distinct from source_row.bound_id
       or latest_review.status is distinct from 'complete'
  );
$$;

create or replace function enforce_project_action_current_evidence()
returns trigger language plpgsql as $$
begin
  if tg_table_name='project_publication_record' then
    if new.decision_card_id is not null and
       not project_action_evidence_is_current(new.project_id,new.decision_card_id) then
      raise exception 'project action evidence is no longer current';
    end if;
  elsif new.status='adopted' and old.status is distinct from new.status and
        not project_action_evidence_is_current(new.project_id,new.id) then
    raise exception 'project action evidence is no longer current';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_publication_current_evidence on project_publication_record;
create trigger trg_project_publication_current_evidence
before insert on project_publication_record
for each row execute function enforce_project_action_current_evidence();
drop trigger if exists trg_project_decision_card_current_evidence on project_decision_card;
create trigger trg_project_decision_card_current_evidence
before update of status on project_decision_card
for each row execute function enforce_project_action_current_evidence();

create or replace function enforce_project_decision_card_evidence_ref()
returns trigger language plpgsql as $$
declare
  primary_video uuid;
  card_created_at timestamptz;
  card_status text;
  bound_review uuid;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_evidence_ref is append-only';
  end if;
  select card.source_video_id, card.created_at, card.status
    into primary_video, card_created_at, card_status
  from project_decision_card card
  where card.id=new.decision_card_id and card.project_id=new.project_id
  for update;
  if primary_video is null then
    raise exception 'project_decision_card_evidence_ref requires a local decision card';
  end if;
  if card_created_at is distinct from transaction_timestamp()
     or card_status not in ('active', 'observing', 'excluded') then
    raise exception 'project_decision_card_evidence_ref must be bound when the card is created';
  end if;
  if new.video_id=primary_video then
    raise exception 'project_decision_card_evidence_ref cannot repeat primary video';
  end if;
  if (select count(*) from project_decision_card_evidence_ref ref
      where ref.decision_card_id=new.decision_card_id) >= 12 then
    raise exception 'project_decision_card_evidence_ref exceeds 12 videos';
  end if;
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    join source_video video_row on video_row.id=inclusion_row.video_id
    where inclusion_row.project_id=new.project_id
      and inclusion_row.video_id=new.video_id
      and inclusion_row.status='accepted'
      and video_row.availability_status='available'
  ) then
    raise exception 'project_decision_card_evidence_ref requires current complete project case review';
  end if;
  select review.id into bound_review from project_video_case_review review
  where review.project_id=new.project_id and review.video_id=new.video_id
  order by review.version_no desc limit 1;
  if bound_review is null or
     (select review.status from project_video_case_review review where review.id=bound_review) <> 'complete' then
    raise exception 'project_decision_card_evidence_ref requires current complete project case review';
  end if;
  new.case_review_id_at_binding := bound_review;
  select video_row.title, account_row.nickname
    into new.video_title_at_binding, new.account_name_at_binding
  from source_video video_row
  left join source_account account_row on account_row.id=video_row.account_id
  where video_row.id=new.video_id;
  return new;
end;
$$;
