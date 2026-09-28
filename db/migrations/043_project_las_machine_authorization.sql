-- Cloud processing permission and human whole-video review are different facts.
-- Machine-first permission binds one project, immutable video version and LAS
-- purpose; it never fills project_las_video_review or implies human viewing.
lock table project_las_analysis_attempt in access exclusive mode;

create table if not exists project_las_video_authorization (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  asset_id uuid not null,
  asset_sha256 text not null check (asset_sha256 ~ '^[0-9a-f]{64}$'),
  asset_manifest_fingerprint text not null
    check (asset_manifest_fingerprint ~ '^[0-9a-f]{64}$'),
  delivery_origin text not null check (char_length(delivery_origin) between 1 and 160),
  object_version_id text not null check (char_length(object_version_id) between 1 and 512
    and object_version_id=btrim(object_version_id) and lower(object_version_id)<>'null'),
  authorization_version text not null check (authorization_version='las-machine-v1'),
  operator_id text not null default 'las_video_understanding'
    check (operator_id='las_video_understanding'),
  template_id text not null default 'omni_video_audio_captioning@v1'
    check (template_id='omni_video_audio_captioning@v1'),
  model_id text not null default 'doubao-seed-2-0-lite-260428'
    check (model_id='doubao-seed-2-0-lite-260428'),
  consent_statement jsonb not null check (
    jsonb_typeof(consent_statement)='object' and pg_column_size(consent_statement)<=8192
  ),
  status text not null default 'draft' check (status in ('draft','authorized','revoked')),
  authorized_by text check (authorized_by is null or
    (char_length(authorized_by) between 3 and 254 and authorized_by=lower(authorized_by))),
  authorized_at timestamptz,
  revoked_by text check (revoked_by is null or
    (char_length(revoked_by) between 3 and 254 and revoked_by=lower(revoked_by))),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id,project_id,video_id,asset_id),
  foreign key (project_id,video_id)
    references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (asset_id,video_id)
    references media_asset(id,video_id) on delete restrict,
  check (
    (status='draft' and authorized_by is null and authorized_at is null
      and revoked_by is null and revoked_at is null) or
    (status='authorized' and authorized_by is not null and authorized_at is not null
      and revoked_by is null and revoked_at is null) or
    (status='revoked' and revoked_by is not null and revoked_at is not null)
  )
);
create index if not exists idx_project_las_authorization_active
  on project_las_video_authorization(project_id,video_id,authorized_at desc)
  where status='authorized';
create unique index if not exists uq_project_las_authorization_active_asset
  on project_las_video_authorization(project_id,asset_id)
  where status='authorized';

create or replace function enforce_project_las_video_authorization()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from media_asset asset where asset.id=new.asset_id
      and asset.video_id=new.video_id and asset.kind='video'
      and asset.content_sha256=new.asset_sha256
  ) then
    raise exception 'LAS authorization requires matching stored video asset';
  end if;
  if tg_op='INSERT' then
    if new.status<>'draft' then
      raise exception 'LAS authorization must begin as draft';
    end if;
  else
    if (new.project_id,new.video_id,new.asset_id,new.asset_sha256,
        new.asset_manifest_fingerprint,new.delivery_origin,new.object_version_id,
        new.authorization_version,new.operator_id,new.template_id,new.model_id,
        new.consent_statement,new.created_at)
       is distinct from
       (old.project_id,old.video_id,old.asset_id,old.asset_sha256,
        old.asset_manifest_fingerprint,old.delivery_origin,old.object_version_id,
        old.authorization_version,old.operator_id,old.template_id,old.model_id,
        old.consent_statement,old.created_at) then
      raise exception 'LAS authorization content is immutable';
    end if;
    if old.status='authorized' and new.status not in ('authorized','revoked') or
       old.status='revoked' or
       old.status='draft' and new.status not in ('draft','authorized','revoked') then
      raise exception 'LAS authorization state transition is invalid';
    end if;
    if old.status='authorized' and
       (new.authorized_by,new.authorized_at) is distinct from
       (old.authorized_by,old.authorized_at) then
      raise exception 'LAS authorization identity is immutable';
    end if;
  end if;
  if new.status='authorized' then
    if new.authorized_by is null or
       char_length(btrim(coalesce(new.consent_statement->>'cloud_consent',''))) < 8 or
       new.consent_statement->>'human_review_status' is distinct from 'not_asserted' then
      raise exception 'LAS machine-first permission requires consent without review claim';
    end if;
    new.authorized_at := coalesce(new.authorized_at,clock_timestamp());
  elsif new.status='revoked' then
    if new.revoked_by is null then
      raise exception 'LAS authorization revocation requires identity';
    end if;
    new.revoked_at := coalesce(new.revoked_at,clock_timestamp());
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;
drop trigger if exists trg_project_las_video_authorization on project_las_video_authorization;
create trigger trg_project_las_video_authorization
before insert or update on project_las_video_authorization
for each row execute function enforce_project_las_video_authorization();
drop trigger if exists trg_project_las_video_authorization_no_delete on project_las_video_authorization;
create trigger trg_project_las_video_authorization_no_delete
before delete on project_las_video_authorization
for each row execute function reject_project_las_receipt_change();

alter table project_las_analysis_attempt
  add column if not exists video_authorization_id uuid;
alter table project_las_analysis_attempt
  drop constraint if exists project_las_attempt_authorization_fk;
alter table project_las_analysis_attempt
  add constraint project_las_attempt_authorization_fk
  foreign key (video_authorization_id,project_id,video_id,asset_id)
  references project_las_video_authorization(id,project_id,video_id,asset_id)
  on delete restrict;
