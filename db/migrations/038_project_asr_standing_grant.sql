-- A project may authorize cloud ASR for its accepted, available public-video
-- evidence without claiming that a human listened to each normalized WAV.
-- source_video has no separate visibility flag, so the worker must verify
-- public provenance and reject non-public source material.
create table if not exists project_asr_standing_grant (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  grant_version text not null default 'project-asr-standing-v1'
    check (grant_version = 'project-asr-standing-v1'),
  provider text not null check (provider = 'volcengine-doubao-asr'),
  source_scope text not null check (source_scope = 'accepted_available_public_video'),
  status text not null default 'active' check (status in ('active', 'revoked')),
  authorized_by text not null check (char_length(authorized_by) between 3 and 254 and authorized_by = lower(authorized_by)),
  authorized_at timestamptz not null default clock_timestamp(),
  revoked_by text check (revoked_by is null or (char_length(revoked_by) between 3 and 254 and revoked_by = lower(revoked_by))),
  revoked_at timestamptz,
  unique (id, project_id),
  check ((status = 'active' and revoked_by is null and revoked_at is null)
      or (status = 'revoked' and revoked_by is not null and revoked_at is not null))
);
create unique index if not exists uq_project_asr_standing_grant_active
  on project_asr_standing_grant(project_id) where status='active';

create or replace function enforce_project_asr_standing_grant_lifecycle()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'active' or new.revoked_by is not null or new.revoked_at is not null then
      raise exception 'project ASR standing grant must begin active';
    end if;
  elsif old.status <> 'active' or new.status <> 'revoked' then
    raise exception 'project ASR standing grant may only transition active to revoked';
  elsif (new.id,new.project_id,new.grant_version,new.provider,new.source_scope,new.authorized_by,new.authorized_at)
        is distinct from
        (old.id,old.project_id,old.grant_version,old.provider,old.source_scope,old.authorized_by,old.authorized_at) then
    raise exception 'project ASR standing grant scope and authorization are immutable';
  elsif new.revoked_by is null then
    raise exception 'project ASR standing grant revocation requires actor';
  else
    new.revoked_at := coalesce(new.revoked_at, clock_timestamp());
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_asr_standing_grant_lifecycle on project_asr_standing_grant;
create trigger trg_project_asr_standing_grant_lifecycle
before insert or update on project_asr_standing_grant
for each row execute function enforce_project_asr_standing_grant_lifecycle();
create or replace function reject_project_asr_standing_grant_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'project ASR standing grant must be revoked, not deleted';
end;
$$;
drop trigger if exists trg_project_asr_standing_grant_no_delete on project_asr_standing_grant;
create trigger trg_project_asr_standing_grant_no_delete
before delete on project_asr_standing_grant
for each row execute function reject_project_asr_standing_grant_delete();

alter table project_asr_media_review
  add column if not exists authorization_kind text not null default 'listened',
  add column if not exists standing_grant_id uuid;
alter table project_asr_media_review
  add constraint project_asr_media_review_standing_grant_fk
  foreign key (standing_grant_id, project_id)
  references project_asr_standing_grant(id, project_id) on delete restrict;

-- 032's unnamed table-level lifecycle check requires reviewed_by/at for every
-- approved row. Replace that exact check so standing approval cannot assert it.
do $$
declare old_check text;
begin
  select conname into old_check from pg_constraint
  where conrelid='project_asr_media_review'::regclass and contype='c'
    and pg_get_constraintdef(oid) like '%reviewed_by%'
    and pg_get_constraintdef(oid) like '%reviewed_at%'
    and pg_get_constraintdef(oid) like '%revoked_by%'
    and pg_get_constraintdef(oid) like '%revoked_at%'
    and conname <> 'project_asr_media_review_authorization_state_check';
  if old_check is null then
    raise exception '032 project ASR media review lifecycle check not found';
  end if;
  execute format('alter table project_asr_media_review drop constraint %I', old_check);
