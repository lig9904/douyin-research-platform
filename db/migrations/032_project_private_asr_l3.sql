-- Project-private ASR/L3 is deliberately separate from canonical transcript,
-- analysis_run and research_task_cost.  The same public video may be reviewed
-- and analysed by multiple projects, but neither approval, transcript, result
-- nor cost may be silently reused across the project boundary.

create table if not exists project_asr_media_review (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  asset_id uuid not null,
  review_version text not null check (char_length(review_version) between 1 and 80),
  media_fingerprint text not null check (media_fingerprint ~ '^[0-9a-f]{64}$'),
  delivery_origin text not null check (char_length(delivery_origin) between 1 and 120),
  identity_source text not null check (identity_source = 'windmill_end_user_email_allowlist_v1'),
  -- This is a human confirmation of the exact delivery scope.  Worker/model
  -- code must never synthesize it or update it after approval.
  review_statement jsonb not null default '{}'::jsonb
    check (jsonb_typeof(review_statement) = 'object' and pg_column_size(review_statement) <= 8192),
  status text not null default 'draft' check (status in ('draft', 'approved', 'revoked')),
  reviewed_by text check (reviewed_by is null or (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by))),
  reviewed_at timestamptz,
  revoked_by text check (revoked_by is null or (char_length(revoked_by) between 3 and 254 and revoked_by = lower(revoked_by))),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  unique (project_id, asset_id, review_version),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (asset_id, video_id)
    references media_asset(id, video_id) on delete restrict,
  check (
    (status = 'draft' and reviewed_by is null and reviewed_at is null and revoked_by is null and revoked_at is null)
    or (status = 'approved' and reviewed_by is not null and reviewed_at is not null and revoked_by is null and revoked_at is null)
    or (status = 'revoked' and revoked_by is not null and revoked_at is not null
        and ((reviewed_by is null and reviewed_at is null) or (reviewed_by is not null and reviewed_at is not null)))
  )
);
create index if not exists idx_project_asr_media_review_active
  on project_asr_media_review(project_id, video_id, reviewed_at desc)
  where status = 'approved';

