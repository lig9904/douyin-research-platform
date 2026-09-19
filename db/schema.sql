-- Douyin Research Platform V1
-- PostgreSQL initial schema. Fields will be refined after V0 interface verification.

create extension if not exists pgcrypto;

create table if not exists source_account (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  platform text not null default 'douyin',
  platform_account_id text not null,
  nickname text,
  profile_url text,
  follower_count bigint,
  raw_profile jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (provider, platform, platform_account_id)
);

create table if not exists source_video (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  platform text not null default 'douyin',
  platform_video_id text not null,
  account_id uuid references source_account(id),
  title text,
  description text,
  source_url text,
  published_at timestamptz,
  duration_ms integer,
  raw_payload jsonb,
  availability_status text not null default 'available',
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (provider, platform, platform_video_id)
);

create table if not exists discovery_event (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  source_type text not null,
  source_key text,
  discovered_at timestamptz not null default now(),
  rank_value numeric,
  rule_version text,
  metadata jsonb
);

create table if not exists metric_snapshot (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  captured_at timestamptz not null default now(),
  play_count bigint,
  like_count bigint,
  comment_count bigint,
  share_count bigint,
  collect_count bigint,
  author_follower_count bigint,
  raw_metrics jsonb,
  unique (video_id, captured_at)
);

create table if not exists video_score (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  score_type text not null,
  score numeric not null,
  rule_version text not null,
  components jsonb not null default '{}'::jsonb,
  calculated_at timestamptz not null default now()
);

create table if not exists video_comment (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  platform_comment_id text,
  text_content text,
  like_count bigint,
  published_at timestamptz,
  raw_payload jsonb,
  captured_at timestamptz not null default now()
);

create table if not exists transcript (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  language text,
  text_content text not null,
  segments jsonb,
  created_at timestamptz not null default now(),
  unique(video_id, provider)
);

create table if not exists analysis_run (
  id uuid primary key default gen_random_uuid(),
  video_id uuid references source_video(id) on delete cascade,
  analysis_type text not null,
  analysis_level text not null,
  status text not null default 'completed',
  model text,
  prompt_version text,
  schema_version text,
  input_refs jsonb,
  output jsonb,
  cost_amount numeric(14,6),
  cost_currency text default 'CNY',
  created_at timestamptz not null default now()
);

create table if not exists human_annotation (
  id uuid primary key default gen_random_uuid(),
  video_id uuid references source_video(id) on delete cascade,
  actor text,
  annotation_type text not null,
  value jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists collection (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  description text,
  created_by text,
  created_at timestamptz not null default now()
);

create table if not exists collection_item (
  collection_id uuid not null references collection(id) on delete cascade,
  video_id uuid not null references source_video(id) on delete cascade,
  note text,
  added_at timestamptz not null default now(),
  primary key (collection_id, video_id)
);

create table if not exists external_api_call (
  id bigserial primary key,
  provider text not null,
  endpoint_key text not null,
  request_fingerprint text,
  status text not null,
  http_status integer,
  cached boolean not null default false,
  estimated_cost numeric(14,6),
  cost_currency text default 'CNY',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  metadata jsonb
);

create index if not exists idx_video_published_at on source_video(published_at desc);
create index if not exists idx_video_account on source_video(account_id);
create index if not exists idx_discovery_video_time on discovery_event(video_id, discovered_at desc);
create index if not exists idx_metric_video_time on metric_snapshot(video_id, captured_at desc);
create index if not exists idx_comment_video on video_comment(video_id);
create index if not exists idx_analysis_video_time on analysis_run(video_id, created_at desc);
create index if not exists idx_video_title_lower on source_video(lower(title));
