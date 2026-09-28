-- 041 approvals bound a database media reference, not immutable S3 bytes.
-- Keep those historical records for display/reconciliation, but never turn
-- them into a new paid submission. A non-null S3 VersionId is required for
-- every new live attempt after this migration.
-- The release runner owns the transaction covering this migration and its
-- schema_migrations ledger row. Never commit inside this file: that would
-- split the schema change from its ledger entry. Hold the attempt table lock
-- until that outer transaction commits. If an old Worker has durably claimed
-- a request but has not resolved its Submit transaction, abort.
lock table project_las_analysis_attempt in access exclusive mode;
alter table project_las_video_review
  add column if not exists binding_scheme text not null default 'legacy_key_v1';
alter table project_las_video_review
  add column if not exists object_version_id text;
alter table project_las_video_review
  alter column binding_scheme set default 'versioned_bytes_v2';
alter table project_las_video_review
  drop constraint if exists project_las_review_binding_scheme_valid;
alter table project_las_video_review
  add constraint project_las_review_binding_scheme_valid check (
    binding_scheme in ('legacy_key_v1','versioned_bytes_v2') and
    (binding_scheme='legacy_key_v1' and object_version_id is null or
     binding_scheme='versioned_bytes_v2' and object_version_id is not null and
     char_length(object_version_id) between 1 and 512 and
     object_version_id=btrim(object_version_id) and
     lower(object_version_id)<>'null')
  );

alter table project_las_analysis_attempt
  add column if not exists object_version_id text;
alter table project_las_analysis_receipt
  add column if not exists object_version_id text;

do $$
begin
  if exists (
    select 1 from project_las_analysis_attempt
     where record_mode='live_pre_dispatch' and status='submitting'
       and object_version_id is null
  ) then
    raise exception 'unresolved legacy LAS Submit claim blocks version cutover';
  end if;
end;
$$;

-- An old prepared request has not reached the provider. It cannot inherit a
-- byte-level authorization after the fact. Claimed/submitted/unknown rows
-- are deliberately left untouched so Poll and manual reconciliation remain
-- tied to their original task IDs and may still produce legacy receipts.
update project_las_analysis_attempt
   set status='cancelled',error_code='legacy_object_version_unbound'
 where record_mode='live_pre_dispatch' and object_version_id is null
   and status='prepared';

create or replace function enforce_project_las_review_version_binding()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and
     (new.binding_scheme,new.object_version_id) is distinct from
     (old.binding_scheme,old.object_version_id) then
    raise exception 'LAS review byte version is immutable';
  end if;
  if tg_op='INSERT' and new.binding_scheme<>'versioned_bytes_v2' then
    raise exception 'new LAS review requires versioned bytes';
  end if;
  if new.status='approved' and
     (new.binding_scheme<>'versioned_bytes_v2' or
      new.object_version_id is null) and
     (tg_op='INSERT' or old.status<>'approved') then
    raise exception 'new LAS approval requires versioned bytes';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_review_version_binding on project_las_video_review;
create trigger trg_project_las_review_version_binding
before insert or update on project_las_video_review
for each row execute function enforce_project_las_review_version_binding();

create or replace function enforce_project_las_attempt_version_binding()
returns trigger language plpgsql as $$
begin
  if tg_op='UPDATE' and new.object_version_id is distinct from old.object_version_id then
    raise exception 'LAS attempt byte version is immutable';
  end if;
  if new.record_mode='live_pre_dispatch' and
     (tg_op='INSERT' or old.status='prepared' and new.status='submitting') and
     not exists (
       select 1 from project_las_video_review review
       where review.id=new.video_review_id and review.status='approved'
         and review.binding_scheme='versioned_bytes_v2'
         and review.object_version_id is not null
         and review.object_version_id=new.object_version_id
     ) then
    raise exception 'new live LAS attempt requires matching reviewed object version';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_attempt_version_binding on project_las_analysis_attempt;
create trigger trg_project_las_attempt_version_binding
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_attempt_version_binding();

create or replace function enforce_project_las_receipt_version_binding()
returns trigger language plpgsql as $$
declare
  attempt_row project_las_analysis_attempt%rowtype;
begin
  select * into attempt_row from project_las_analysis_attempt where id=new.attempt_id;
  if not found or new.object_version_id is distinct from attempt_row.object_version_id then
    raise exception 'LAS receipt byte version must match its attempt';
  end if;
  -- A pre-042 submitted task is allowed to settle against its original ID,
  -- but is explicitly labelled legacy/unproven rather than inventing a
  -- VersionId. All 042 live attempts have a concrete version by the trigger.
  return new;
end;
$$;
drop trigger if exists trg_project_las_receipt_version_binding on project_las_analysis_receipt;
create trigger trg_project_las_receipt_version_binding
before insert on project_las_analysis_receipt
for each row execute function enforce_project_las_receipt_version_binding();
