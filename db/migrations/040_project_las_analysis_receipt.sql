-- Project-private whole-video analysis receipts are distinct from human Case
-- reviews and from supplier bills. An estimate is never evidence of payment.
create table if not exists project_las_analysis_attempt (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  asset_id uuid not null,
  asset_sha256 text not null check (asset_sha256 ~ '^[0-9a-f]{64}$'),
  task_key text not null check (char_length(task_key) between 1 and 512),
  attempt_no integer not null check (attempt_no > 0),
  provider text not null check (char_length(provider) between 1 and 120),
  account_scope text not null check (char_length(account_scope) between 1 and 160),
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  provider_task_ref text check (provider_task_ref is null or char_length(provider_task_ref) between 1 and 512),
  submit_job_ref text check (submit_job_ref is null or char_length(submit_job_ref) between 1 and 512),
  status text not null check (status in ('prepared','submitting','submitted','running',
                                         'completed','failed','cancelled','unknown')),
  submission_count integer not null default 0 check (submission_count between 0 and 1),
  authorization_ref text not null check (char_length(authorization_ref) between 1 and 512),
  authorized_by text not null check (char_length(authorized_by) between 3 and 254),
  record_mode text not null check (record_mode in ('live_pre_dispatch','historical_backfill')),
  input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
  error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (task_key,attempt_no),
  unique (id,project_id,video_id),
  unique (provider,provider_task_ref),
  foreign key (project_id,video_id)
    references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (asset_id,video_id)
    references media_asset(id,video_id) on delete restrict,
  check (status not in ('submitted','running','completed') or
         (submission_count=1 and provider_task_ref is not null))
);
create index if not exists idx_project_las_attempt_project_time
  on project_las_analysis_attempt(project_id,created_at desc);

create table if not exists project_las_analysis_receipt (
  id uuid primary key default gen_random_uuid(),
  attempt_id uuid not null unique,
  project_id uuid not null,
  video_id uuid not null,
  asset_id uuid not null,
  asset_sha256 text not null check (asset_sha256 ~ '^[0-9a-f]{64}$'),
  task_key text not null check (char_length(task_key) between 1 and 512),
  attempt_no integer not null check (attempt_no > 0),
  provider text not null check (char_length(provider) between 1 and 120),
  account_scope text not null check (char_length(account_scope) between 1 and 160),
  provider_task_ref text not null check (char_length(provider_task_ref) between 1 and 512),
  submit_job_ref text not null check (char_length(submit_job_ref) between 1 and 512),
  completion_job_ref text not null check (char_length(completion_job_ref) between 1 and 512),
  model_id text not null check (char_length(model_id) between 1 and 160),
  operator_id text not null check (char_length(operator_id) between 1 and 160),
  operator_version text not null check (char_length(operator_version) between 1 and 160),
  template_id text not null check (char_length(template_id) between 1 and 160),
  -- Unknown is an open attempt, not an immutable terminal receipt: a late
  -- provider completion must still be attachable to this same attempt.
  status text not null check (status in ('completed','failed','cancelled')),
  business_code integer,
  result_sha256 text check (result_sha256 is null or result_sha256 ~ '^[0-9a-f]{64}$'),
  token_usages jsonb not null default '{}'::jsonb check (jsonb_typeof(token_usages)='object'),
  estimated_cost numeric(14,6) check (estimated_cost >= 0),
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  pricing_version text check (pricing_version is null or char_length(pricing_version) between 1 and 160),
  executed_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  recorded_by text not null check (char_length(recorded_by) between 3 and 254),
  binding_basis text not null check (binding_basis in ('reviewed_submission_and_result')),
  unique (provider,provider_task_ref),
  unique (task_key,attempt_no),
  unique (id,project_id,video_id),
  foreign key (attempt_id,project_id,video_id)
    references project_las_analysis_attempt(id,project_id,video_id) on delete restrict,
  foreign key (project_id,video_id)
    references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (asset_id,video_id)
    references media_asset(id,video_id) on delete restrict,
  check (estimated_cost is null or pricing_version is not null),
  check (status <> 'completed' or (business_code=0 and result_sha256 is not null))
);
create index if not exists idx_project_las_receipt_project_time
  on project_las_analysis_receipt(project_id,executed_at desc);