alter table project_las_analysis_attempt
  drop constraint if exists project_las_one_permission_source;
alter table project_las_analysis_attempt
  add constraint project_las_one_permission_source
  check (video_review_id is null or video_authorization_id is null);
alter table project_las_analysis_attempt
  drop constraint if exists project_las_live_review_required;
alter table project_las_analysis_attempt
  drop constraint if exists project_las_live_permission_required;
alter table project_las_analysis_attempt
  add constraint project_las_live_permission_required
  check (record_mode<>'live_pre_dispatch' or
         num_nonnulls(video_review_id,video_authorization_id)=1 or
         status in ('cancelled','failed','completed','unknown')) not valid;

create or replace function enforce_project_las_authorization_attempt_identity()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and
     new.video_authorization_id is distinct from old.video_authorization_id then
    raise exception 'LAS attempt authorization identity is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_authorization_attempt_identity on project_las_analysis_attempt;
create trigger trg_project_las_authorization_attempt_identity
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_authorization_attempt_identity();

create or replace function enforce_project_las_live_review_binding()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and new.video_review_id is distinct from old.video_review_id then
    raise exception 'LAS attempt review identity is immutable';
  end if;
  if new.record_mode='live_pre_dispatch' and
     (tg_op='INSERT' or old.status='prepared' and new.status='submitting') then
    if new.video_authorization_id is not null then
      if not exists (
        select 1 from project_las_video_authorization permission_row
        where permission_row.id=new.video_authorization_id
          and permission_row.project_id=new.project_id
          and permission_row.video_id=new.video_id
          and permission_row.asset_id=new.asset_id
          and permission_row.asset_sha256=new.asset_sha256
          and permission_row.status='authorized'
          and new.authorization_ref=permission_row.id::text
          and new.authorized_by=permission_row.authorized_by
        for update of permission_row
      ) then
        raise exception 'live LAS attempt requires current project video authorization';
      end if;
    elsif not exists (
      select 1 from project_las_video_review review
      where review.id=new.video_review_id and review.project_id=new.project_id
        and review.video_id=new.video_id and review.asset_id=new.asset_id
        and review.asset_sha256=new.asset_sha256 and review.status='approved'
        and new.authorization_ref=review.id::text
        and new.authorized_by=review.reviewed_by
      for update of review
    ) then
      raise exception 'live LAS attempt requires current project video review';
    end if;
    if not exists (
      select 1 from project_video_inclusion inclusion_row
      join source_video source_row on source_row.id=inclusion_row.video_id
      join research_project project_row on project_row.id=inclusion_row.project_id
      join research_organization org_row on org_row.id=project_row.organization_id
      where inclusion_row.project_id=new.project_id and inclusion_row.video_id=new.video_id
        and inclusion_row.status='accepted' and source_row.availability_status='available'
        and project_row.status='active' and org_row.status='active'
      for update of inclusion_row,source_row,project_row,org_row
    ) then
      raise exception 'live LAS attempt requires active project and available video';
    end if;
  end if;
  return new;
end;
$$;

create or replace function enforce_project_las_attempt_version_binding()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and new.object_version_id is distinct from old.object_version_id then
    raise exception 'LAS attempt byte version is immutable';
  end if;
  if new.record_mode='live_pre_dispatch' and
     (tg_op='INSERT' or old.status='prepared' and new.status='submitting') then
    if new.video_authorization_id is not null then
      if not exists (
        select 1 from project_las_video_authorization permission_row
        where permission_row.id=new.video_authorization_id
          and permission_row.status='authorized'
          and permission_row.object_version_id=new.object_version_id
      ) then
        raise exception 'new live LAS attempt requires matching authorized object version';
      end if;
    elsif not exists (
      select 1 from project_las_video_review review
      where review.id=new.video_review_id and review.status='approved'
        and review.binding_scheme='versioned_bytes_v2'
        and review.object_version_id is not null
        and review.object_version_id=new.object_version_id
    ) then
      raise exception 'new live LAS attempt requires matching reviewed object version';
    end if;
  end if;
  return new;
end;
$$;

alter table project_las_analysis_receipt
  drop constraint if exists project_las_analysis_receipt_binding_basis_check;
alter table project_las_analysis_receipt
  add constraint project_las_analysis_receipt_binding_basis_check
  check (binding_basis in ('reviewed_submission_and_result',
                          'authorized_machine_first_and_result'));

create or replace function enforce_project_las_live_receipt_contract()
returns trigger language plpgsql as $$
begin
  if exists (
    select 1 from project_las_analysis_attempt attempt
    where attempt.id=new.attempt_id and attempt.record_mode='live_pre_dispatch'
  ) and not exists (
    select 1 from project_las_analysis_attempt attempt
    left join project_las_video_review review on review.id=attempt.video_review_id
    left join project_las_video_authorization permission_row
      on permission_row.id=attempt.video_authorization_id
    where attempt.id=new.attempt_id and attempt.project_id=new.project_id
      and attempt.video_id=new.video_id and attempt.asset_id=new.asset_id
      and new.operator_version='v1'
      and (
        (attempt.video_review_id is not null and new.model_id=review.model_id
         and new.operator_id=review.operator_id and new.template_id=review.template_id
         and new.binding_basis='reviewed_submission_and_result') or
        (attempt.video_authorization_id is not null
         and new.model_id=permission_row.model_id
         and new.operator_id=permission_row.operator_id
         and new.template_id=permission_row.template_id
         and new.binding_basis='authorized_machine_first_and_result')
      )
  ) then
    raise exception 'live LAS receipt must match its permission and provider contract';
  end if;
  return new;
end;
$$;
