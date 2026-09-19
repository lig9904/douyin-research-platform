-- Douyin Research Platform V1
-- Canonical business schema. External provider payloads are stored separately.
-- Fields will be refined after V0 interface verification.

create extension if not exists pgcrypto;

-- Canonical account identity: provider-independent.
create table if not exists source_account (
  id uuid primary key default gen_random_uuid(),
  platform text not null default 'douyin',
  platform_account_id text not null,
  nickname text,
  profile_url text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (platform, platform_account_id)
);

create table if not exists account_metric_snapshot (
  id bigserial primary key,
  account_id uuid not null references source_account(id) on delete cascade,
  provider text not null,
  captured_at timestamptz not null default now(),
  follower_count bigint,
  following_count bigint,
  total_favorited bigint,
  video_count bigint,
  raw_metrics jsonb
);

create table if not exists provider_account_snapshot (
  id bigserial primary key,
  account_id uuid not null references source_account(id) on delete cascade,
  provider text not null,
  provider_object_id text,
  captured_at timestamptz not null default now(),
  raw_payload jsonb not null
);

-- Canonical video identity: provider-independent.
create table if not exists source_video (
  id uuid primary key default gen_random_uuid(),
  platform text not null default 'douyin',
  platform_video_id text not null,
  account_id uuid references source_account(id),
  title text,
  description text,
  source_url text,
  published_at timestamptz,
  duration_ms integer,
  availability_status text not null default 'available',
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (platform, platform_video_id)
);

create table if not exists provider_video_snapshot (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  provider_object_id text,
  captured_at timestamptz not null default now(),
  raw_payload jsonb not null
);

-- Provider lineage for canonical entities. Supports source replacement and provider-exit dry-runs.
create table if not exists provider_entity_lineage (
  provider text not null,
  entity_type text not null check (entity_type in ('account', 'video')),
  entity_id uuid not null,
  source_mode text not null default 'api',
  first_observed_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  observation_count bigint not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  primary key (provider, entity_type, entity_id)
);

-- Non-video signals: rising hot topics, search terms, topic lists, city hot topics,
-- creative topics/keywords, etc.
create table if not exists external_signal (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  platform text not null default 'douyin',
  signal_type text not null,
  provider_signal_id text,
  signal_key text not null,
  title text,
  category_key text,
  city_code text,
  raw_payload jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (provider, platform, signal_type, signal_key)
);

create table if not exists signal_snapshot (
  id bigserial primary key,
  signal_id uuid not null references external_signal(id) on delete cascade,
  captured_at timestamptz not null default now(),
  rank_value numeric,
  rank_change numeric,
  heat_value numeric,
  value_json jsonb,
  raw_payload jsonb
);

create table if not exists signal_video_link (
  signal_id uuid not null references external_signal(id) on delete cascade,
  video_id uuid not null references source_video(id) on delete cascade,
  relation_type text not null default 'related',
  observed_at timestamptz not null default now(),
  metadata jsonb,
  primary key (signal_id, video_id, relation_type)
);

-- Why/how a video entered the research pool.
create table if not exists discovery_event (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
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
  provider text not null,
  captured_at timestamptz not null default now(),
  play_count bigint,
  like_count bigint,
  comment_count bigint,
  share_count bigint,
  collect_count bigint,
  author_follower_count bigint,
  raw_metrics jsonb
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
  provider text not null,
  platform_comment_id text,
  text_content text,
  like_count bigint,
  published_at timestamptz,
  raw_payload jsonb,
  captured_at timestamptz not null default now()
);

create unique index if not exists uq_video_comment_provider_id
  on video_comment(provider, platform_comment_id)
  where platform_comment_id is not null;

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
  signal_id uuid references external_signal(id) on delete cascade,
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
  created_at timestamptz not null default now(),
  check ((video_id is not null)::int + (signal_id is not null)::int <= 1)
);