create or replace function enforce_project_las_attempt_lifecycle()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from media_asset asset where asset.id=new.asset_id
      and asset.video_id=new.video_id and asset.kind='video'
      and asset.content_sha256=new.asset_sha256
  ) then
    raise exception 'LAS attempt must bind a stored video asset SHA-256';
  end if;
  if tg_op='INSERT' then
    if not exists (
      select 1 from project_video_inclusion inclusion_row
      join source_video video on video.id=inclusion_row.video_id
      where inclusion_row.project_id=new.project_id and inclusion_row.video_id=new.video_id
        and inclusion_row.status='accepted' and video.availability_status='available'
    ) then
      raise exception 'LAS attempt requires an accepted available project video';
    end if;
    if new.record_mode='live_pre_dispatch' and
       (new.status<>'prepared' or new.submission_count<>0 or
        new.provider_task_ref is not null) then
      raise exception 'live LAS attempt must be recorded before provider dispatch';
    end if;
    return new;
  end if;
  if (new.project_id,new.video_id,new.asset_id,new.asset_sha256,new.task_key,
      new.attempt_no,new.provider,new.account_scope,new.cost_currency,
      new.authorization_ref,new.authorized_by,
      new.record_mode,new.input_fingerprint,new.created_at)
     is distinct from
     (old.project_id,old.video_id,old.asset_id,old.asset_sha256,old.task_key,
      old.attempt_no,old.provider,old.account_scope,old.cost_currency,
      old.authorization_ref,old.authorized_by,
      old.record_mode,old.input_fingerprint,old.created_at) then
    raise exception 'LAS attempt identity is immutable';
  end if;
  if old.provider_task_ref is not null and
     new.provider_task_ref is distinct from old.provider_task_ref then
    raise exception 'LAS provider task reference is immutable';
  end if;
  if old.submit_job_ref is not null and
     new.submit_job_ref is distinct from old.submit_job_ref then
    raise exception 'LAS submit job reference is immutable';
  end if;
  if new.submission_count<old.submission_count or
     new.submission_count>old.submission_count+1 or
     (new.submission_count=1 and old.submission_count=0 and
      (old.status<>'prepared' or new.status<>'submitting')) then
    raise exception 'LAS submission may be claimed once before HTTP';
  end if;
  if old.status in ('completed','failed','cancelled') and new.status<>old.status then
    raise exception 'terminal LAS attempt cannot restart';
  end if;
  if old.status='prepared' and new.status not in ('prepared','submitting','cancelled') or
     old.status='submitting' and new.status not in ('submitting','submitted','unknown','failed') or
     old.status='submitted' and new.status not in ('submitted','running','completed','unknown','failed') or
     old.status='running' and new.status not in ('running','completed','unknown','failed') or
     old.status='unknown' and new.status not in ('unknown','submitted','running','completed','failed') then
    raise exception 'LAS attempt state transition is invalid';
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;
drop trigger if exists trg_project_las_attempt_lifecycle on project_las_analysis_attempt;
create trigger trg_project_las_attempt_lifecycle
before insert or update on project_las_analysis_attempt
for each row execute function enforce_project_las_attempt_lifecycle();

create or replace function enforce_project_las_receipt_asset()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from media_asset asset
    where asset.id=new.asset_id and asset.video_id=new.video_id
      and asset.kind='video' and asset.content_sha256=new.asset_sha256
  ) then
    raise exception 'LAS receipt must bind the reviewed video asset SHA-256';
  end if;
  if not exists (
    select 1 from project_las_analysis_attempt attempt
    where attempt.id=new.attempt_id and attempt.project_id=new.project_id
      and attempt.video_id=new.video_id and attempt.asset_id=new.asset_id
      and attempt.asset_sha256=new.asset_sha256 and attempt.task_key=new.task_key
      and attempt.attempt_no=new.attempt_no and attempt.provider=new.provider
      and attempt.account_scope=new.account_scope
      and attempt.cost_currency=new.cost_currency
      and attempt.provider_task_ref=new.provider_task_ref
      and attempt.submit_job_ref=new.submit_job_ref and attempt.status=new.status
  ) then
    raise exception 'LAS receipt must match its durable provider attempt';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_receipt_asset on project_las_analysis_receipt;