create or replace function enforce_project_asr_media_review_lifecycle()
returns trigger language plpgsql as $$
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
    new.project_id, new.video_id, new.asset_id, new.review_version, new.media_fingerprint,
    new.delivery_origin, new.identity_source, new.review_statement, new.reviewed_by, new.reviewed_at
  ) is distinct from (
    old.project_id, old.video_id, old.asset_id, old.review_version, old.media_fingerprint,
    old.delivery_origin, old.identity_source, old.review_statement, old.reviewed_by, old.reviewed_at
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

  if new.status = 'approved' then
    if new.reviewed_by is null then
      raise exception 'approved project_asr_media_review requires reviewed_by';
    end if;
    new.reviewed_at := coalesce(new.reviewed_at, clock_timestamp());
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
drop trigger if exists trg_project_asr_media_review_lifecycle on project_asr_media_review;
create trigger trg_project_asr_media_review_lifecycle
before insert or update on project_asr_media_review
for each row execute function enforce_project_asr_media_review_lifecycle();

create or replace function reject_project_asr_media_review_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'project_asr_media_review must be revoked, not deleted';
end;
$$;
drop trigger if exists trg_project_asr_media_review_no_delete on project_asr_media_review;
create trigger trg_project_asr_media_review_no_delete
before delete on project_asr_media_review
for each row execute function reject_project_asr_media_review_delete();

create table if not exists project_research_task_cost (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  task_key text not null unique check (char_length(task_key) between 1 and 512),
  task_type text not null check (task_type in ('asr_transcription', 'l3_structured_research')),
  task_version text not null check (char_length(task_version) between 1 and 160),
  status text not null check (status in ('completed', 'failed', 'cancelled')),
  input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
  output_fingerprint text check (output_fingerprint is null or output_fingerprint ~ '^[0-9a-f]{64}$'),
  api_cost numeric(14,6) check (api_cost >= 0),
  asr_cost numeric(14,6) check (asr_cost >= 0),
  llm_cost numeric(14,6) check (llm_cost >= 0),
  total_cost numeric(14,6) generated always as (
    case when api_cost is null or asr_cost is null or llm_cost is null then null
      else api_cost + asr_cost + llm_cost end
  ) stored,
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  cost_basis text not null check (cost_basis in ('actual', 'estimated', 'mixed', 'unknown')),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict
);
create index if not exists idx_project_research_task_cost_project_video_time
  on project_research_task_cost(project_id, video_id, created_at desc);

create table if not exists project_asr_execution_job (
  id uuid primary key default gen_random_uuid(),
  task_key text not null unique check (char_length(task_key) between 1 and 512),
  project_id uuid not null,
  video_id uuid not null,
  media_review_id uuid not null,
  reviewed_asset_id uuid not null,
  review_version text not null check (char_length(review_version) between 1 and 80),
  media_fingerprint text not null check (media_fingerprint ~ '^[0-9a-f]{64}$'),
  provider text not null check (char_length(provider) between 1 and 120),
  model_id text not null check (char_length(model_id) between 1 and 160),
  model_revision text not null check (char_length(model_revision) between 1 and 160),
  engine_version text not null check (char_length(engine_version) between 1 and 160),
  source_fingerprint text not null check (source_fingerprint ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('queued', 'submitting', 'submitted', 'running', 'completed', 'failed', 'cancelled')),
  provider_task_ref text,
  submission_count integer not null default 0 check (submission_count between 0 and 1),
  poll_count integer not null default 0 check (poll_count >= 0),
  estimated_api_cost numeric(14,6) check (estimated_api_cost >= 0),
  estimated_asr_cost numeric(14,6) check (estimated_asr_cost >= 0),
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  task_cost_id uuid unique,
  error_code text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (media_review_id, project_id, video_id)
    references project_asr_media_review(id, project_id, video_id) on delete restrict,
  foreign key (reviewed_asset_id, video_id)
    references media_asset(id, video_id) on delete restrict,
  foreign key (task_cost_id, project_id, video_id)
    references project_research_task_cost(id, project_id, video_id) on delete restrict
);
create index if not exists idx_project_asr_execution_job_project_video_time
  on project_asr_execution_job(project_id, video_id, created_at desc);

create or replace function enforce_project_asr_execution_job_approval()
returns trigger language plpgsql as $$
declare review_row project_asr_media_review%rowtype;
begin
  -- Lock the approval while changing into a dispatchable state so revocation
  -- cannot interleave with this state transition.  The worker must still read
  -- it again immediately before Provider HTTP; this trigger cannot cover work
  -- performed after the database transaction has committed.
  if new.status in ('submitting', 'submitted', 'running', 'completed') then
    select * into review_row from project_asr_media_review
      where id=new.media_review_id and project_id=new.project_id and video_id=new.video_id
      for update;
    if not found or review_row.status <> 'approved'
       or review_row.review_version <> new.review_version
       or review_row.media_fingerprint <> new.media_fingerprint
       or review_row.asset_id <> new.reviewed_asset_id then
      raise exception 'project_asr_execution_job requires current approved project media review';
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
drop trigger if exists trg_project_asr_execution_job_approval on project_asr_execution_job;
create trigger trg_project_asr_execution_job_approval
before insert or update on project_asr_execution_job
for each row execute function enforce_project_asr_execution_job_approval();

create table if not exists project_transcript (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  execution_job_id uuid not null unique,
  media_review_id uuid not null,
  asr_provider text not null,
  model_id text,
  model_revision text,
  engine_version text,
  language text,
  text_content text not null check (char_length(text_content) > 0),
  text_fingerprint text not null check (text_fingerprint ~ '^[0-9a-f]{64}$'),
  segments jsonb,
  audio_duration_ms bigint check (audio_duration_ms is null or audio_duration_ms >= 0),
  task_cost_id uuid unique,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (execution_job_id, project_id, video_id)
    references project_asr_execution_job(id, project_id, video_id) on delete restrict,
  foreign key (media_review_id, project_id, video_id)
    references project_asr_media_review(id, project_id, video_id) on delete restrict,
  foreign key (task_cost_id, project_id, video_id)
    references project_research_task_cost(id, project_id, video_id) on delete restrict
);
create index if not exists idx_project_transcript_project_video_time
  on project_transcript(project_id, video_id, created_at desc);

create or replace function enforce_project_transcript_completed_job()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from project_asr_execution_job job
    where job.id=new.execution_job_id and job.project_id=new.project_id and job.video_id=new.video_id
      and job.media_review_id=new.media_review_id and job.status='completed'
  ) then
    raise exception 'project_transcript requires completed local ASR execution job';
  end if;
  if new.task_cost_id is not null and not exists (
    select 1 from project_asr_execution_job job
    where job.id=new.execution_job_id and job.project_id=new.project_id and job.video_id=new.video_id
      and job.task_cost_id=new.task_cost_id
  ) then
    raise exception 'project_transcript task cost must be the local ASR job cost';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_transcript_completed_job on project_transcript;