create table if not exists human_annotation (
  id uuid primary key default gen_random_uuid(),
  video_id uuid references source_video(id) on delete cascade,
  signal_id uuid references external_signal(id) on delete cascade,
  actor text,
  annotation_type text not null,
  value jsonb not null,
  created_at timestamptz not null default now(),
  check ((video_id is not null)::int + (signal_id is not null)::int <= 1)
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
  video_id uuid references source_video(id) on delete cascade,
  signal_id uuid references external_signal(id) on delete cascade,
  note text,
  added_at timestamptz not null default now(),
  check ((video_id is not null)::int + (signal_id is not null)::int = 1)
);

create unique index if not exists uq_collection_video
  on collection_item(collection_id, video_id) where video_id is not null;
create unique index if not exists uq_collection_signal
  on collection_item(collection_id, signal_id) where signal_id is not null;

-- Runtime registry for provider capabilities, prices and V0 verification state.
-- Business code reads this instead of hard-coding endpoint prices/batch sizes.
create table if not exists api_endpoint_registry (
  provider text not null,
  endpoint_key text not null,
  sdk_method text,
  http_method text,
  endpoint_path text,
  access_mode text not null default 'sdk',
  lifecycle_status text not null default 'candidate',
  analysis_level text,
  production_ready boolean not null default false,
  max_batch_size integer,
  max_pages integer,
  cache_ttl_seconds integer,
  unit_cost numeric(14,6),
  cost_currency text default 'USD',
  price_source text,
  sdk_version_verified text,
  last_verified_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  primary key (provider, endpoint_key)
);

-- Raw API responses make normalization replayable without repurchasing source data.
create table if not exists external_api_response (
  id bigserial primary key,
  provider text not null,
  endpoint_key text not null,
  request_fingerprint text,
  requested_at timestamptz not null default now(),
  http_status integer,
  response_code text,
  response_body jsonb,
  provider_request_id text,
  expires_at timestamptz
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
  actual_cost numeric(14,6),
  cost_currency text default 'USD',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  metadata jsonb
);

-- Community Edition does not provide global concurrency/rate limits.
-- These tables support a DB-coordinated token bucket / daily budget gate.
create table if not exists api_rate_bucket (
  provider text not null,
  bucket_key text not null,
  window_started_at timestamptz not null,
  window_seconds integer not null,
  used_count integer not null default 0,
  max_count integer not null,
  updated_at timestamptz not null default now(),
  primary key (provider, bucket_key, window_started_at)
);

create table if not exists daily_budget (
  budget_date date not null,
  provider text not null,
  budget_key text not null default 'default',
  max_cost numeric(14,6),
  max_requests integer,
  spent_cost numeric(14,6) not null default 0,
  used_requests integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (budget_date, provider, budget_key)
);

create index if not exists idx_account_metric_time on account_metric_snapshot(account_id, captured_at desc);
create index if not exists idx_provider_account_time on provider_account_snapshot(account_id, provider, captured_at desc);
create index if not exists idx_video_published_at on source_video(published_at desc);
create index if not exists idx_video_account on source_video(account_id);
create index if not exists idx_provider_video_time on provider_video_snapshot(video_id, provider, captured_at desc);
create index if not exists idx_signal_type_seen on external_signal(signal_type, last_seen_at desc);
create index if not exists idx_signal_snapshot_time on signal_snapshot(signal_id, captured_at desc);
create index if not exists idx_discovery_video_time on discovery_event(video_id, discovered_at desc);
create index if not exists idx_metric_video_time on metric_snapshot(video_id, captured_at desc);
create index if not exists idx_comment_video on video_comment(video_id);
create index if not exists idx_analysis_video_time on analysis_run(video_id, created_at desc);
create index if not exists idx_analysis_signal_time on analysis_run(signal_id, created_at desc);
create index if not exists idx_video_title_lower on source_video(lower(title));
create index if not exists idx_api_response_fingerprint on external_api_response(provider, endpoint_key, request_fingerprint, requested_at desc);