create trigger trg_project_las_receipt_asset
before insert on project_las_analysis_receipt
for each row execute function enforce_project_las_receipt_asset();

create or replace function reject_project_las_receipt_change()
returns trigger language plpgsql as $$
begin
  raise exception 'project_las_analysis_receipt is append-only';
end;
$$;
drop trigger if exists trg_project_las_receipt_no_change on project_las_analysis_receipt;
create trigger trg_project_las_receipt_no_change
before update or delete on project_las_analysis_receipt
for each row execute function reject_project_las_receipt_change();
drop trigger if exists trg_project_las_attempt_no_delete on project_las_analysis_attempt;
create trigger trg_project_las_attempt_no_delete
before delete on project_las_analysis_attempt
for each row execute function reject_project_las_receipt_change();

-- Only a verified supplier line item may populate an actual charge. A later
-- correction is another signed item, never an overwrite of the estimate.
create table if not exists project_las_supplier_bill_item (
  id uuid primary key default gen_random_uuid(),
  receipt_id uuid not null,
  project_id uuid not null,
  video_id uuid not null,
  provider text not null check (char_length(provider) between 1 and 120),
  account_scope text not null check (char_length(account_scope) between 1 and 160),
  provider_task_ref text not null check (char_length(provider_task_ref) between 1 and 512),
  supplier_bill_ref text not null check (char_length(supplier_bill_ref) between 1 and 512),
  supplier_line_ref text not null check (char_length(supplier_line_ref) between 1 and 512),
  reconciliation_version integer not null check (reconciliation_version > 0),
  signed_amount numeric(14,6) not null,
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  reconciliation_status text not null default 'partial'
    check (reconciliation_status in ('partial','final')),
  billing_date date not null,
  billing_timezone text not null check (char_length(billing_timezone) between 1 and 80),
  evidence_source text not null check (evidence_source in ('volcengine_bill_api','volcengine_bill_export')),
  supplier_evidence_sha256 text not null check (supplier_evidence_sha256 ~ '^[0-9a-f]{64}$'),
  verified_by text not null check (char_length(verified_by) between 3 and 254),
  verified_at timestamptz not null default now(),
  unique (provider,account_scope,supplier_line_ref),
  unique (receipt_id,reconciliation_version),
  foreign key (receipt_id,project_id,video_id)
    references project_las_analysis_receipt(id,project_id,video_id) on delete restrict
);
create index if not exists idx_project_las_bill_project_date
  on project_las_supplier_bill_item(project_id,billing_date desc);
create or replace function enforce_project_las_bill_currency()
returns trigger language plpgsql as $$
declare latest_version integer;
begin
  if not exists (
    select 1 from project_las_analysis_receipt receipt
    where receipt.id=new.receipt_id and receipt.project_id=new.project_id
      and receipt.video_id=new.video_id and receipt.cost_currency=new.cost_currency
      and receipt.provider=new.provider and receipt.account_scope=new.account_scope
      and receipt.provider_task_ref=new.provider_task_ref
    for update
  ) then
    raise exception 'LAS supplier bill must match its project receipt, account, task and currency';
  end if;
  select coalesce(max(item.reconciliation_version),0) into latest_version
    from project_las_supplier_bill_item item where item.receipt_id=new.receipt_id;
  if new.reconciliation_version<>latest_version+1 then
    raise exception 'LAS supplier bill reconciliation version must be sequential';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_las_bill_currency on project_las_supplier_bill_item;
create trigger trg_project_las_bill_currency
before insert on project_las_supplier_bill_item
for each row execute function enforce_project_las_bill_currency();
drop trigger if exists trg_project_las_bill_no_change on project_las_supplier_bill_item;
create trigger trg_project_las_bill_no_change
before update or delete on project_las_supplier_bill_item
for each row execute function reject_project_las_receipt_change();
