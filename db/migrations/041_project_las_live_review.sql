-- Project-local whole-video review is an authorization for one exact stored
-- object and one LAS analysis purpose, never a reusable global approval.
create table if not exists project_las_video_review (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  asset_id uuid not null,
  asset_sha256 text not null check (asset_sha256 ~ '^[0-9a-f]{64}$'),
  asset_manifest_fingerprint text not null check (asset_manifest_fingerprint ~ '^[0-9a-f]{64}$'),
  delivery_origin text not null check (char_length(delivery_origin) between 1 and 160),
  review_version text not null check (char_length(review_version) between 1 and 80),
  operator_id text not null default 'las_video_understanding'
    check (operator_id='las_video_understanding'),
  template_id text not null default 'omni_video_audio_captioning@v1'
    check (template_id='omni_video_audio_captioning@v1'),
  model_id text not null default 'doubao-seed-2-0-lite-260428'
    check (model_id='doubao-seed-2-0-lite-260428'),
  review_statement jsonb not null check (
    jsonb_typeof(review_statement)='object' and pg_column_size(review_statement)<=8192
  ),
  status text not null default 'draft' check (status in ('draft','approved','revoked')),
  reviewed_by text check (reviewed_by is null or
    (char_length(reviewed_by) between 3 and 254 and reviewed_by=lower(reviewed_by))),
  reviewed_at timestamptz,
  revoked_by text check (revoked_by is null or
    (char_length(revoked_by) between 3 and 254 and revoked_by=lower(revoked_by))),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id,project_id,video_id,asset_id),
  unique (project_id,asset_id,review_version),
  foreign key (project_id,video_id)
    references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (asset_id,video_id)
    references media_asset(id,video_id) on delete restrict,
  check (
    (status='draft' and reviewed_by is null and reviewed_at is null
      and revoked_by is null and revoked_at is null) or
    (status='approved' and reviewed_by is not null and reviewed_at is not null
      and revoked_by is null and revoked_at is null) or
    (status='revoked' and revoked_by is not null and revoked_at is not null)
  )
);
create index if not exists idx_project_las_review_active
  on project_las_video_review(project_id,video_id,reviewed_at desc)
  where status='approved';

create or replace function enforce_project_las_video_review()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from media_asset asset where asset.id=new.asset_id
      and asset.video_id=new.video_id and asset.kind='video'
      and asset.content_sha256=new.asset_sha256
  ) then
    raise exception 'LAS review requires matching stored video asset';
  end if;
  if tg_op='INSERT' then
    if new.status<>'draft' then
      raise exception 'LAS review must begin as draft';
    end if;
  else
    if (new.project_id,new.video_id,new.asset_id,new.asset_sha256,
        new.asset_manifest_fingerprint,new.delivery_origin,new.review_version,
        new.operator_id,new.template_id,new.model_id,
        new.review_statement,new.created_at)
       is distinct from
       (old.project_id,old.video_id,old.asset_id,old.asset_sha256,
        old.asset_manifest_fingerprint,old.delivery_origin,old.review_version,
        old.operator_id,old.template_id,old.model_id,
        old.review_statement,old.created_at)
       then
      raise exception 'LAS review content is immutable';
    end if;
    if old.status='approved' and new.status not in ('approved','revoked') or
       old.status='revoked' or
       old.status='draft' and new.status not in ('draft','approved','revoked') then
      raise exception 'LAS review state transition is invalid';
    end if;
    if old.status='approved' and
       (new.reviewed_by,new.reviewed_at) is distinct from
       (old.reviewed_by,old.reviewed_at) then
      raise exception 'approved LAS review identity is immutable';
    end if;
  end if;
  if new.status='approved' then
    if new.reviewed_by is null or
       char_length(btrim(coalesce(new.review_statement->>'consent_statement',''))) < 8 then
      raise exception 'LAS approval requires human identity and statement';
    end if;
    new.reviewed_at := coalesce(new.reviewed_at,clock_timestamp());
  elsif new.status='revoked' then
    if new.revoked_by is null then
      raise exception 'LAS revocation requires human identity';
    end if;
    new.revoked_at := coalesce(new.revoked_at,clock_timestamp());
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;
drop trigger if exists trg_project_las_video_review on project_las_video_review;
create trigger trg_project_las_video_review
before insert or update on project_las_video_review
for each row execute function enforce_project_las_video_review();
drop trigger if exists trg_project_las_video_review_no_delete on project_las_video_review;
create trigger trg_project_las_video_review_no_delete
before delete on project_las_video_review
for each row execute function reject_project_las_receipt_change();

alter table project_las_analysis_attempt
  add column if not exists video_review_id uuid;
alter table project_las_analysis_attempt
  add column if not exists provider_task_recorded_at timestamptz;
alter table project_las_analysis_attempt
  add column if not exists requested_by text
  check (requested_by is null or
    (char_length(requested_by) between 3 and 254 and requested_by=lower(requested_by)));
-- 040 could hold pre-review live attempts. Never dispatch those after this
-- migration: cancel unsubmitted work and quarantine any claimed/provider task
-- for manual reconciliation without claiming a reviewed completion.
update project_las_analysis_attempt
   set status=case when status='prepared' then 'cancelled' else 'unknown' end,
       error_code='legacy_live_unreviewed_requires_manual_reconciliation'
 where record_mode='live_pre_dispatch' and video_review_id is null
   and status in ('prepared','submitting','submitted','running');