end;
$$;
alter table project_asr_media_review
  add constraint project_asr_media_review_authorization_state_check check (
    (authorization_kind='listened' and standing_grant_id is null and
      ((status='draft' and reviewed_by is null and reviewed_at is null and revoked_by is null and revoked_at is null)
       or (status='approved' and reviewed_by is not null and reviewed_at is not null and revoked_by is null and revoked_at is null)
       or (status='revoked' and revoked_by is not null and revoked_at is not null
           and ((reviewed_by is null and reviewed_at is null) or (reviewed_by is not null and reviewed_at is not null)))))
    or
    (authorization_kind='standing_grant' and standing_grant_id is not null
      and review_version=('standing-media-v1:' || standing_grant_id::text)
      and review_statement->>'listening_status' is not distinct from 'not_listened'
      and coalesce(review_statement->>'consent_statement','') <> '我已核对音频内容并同意交由云端转写'
      and reviewed_by is null and reviewed_at is null
      and ((status in ('draft','approved') and revoked_by is null and revoked_at is null)
        or (status='revoked' and revoked_by is not null and revoked_at is not null)))
  );

create or replace function enforce_project_asr_media_review_lifecycle()
returns trigger language plpgsql as $$
declare grant_row project_asr_standing_grant%rowtype;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' or new.reviewed_by is not null or new.reviewed_at is not null
       or new.revoked_by is not null or new.revoked_at is not null then
      raise exception 'project_asr_media_review must begin as draft';
    end if;
  elsif old.status = 'draft' then
    if new.status not in ('draft', 'approved', 'revoked') then
      raise exception 'draft project_asr_media_review may only become approved or revoked';
    end if;
  elsif old.status = 'approved' then
    if new.status <> 'revoked' then
      raise exception 'approved project_asr_media_review may only become revoked';
    end if;
  else
    raise exception 'revoked project_asr_media_review is immutable';
  end if;

  if tg_op = 'UPDATE' and old.status <> 'draft' and (
    new.project_id, new.video_id, new.asset_id, new.review_version, new.media_fingerprint, new.asset_manifest_fingerprint,
    new.delivery_origin, new.identity_source, new.review_statement, new.authorization_kind, new.standing_grant_id,
    new.reviewed_by, new.reviewed_at
  ) is distinct from (
    old.project_id, old.video_id, old.asset_id, old.review_version, old.media_fingerprint, old.asset_manifest_fingerprint,
    old.delivery_origin, old.identity_source, old.review_statement, old.authorization_kind, old.standing_grant_id,
    old.reviewed_by, old.reviewed_at
  ) then
    raise exception 'approved project_asr_media_review content is immutable';
  end if;
  if not exists (
    select 1 from media_asset asset
    where asset.id=new.asset_id and asset.video_id=new.video_id
      and asset.kind='audio' and asset.content_type='audio/wav'
      and asset.content_sha256=new.media_fingerprint
  ) then
    raise exception 'project_asr_media_review requires matching normalized WAV audio asset fingerprint';
  end if;

  if new.authorization_kind='standing_grant' then
    if new.standing_grant_id is null or new.review_version <> ('standing-media-v1:' || new.standing_grant_id::text)
       or new.review_statement->>'listening_status' is distinct from 'not_listened'
       or new.reviewed_by is not null or new.reviewed_at is not null then
      raise exception 'standing ASR authorization does not assert human listening';
    end if;
    if new.status='approved' and (tg_op='INSERT' or old.status <> 'approved') then
      select * into grant_row from project_asr_standing_grant
      where id=new.standing_grant_id and project_id=new.project_id for update;
      if not found or grant_row.status <> 'active' then
        raise exception 'standing ASR review requires active project grant';
      end if;
    end if;
  elsif new.authorization_kind <> 'listened' or new.standing_grant_id is not null then
    raise exception 'project ASR review authorization kind is invalid';
  end if;

  if new.status = 'approved' then
    if new.authorization_kind='listened' then
      if new.reviewed_by is null then
        raise exception 'approved project_asr_media_review requires reviewed_by';
      end if;
      new.reviewed_at := coalesce(new.reviewed_at, clock_timestamp());
    end if;
    new.revoked_by := null;
    new.revoked_at := null;
  elsif new.status = 'revoked' then
    if new.revoked_by is null then
      raise exception 'revoked project_asr_media_review requires revoked_by';
    end if;
    new.revoked_at := coalesce(new.revoked_at, clock_timestamp());
    if tg_op = 'INSERT' or old.status = 'draft' then
      new.reviewed_by := null;
      new.reviewed_at := null;
    end if;
  elsif tg_op = 'INSERT' or old.status = 'draft' then
    new.reviewed_by := null;
    new.reviewed_at := null;
    new.revoked_by := null;
    new.revoked_at := null;
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;