create trigger trg_project_transcript_completed_job
before insert on project_transcript
for each row execute function enforce_project_transcript_completed_job();
create or replace function reject_project_transcript_change()
returns trigger language plpgsql as $$ begin raise exception 'project_transcript is immutable'; end; $$;
drop trigger if exists trg_project_transcript_immutable on project_transcript;
create trigger trg_project_transcript_immutable before update or delete on project_transcript
for each row execute function reject_project_transcript_change();

create table if not exists project_l3_privacy_review (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  transcript_id uuid not null,
  review_version text not null check (char_length(review_version) between 1 and 80),
  evidence_fingerprint text not null check (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
  evidence_manifest jsonb not null default '{}'::jsonb
    check (jsonb_typeof(evidence_manifest) = 'object' and pg_column_size(evidence_manifest) <= 16384),
  status text not null default 'draft' check (status in ('draft', 'approved', 'revoked')),
  reviewed_by text check (reviewed_by is null or (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by))),
  reviewed_at timestamptz,
  revoked_by text check (revoked_by is null or (char_length(revoked_by) between 3 and 254 and revoked_by = lower(revoked_by))),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  unique (project_id, video_id, review_version, evidence_fingerprint),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (transcript_id, project_id, video_id)
    references project_transcript(id, project_id, video_id) on delete restrict,
  check (
    (status = 'draft' and reviewed_by is null and reviewed_at is null and revoked_by is null and revoked_at is null)
    or (status = 'approved' and reviewed_by is not null and reviewed_at is not null and revoked_by is null and revoked_at is null)
    or (status = 'revoked' and revoked_by is not null and revoked_at is not null
        and ((reviewed_by is null and reviewed_at is null) or (reviewed_by is not null and reviewed_at is not null)))
  )
);
create index if not exists idx_project_l3_privacy_review_active
  on project_l3_privacy_review(project_id, video_id, reviewed_at desc) where status='approved';