alter table project_las_analysis_attempt
  drop constraint if exists project_las_attempt_review_fk;
alter table project_las_analysis_attempt
  add constraint project_las_attempt_review_fk
  foreign key (video_review_id,project_id,video_id,asset_id)
  references project_las_video_review(id,project_id,video_id,asset_id) on delete restrict;
alter table project_las_analysis_attempt
  drop constraint if exists project_las_live_review_required;
alter table project_las_analysis_attempt
  add constraint project_las_live_review_required
  check (record_mode<>'live_pre_dispatch' or video_review_id is not null or
         status in ('cancelled','failed','completed','unknown')) not valid;
alter table project_las_analysis_attempt
  drop constraint if exists project_las_live_provider_required;
alter table project_las_analysis_attempt
  add constraint project_las_live_provider_required
  check (record_mode<>'live_pre_dispatch' or provider='volcengine_las' or
         (video_review_id is null and status in ('cancelled','failed','completed','unknown')))
  not valid;

create or replace function enforce_project_las_live_review_binding()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and new.video_review_id is distinct from old.video_review_id then
    raise exception 'LAS attempt review identity is immutable';
  end if;
  if new.record_mode='live_pre_dispatch' and
     (tg_op='INSERT' or old.status='prepared' and new.status='submitting') then
    if not exists (
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
drop trigger if exists trg_project_las_live_review_binding on project_las_analysis_attempt;
create trigger trg_project_las_live_review_binding
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_live_review_binding();

create or replace function enforce_project_las_provider_task_time()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' then
    if old.provider_task_recorded_at is not null and
       new.provider_task_recorded_at is distinct from old.provider_task_recorded_at then
      raise exception 'LAS provider task time is immutable';
    end if;
    if old.provider_task_ref is null and new.provider_task_ref is not null and
       new.record_mode='live_pre_dispatch' then
      new.provider_task_recorded_at := clock_timestamp();
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_provider_task_time on project_las_analysis_attempt;
create trigger trg_project_las_provider_task_time
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_provider_task_time();

create or replace function enforce_project_las_request_actor()
returns trigger language plpgsql as $$
begin
  if new.record_mode='live_pre_dispatch' and tg_op='INSERT' and
     (new.requested_by is null or not exists (
       select 1 from (
         select distinct on (actor_id) actor_id,role,status,effective_until
         from research_project_member
         where project_id=new.project_id and actor_id=new.requested_by
           and effective_from<=now()
         order by actor_id,effective_from desc
       ) latest
       where latest.status='active' and latest.role in ('owner','admin')
         and (latest.effective_until is null or latest.effective_until>now())
     )) then
    raise exception 'live LAS request requires authenticated project manager';
  end if;
  if tg_op='UPDATE' and new.requested_by is distinct from old.requested_by then
    raise exception 'LAS paid request identity is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_request_actor on project_las_analysis_attempt;
create trigger trg_project_las_request_actor
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_request_actor();

create or replace function enforce_project_las_live_receipt_contract()
returns trigger language plpgsql as $$
begin
  if exists (
    select 1 from project_las_analysis_attempt attempt
    where attempt.id=new.attempt_id and attempt.record_mode='live_pre_dispatch'
  ) and not exists (
    select 1 from project_las_analysis_attempt attempt
    join project_las_video_review review on review.id=attempt.video_review_id
    where attempt.id=new.attempt_id and attempt.project_id=new.project_id
      and attempt.video_id=new.video_id and attempt.asset_id=new.asset_id
      and new.model_id=review.model_id and new.operator_id=review.operator_id
      and new.operator_version='v1' and new.template_id=review.template_id
  ) then
    raise exception 'live LAS receipt must match reviewed provider contract';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_live_receipt_contract on project_las_analysis_receipt;
create trigger trg_project_las_live_receipt_contract
before insert on project_las_analysis_receipt
for each row execute function enforce_project_las_live_receipt_contract();

-- Provider summaries are private machine observations, not signed human Case
-- reviews. Receipts and supplier bill items remain separate immutable facts.
create table if not exists project_las_analysis_result (
  id uuid primary key default gen_random_uuid(),
  receipt_id uuid not null unique,
  project_id uuid not null,
  video_id uuid not null,
  final_summary text not null check (char_length(final_summary) between 1 and 250000),
  summary_sha256 text not null check (summary_sha256 ~ '^[0-9a-f]{64}$'),
  provenance text not null default 'provider_machine_only'
    check (provenance='provider_machine_only'),
  created_at timestamptz not null default now(),
  foreign key (receipt_id,project_id,video_id)
    references project_las_analysis_receipt(id,project_id,video_id) on delete restrict
);
create or replace function enforce_project_las_result_binding()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from project_las_analysis_receipt receipt
    where receipt.id=new.receipt_id and receipt.project_id=new.project_id
      and receipt.video_id=new.video_id and receipt.status='completed'
  ) or new.summary_sha256 <> encode(sha256(convert_to(new.final_summary,'UTF8')),'hex') then
    raise exception 'LAS machine summary must match completed private receipt';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_result_binding on project_las_analysis_result;
create trigger trg_project_las_result_binding
before insert on project_las_analysis_result
for each row execute function enforce_project_las_result_binding();
drop trigger if exists trg_project_las_result_no_change on project_las_analysis_result;
create trigger trg_project_las_result_no_change
before update or delete on project_las_analysis_result
for each row execute function reject_project_las_receipt_change();