create or replace function enforce_project_asr_execution_job_approval()
returns trigger language plpgsql as $$
declare
  review_row project_asr_media_review%rowtype;
  grant_row project_asr_standing_grant%rowtype;
  new_submission boolean;
  reconciling_submission boolean;
begin
  -- A provider call may have started just before revocation, while the
  -- response transaction rolled back. Preserve its unknown-cost/audit fact
  -- without reopening authorization for another HTTP submission.
  reconciling_submission := false;
  if tg_op='UPDATE' then
    reconciling_submission := old.status='submitting'
      and new.status='submitting' and old.submission_count=0 and new.submission_count=1
      and new.error_code='project_asr_reconciliation_required'
      and new.task_cost_id is not null and new.provider_task_ref is null
      and (new.task_key,new.project_id,new.video_id,new.media_review_id,
           new.reviewed_asset_id,new.review_version,new.media_fingerprint,
           new.asset_manifest_fingerprint,new.provider,new.model_id,
           new.model_revision,new.engine_version,new.source_fingerprint,
           new.cost_currency,new.poll_count)
          is not distinct from
          (old.task_key,old.project_id,old.video_id,old.media_review_id,
           old.reviewed_asset_id,old.review_version,old.media_fingerprint,
           old.asset_manifest_fingerprint,old.provider,old.model_id,
           old.model_revision,old.engine_version,old.source_fingerprint,
           old.cost_currency,old.poll_count);
  end if;
  if new.status in ('submitting', 'submitted', 'running', 'completed')
     and not reconciling_submission then
    select * into review_row from project_asr_media_review
      where id=new.media_review_id and project_id=new.project_id and video_id=new.video_id
      for update;
    if not found or review_row.status <> 'approved'
       or review_row.review_version <> new.review_version
       or review_row.media_fingerprint <> new.media_fingerprint
       or review_row.media_fingerprint <> new.source_fingerprint
       or review_row.asset_manifest_fingerprint <> new.asset_manifest_fingerprint
       or review_row.asset_id <> new.reviewed_asset_id then
      raise exception 'project_asr_execution_job requires current approved project media review';
    end if;
    if review_row.authorization_kind='standing_grant' then
      new_submission := new.status='submitting' or
        (tg_op='INSERT' and new.status in ('submitted','running','completed')) or
        (tg_op='UPDATE' and old.status not in ('submitting','submitted','running')
         and new.status in ('submitted','running','completed'));
      -- The grant row lock serializes a first submit with revocation. A
      -- previously submitted task may still poll and settle after revocation.
      select * into grant_row from project_asr_standing_grant
      where id=review_row.standing_grant_id and project_id=new.project_id for update;
      if not found or grant_row.provider <> new.provider
         or grant_row.source_scope <> 'accepted_available_public_video'
         or (new_submission and grant_row.status <> 'active') then
        raise exception 'project_asr_execution_job requires matching active standing grant for new submission';
      end if;
    elsif review_row.authorization_kind <> 'listened' then
      raise exception 'project_asr_execution_job authorization kind is invalid';
    end if;
    if not exists (
      select 1 from project_video_inclusion inclusion_row
      join source_video video_row on video_row.id=inclusion_row.video_id
      where inclusion_row.project_id=new.project_id and inclusion_row.video_id=new.video_id
        and inclusion_row.status='accepted' and video_row.availability_status='available'
    ) then
      raise exception 'project_asr_execution_job requires accepted available project video';
    end if;
  end if;
  if tg_op = 'UPDATE' and old.project_id is distinct from new.project_id then
    raise exception 'project_asr_execution_job project_id is immutable';
  end if;
  if new.task_cost_id is not null and not exists (
    select 1 from project_research_task_cost cost
    where cost.id=new.task_cost_id and cost.project_id=new.project_id and cost.video_id=new.video_id
      and cost.task_key=new.task_key and cost.task_type='asr_transcription'
  ) then
    raise exception 'project_asr_execution_job task cost must be matching local ASR cost';
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;

comment on table project_asr_standing_grant is
  'Revocable project-level permission for cloud ASR of accepted available public-video audio; not proof of per-video listening.';
comment on column project_asr_media_review.authorization_kind is
  'listened is human per-asset review; standing_grant is a scoped delivery authorization and never proof of listening.';