create or replace function enforce_project_l3_privacy_review_lifecycle()
returns trigger language plpgsql as $$
begin
  if tg_op='INSERT' then
    if new.status <> 'draft' or new.reviewed_by is not null or new.reviewed_at is not null
       or new.revoked_by is not null or new.revoked_at is not null then
      raise exception 'project_l3_privacy_review must begin as draft';
    end if;
  elsif old.status='draft' then
    if new.status not in ('draft','approved','revoked') then raise exception 'draft project_l3_privacy_review may only become approved or revoked'; end if;
  elsif old.status='approved' then
    if new.status <> 'revoked' then raise exception 'approved project_l3_privacy_review may only become revoked'; end if;
  else
    raise exception 'revoked project_l3_privacy_review is immutable';
  end if;
  if tg_op='UPDATE' and old.status <> 'draft' and (
    new.project_id,new.video_id,new.transcript_id,new.review_version,new.evidence_fingerprint,
    new.evidence_manifest,new.reviewed_by,new.reviewed_at
  ) is distinct from (
    old.project_id,old.video_id,old.transcript_id,old.review_version,old.evidence_fingerprint,
    old.evidence_manifest,old.reviewed_by,old.reviewed_at
  ) then raise exception 'approved project_l3_privacy_review content is immutable'; end if;
  if new.status='approved' then
    if new.reviewed_by is null then raise exception 'approved project_l3_privacy_review requires reviewed_by'; end if;
    new.reviewed_at:=coalesce(new.reviewed_at,clock_timestamp()); new.revoked_by:=null; new.revoked_at:=null;
  elsif new.status='revoked' then
    if new.revoked_by is null then raise exception 'revoked project_l3_privacy_review requires revoked_by'; end if;
    new.revoked_at:=coalesce(new.revoked_at,clock_timestamp());
    if tg_op='INSERT' or old.status='draft' then new.reviewed_by:=null; new.reviewed_at:=null; end if;
  elsif tg_op='INSERT' or old.status='draft' then
    new.reviewed_by:=null; new.reviewed_at:=null; new.revoked_by:=null; new.revoked_at:=null;
  end if;
  new.updated_at:=clock_timestamp(); return new;
end;
$$;
drop trigger if exists trg_project_l3_privacy_review_lifecycle on project_l3_privacy_review;
create trigger trg_project_l3_privacy_review_lifecycle before insert or update on project_l3_privacy_review
for each row execute function enforce_project_l3_privacy_review_lifecycle();
create or replace function reject_project_l3_privacy_review_delete()
returns trigger language plpgsql as $$ begin raise exception 'project_l3_privacy_review must be revoked, not deleted'; end; $$;
drop trigger if exists trg_project_l3_privacy_review_no_delete on project_l3_privacy_review;
create trigger trg_project_l3_privacy_review_no_delete before delete on project_l3_privacy_review
for each row execute function reject_project_l3_privacy_review_delete();

create table if not exists project_l3_execution_job (
  id uuid primary key default gen_random_uuid(),
  task_key text not null unique check (char_length(task_key) between 1 and 512),
  project_id uuid not null,
  video_id uuid not null,
  privacy_review_id uuid not null,
  review_version text not null check (char_length(review_version) between 1 and 80),
  evidence_fingerprint text not null check (evidence_fingerprint ~ '^[0-9a-f]{64}$'),
  provider text not null check (char_length(provider) between 1 and 120),
  model_id text not null check (char_length(model_id) between 1 and 160),
  model_revision text not null check (char_length(model_revision) between 1 and 160),
  prompt_version text not null check (char_length(prompt_version) between 1 and 160),
  schema_version text not null check (char_length(schema_version) between 1 and 160),
  input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  attempt_count integer not null default 0 check (attempt_count between 0 and 1),
  estimated_llm_cost numeric(14,6) check (estimated_llm_cost >= 0),
  cost_currency text not null check (char_length(cost_currency) between 1 and 12),
  task_cost_id uuid unique,
  error_code text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (id, project_id, video_id),
  foreign key (project_id, video_id) references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (privacy_review_id, project_id, video_id) references project_l3_privacy_review(id,project_id,video_id) on delete restrict,
  foreign key (task_cost_id, project_id, video_id) references project_research_task_cost(id,project_id,video_id) on delete restrict
);
create index if not exists idx_project_l3_execution_job_project_video_time on project_l3_execution_job(project_id,video_id,created_at desc);

create or replace function enforce_project_l3_execution_job_approval()
returns trigger language plpgsql as $$
declare review_row project_l3_privacy_review%rowtype;
begin
  if new.status in ('running','completed') then
    -- Same transaction-level serialization as ASR.  The worker repeats this
    -- check immediately before Provider HTTP to close the post-commit window.
    select * into review_row from project_l3_privacy_review
      where id=new.privacy_review_id and project_id=new.project_id and video_id=new.video_id for update;
    if not found or review_row.status<>'approved' or review_row.review_version<>new.review_version
       or review_row.evidence_fingerprint<>new.evidence_fingerprint then
      raise exception 'project_l3_execution_job requires current approved project privacy review';
    end if;
  end if;
  if tg_op='UPDATE' and old.project_id is distinct from new.project_id then
    raise exception 'project_l3_execution_job project_id is immutable';
  end if;
  if new.task_cost_id is not null and not exists (
    select 1 from project_research_task_cost cost
    where cost.id=new.task_cost_id and cost.project_id=new.project_id and cost.video_id=new.video_id
      and cost.task_key=new.task_key and cost.task_type='l3_structured_research'
  ) then
    raise exception 'project_l3_execution_job task cost must be matching local L3 cost';
  end if;
  new.updated_at:=clock_timestamp(); return new;
end;
$$;
drop trigger if exists trg_project_l3_execution_job_approval on project_l3_execution_job;
create trigger trg_project_l3_execution_job_approval before insert or update on project_l3_execution_job
for each row execute function enforce_project_l3_execution_job_approval();

create table if not exists project_l3_analysis_result (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  execution_job_id uuid not null unique,
  privacy_review_id uuid not null,
  analysis_type text not null default 'l3_structured_research' check (analysis_type='l3_structured_research'),
  output jsonb not null check (jsonb_typeof(output)='object'),
  output_fingerprint text not null check (output_fingerprint ~ '^[0-9a-f]{64}$'),
  task_cost_id uuid unique,
  created_at timestamptz not null default now(),
  unique (id,project_id,video_id),
  foreign key (project_id,video_id) references project_video_inclusion(project_id,video_id) on delete restrict,
  foreign key (execution_job_id,project_id,video_id) references project_l3_execution_job(id,project_id,video_id) on delete restrict,
  foreign key (privacy_review_id,project_id,video_id) references project_l3_privacy_review(id,project_id,video_id) on delete restrict,
  foreign key (task_cost_id,project_id,video_id) references project_research_task_cost(id,project_id,video_id) on delete restrict
);
create index if not exists idx_project_l3_analysis_result_project_video_time on project_l3_analysis_result(project_id,video_id,created_at desc);

create or replace function enforce_project_l3_analysis_result_completed_job()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from project_l3_execution_job job
    where job.id=new.execution_job_id and job.project_id=new.project_id and job.video_id=new.video_id
      and job.privacy_review_id=new.privacy_review_id and job.status='completed'
  ) then raise exception 'project_l3_analysis_result requires completed local L3 execution job'; end if;
  if new.task_cost_id is not null and not exists (
    select 1 from project_l3_execution_job job
    where job.id=new.execution_job_id and job.project_id=new.project_id and job.video_id=new.video_id
      and job.task_cost_id=new.task_cost_id
  ) then raise exception 'project_l3_analysis_result task cost must be the local L3 job cost'; end if;
  return new;
end;
$$;
drop trigger if exists trg_project_l3_analysis_result_completed_job on project_l3_analysis_result;
create trigger trg_project_l3_analysis_result_completed_job before insert on project_l3_analysis_result
for each row execute function enforce_project_l3_analysis_result_completed_job();
create or replace function reject_project_l3_analysis_result_change()
returns trigger language plpgsql as $$ begin raise exception 'project_l3_analysis_result is immutable'; end; $$;
drop trigger if exists trg_project_l3_analysis_result_immutable on project_l3_analysis_result;
create trigger trg_project_l3_analysis_result_immutable before update or delete on project_l3_analysis_result
for each row execute function reject_project_l3_analysis_result_change();

comment on table project_asr_media_review is
  'Project-local human approval for sending one exact media fingerprint to ASR. Approval may be revoked but never reused cross-project.';
comment on table project_transcript is
  'Project-private ASR result. Canonical transcript is not a fallback or cache for this table.';
comment on table project_l3_privacy_review is
  'Project-local approval for one exact L3 evidence bundle; approval may be revoked before dispatch.';
comment on table project_l3_analysis_result is
  'Project-private L3 result. Cross-project result sharing requires a separate explicit grant, never an implicit join.';
