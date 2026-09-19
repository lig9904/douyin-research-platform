-- Multi-platform Content Research Platform V1
-- Canonical business schema. External provider payloads are stored separately.
-- Fields will be refined after V0 interface verification.

create extension if not exists pgcrypto;

-- Platform registry is the product-level source of truth for platform dimensions.
-- A platform can exist in the UI before a production provider is connected.
create table if not exists platform_registry (
  platform_key text primary key,
  display_name text not null,
  enabled boolean not null default false,
  provider_status text not null default 'planned',
  sort_order integer not null default 100,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into platform_registry(platform_key, display_name, enabled, provider_status, sort_order)
values
  ('douyin', '抖音', true, 'active', 10),
  ('kuaishou', '快手', false, 'planned', 20),
  ('wechat_channels', '视频号', false, 'planned', 30),
  ('xiaohongshu', '小红书', false, 'planned', 40),
  ('bilibili', 'B站', false, 'planned', 50),
  ('weibo', '微博', false, 'planned', 60)
on conflict(platform_key) do update set
  display_name=excluded.display_name,
  sort_order=excluded.sort_order;

-- Canonical account identity: provider-independent.
create table if not exists source_account (
  id uuid primary key default gen_random_uuid(),
  platform text not null references platform_registry(platform_key),
  platform_account_id text not null,
  nickname text,
  profile_url text,
  bio text,
  location_text text,
  account_type text,
  certification_type text,
  research_level smallint not null default 0,
  monitoring_status text not null default 'untracked',
  monitoring_priority numeric,
  next_due_at timestamptz,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (platform, platform_account_id)
);

create table if not exists account_metric_snapshot (
  id bigserial primary key,
  account_id uuid not null references source_account(id) on delete cascade,
  provider text not null,
  source_endpoint text,
  observation_key text,
  captured_at timestamptz not null default now(),
  follower_count bigint,
  following_count bigint,
  total_favorited bigint,
  video_count bigint,
  raw_metrics jsonb
);

create table if not exists account_tag (
  id bigserial primary key,
  account_id uuid not null references source_account(id) on delete cascade,
  tag_type text not null,
  tag_value text not null,
  source text not null default 'manual',
  confidence numeric,
  created_at timestamptz not null default now(),
  unique(account_id, tag_type, tag_value)
);

create index if not exists idx_account_tag_lookup
  on account_tag(tag_type, tag_value);

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
  platform text not null references platform_registry(platform_key),
  platform_video_id text not null,
  account_id uuid references source_account(id),
  title text,
  description text,
  source_url text,
  published_at timestamptz,
  duration_ms integer,
  availability_status text not null default 'available',
  research_level smallint not null default 0,
  monitoring_status text not null default 'observe',
  monitoring_priority numeric,
  next_due_at timestamptz,
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
  platform text not null references platform_registry(platform_key),
  signal_type text not null,
  provider_signal_id text,
  signal_key text not null,
  title text,
  description text,
  category_key text,
  city_code text,
  research_level smallint not null default 0,
  monitoring_status text not null default 'untracked',
  monitoring_priority numeric,
  next_due_at timestamptz,
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
  observation_key text not null,
  discovered_at timestamptz not null default now(),
  rank_value numeric,
  rule_version text,
  metadata jsonb,
  unique(observation_key)
);

create table if not exists metric_snapshot (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  source_endpoint text,
  observation_key text,
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
  source_endpoint text,
  platform_comment_id text,
  parent_platform_comment_id text,
  text_content text,
  like_count bigint,
  reply_count bigint,
  sample_reason text not null default 'top',
  raw_ref text,
  raw_payload jsonb,
  published_at timestamptz,
  captured_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  observation_count bigint not null default 0
);

create unique index if not exists uq_video_comment_provider_video_id
  on video_comment(provider, video_id, platform_comment_id)
  where platform_comment_id is not null;

create index if not exists idx_comment_video_seen
  on video_comment(video_id, last_seen_at desc);

create index if not exists idx_comment_parent
  on video_comment(provider, video_id, parent_platform_comment_id)
  where parent_platform_comment_id is not null;

create table if not exists video_comment_observation (
  id bigserial primary key,
  comment_id uuid not null references video_comment(id) on delete cascade,
  observation_key text not null unique,
  provider text not null,
  source_endpoint text not null,
  request_fingerprint text not null,
  observed_at timestamptz not null,
  like_count bigint,
  reply_count bigint,
  raw_ref text,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_comment_observation_time
  on video_comment_observation(comment_id, observed_at desc);


create table if not exists video_comment_feature_snapshot (
  id bigserial primary key,
  video_id uuid not null references source_video(id) on delete cascade,
  feature_version text not null,
  evidence_fingerprint text not null,
  calculated_at timestamptz not null default now(),
  sampled_comment_count integer not null check (sampled_comment_count >= 0),
  root_comment_count integer not null check (root_comment_count >= 0),
  sampled_reply_count integer not null check (sampled_reply_count >= 0),
  source_observation_count integer not null check (source_observation_count >= 0),
  text_present_count integer not null check (text_present_count >= 0),
  question_text_count integer not null check (question_text_count >= 0),
  like_known_count integer not null check (like_known_count >= 0),
  like_sum bigint,
  like_median numeric,
  reply_known_count integer not null check (reply_known_count >= 0),
  reply_sum bigint,
  reply_median numeric,
  mean_text_length numeric,
  eligible_text_count integer check (eligible_text_count >= 0),
  normalized_unique_text_count integer check (normalized_unique_text_count >= 0),
  duplicate_text_count integer check (duplicate_text_count >= 0),
  duplicate_group_count integer check (duplicate_group_count >= 0),
  max_duplicate_group_size integer check (max_duplicate_group_size >= 0),
  url_text_count integer check (url_text_count >= 0),
  mention_text_count integer check (mention_text_count >= 0),
  emoji_only_text_count integer check (emoji_only_text_count >= 0),
  repeated_char_text_count integer check (repeated_char_text_count >= 0),
  short_text_count integer check (short_text_count >= 0),
  template_like_text_count integer check (template_like_text_count >= 0),
  char_bigram_count integer check (char_bigram_count >= 0),
  unique_char_bigram_count integer check (unique_char_bigram_count >= 0),
  top_char_bigram_count integer check (top_char_bigram_count >= 0),
  top_char_bigram_share numeric check (
    top_char_bigram_share is null or top_char_bigram_share between 0 and 1
  ),
  metadata jsonb not null default '{}'::jsonb,
  unique(video_id, feature_version, evidence_fingerprint)
);

create index if not exists idx_comment_feature_video_time
  on video_comment_feature_snapshot(video_id, calculated_at desc, id desc);

-- Low-cost visual preprocessing. Images themselves are temporary in V1;
-- only deterministic metadata, timestamps and OCR output are persisted.
create table if not exists visual_probe (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  source_provider text,
  source_fingerprint text not null,
  probe_version text not null,
  duration_ms bigint,
  width integer,
  height integer,
  fps numeric,
  frame_count bigint,
  video_codec text,
  audio_present boolean,
  audio_codec text,
  bitrate bigint,
  scene_threshold numeric,
  scene_change_count integer,
  hook_scene_change_count integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(video_id, source_fingerprint, probe_version)
);

create table if not exists visual_frame_event (
  id bigserial primary key,
  probe_id uuid not null references visual_probe(id) on delete cascade,
  frame_type text not null,
  timestamp_ms bigint not null,
  scene_score numeric,
  frame_hash text,
  selected_for_visual boolean not null default false,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_visual_frame_probe_time
  on visual_frame_event(probe_id, timestamp_ms);

create table if not exists ocr_run (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  source_fingerprint text not null,
  engine text not null,
  model_det text,
  model_rec text,
  engine_version text,
  sampling_profile text not null,
  resize_profile text,
  stage text not null,
  status text not null default 'completed',
  frame_count integer,
  detected_text_frames integer,
  unique_text_chars integer,
  mean_confidence numeric,
  processing_ms bigint,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists ocr_segment (
  id bigserial primary key,
  run_id uuid not null references ocr_run(id) on delete cascade,
  start_ms bigint not null,
  end_ms bigint not null,
  text_content text not null,
  normalized_text text,
  confidence numeric,
  bbox jsonb,
  source_frame_count integer not null default 1,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_ocr_run_video_time
  on ocr_run(video_id, created_at desc);
create index if not exists idx_ocr_segment_run_time
  on ocr_segment(run_id, start_ms);

create table if not exists transcript (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  asr_provider text not null,
  model_id text,
  model_revision text,
  engine_version text,
  language text,
  text_content text not null,
  segments jsonb,
  hotword_version text,
  source_provider text,
  source_fingerprint text,
  audio_duration_ms bigint,
  quality_status text not null default 'unreviewed',
  cost_amount numeric(14,6),
  cost_currency text default 'CNY',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_transcript_video_time
  on transcript(video_id, created_at desc);

create unique index if not exists uq_transcript_version
  on transcript(
    video_id,
    asr_provider,
    coalesce(model_id, ''),
    coalesce(model_revision, ''),
    coalesce(hotword_version, ''),
    coalesce(source_fingerprint, '')
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
  platform text references platform_registry(platform_key),
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
  platform text not null references platform_registry(platform_key),
  endpoint_key text not null,
  request_fingerprint text,
  requested_at timestamptz not null default now(),
  http_status integer,
  response_code text,
  response_body jsonb,
  provider_request_id text,
  expires_at timestamptz
);

-- Business-level pipeline history. Windmill job history is operational, not the long-term research audit trail.
create table if not exists pipeline_run (
  id uuid primary key default gen_random_uuid(),
  run_type text not null,
  run_version text,
  platform text references platform_registry(platform_key),
  status text not null default 'running',
  triggered_by text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  input_count bigint,
  output_count bigint,
  promoted_l1_count bigint,
  promoted_l2_count bigint,
  promoted_l3_count bigint,
  api_cost numeric(14,6) not null default 0,
  asr_cost numeric(14,6) not null default 0,
  llm_cost numeric(14,6) not null default 0,
  cost_currency text default 'CNY',
  summary jsonb not null default '{}'::jsonb,
  error_summary jsonb
);

create table if not exists pipeline_run_item (
  run_id uuid not null references pipeline_run(id) on delete cascade,
  entity_type text not null,
  entity_id uuid not null,
  stage text,
  outcome text,
  reason_code text,
  metadata jsonb not null default '{}'::jsonb,
  primary key (run_id, entity_type, entity_id)
);

create index if not exists idx_pipeline_run_time
  on pipeline_run(started_at desc);

create table if not exists daily_research_quota (
  quota_date date not null,
  platform text not null references platform_registry(platform_key),
  quota_key text not null,
  max_items integer not null check (max_items >= 0),
  used_items integer not null default 0
    check (used_items >= 0 and used_items <= max_items),
  updated_at timestamptz not null default now(),
  primary key (quota_date, platform, quota_key)
);

create table if not exists research_promotion_batch (
  id uuid primary key,
  pipeline_run_id uuid not null unique
    references pipeline_run(id) on delete cascade,
  source_run_id uuid not null references pipeline_run(id),
  quota_date date not null,
  platform text not null references platform_registry(platform_key),
  quota_key text not null,
  target_level smallint not null check (target_level between 1 and 3),
  rule_version text not null,
  requested_top_n integer not null check (requested_top_n > 0),
  min_score numeric not null check (min_score between 0 and 100),
  candidate_fingerprint text not null,
  selected_count integer not null check (selected_count >= 0),
  created_at timestamptz not null default now(),
  unique (
    source_run_id, quota_date, rule_version,
    requested_top_n, min_score, candidate_fingerprint
  )
);

create table if not exists research_promotion_decision (
  batch_id uuid not null
    references research_promotion_batch(id) on delete cascade,
  video_id uuid not null references source_video(id) on delete cascade,
  quota_date date not null,
  target_level smallint not null check (target_level between 1 and 3),
  candidate_rank integer check (candidate_rank > 0),
  score numeric,
  outcome text not null check (outcome in ('selected', 'skipped')),
  reason_code text not null,
  metadata jsonb not null default '{}'::jsonb,
  primary key (batch_id, video_id)
);

create unique index if not exists uq_promotion_selected_video_day
  on research_promotion_decision(quota_date, target_level, video_id)
  where outcome='selected';

create index if not exists idx_promotion_batch_source
  on research_promotion_batch(source_run_id, created_at desc);

create table if not exists external_api_call (
  id bigserial primary key,
  provider text not null,
  platform text not null references platform_registry(platform_key),
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
create unique index if not exists uq_account_metric_observation
  on account_metric_snapshot(observation_key)
  where observation_key is not null;
create index if not exists idx_provider_account_time on provider_account_snapshot(account_id, provider, captured_at desc);
create index if not exists idx_video_published_at on source_video(published_at desc);
create index if not exists idx_video_platform_published
  on source_video(platform, published_at desc);
create index if not exists idx_account_platform_seen
  on source_account(platform, last_seen_at desc);
create index if not exists idx_account_monitoring
  on source_account(platform, monitoring_status, research_level);
create index if not exists idx_video_account on source_video(account_id);
create index if not exists idx_provider_video_time on provider_video_snapshot(video_id, provider, captured_at desc);
create index if not exists idx_signal_type_seen on external_signal(signal_type, last_seen_at desc);
create index if not exists idx_signal_monitoring
  on external_signal(platform, monitoring_status, research_level, last_seen_at desc);
create index if not exists idx_signal_snapshot_time on signal_snapshot(signal_id, captured_at desc);
create index if not exists idx_discovery_video_time on discovery_event(video_id, discovered_at desc);
create index if not exists idx_metric_video_time on metric_snapshot(video_id, captured_at desc);
create unique index if not exists uq_metric_observation
  on metric_snapshot(observation_key)
  where observation_key is not null;
create index if not exists idx_video_monitor_due
  on source_video(monitoring_status, next_due_at)
  where monitoring_status <> 'stopped';
create index if not exists idx_comment_video on video_comment(video_id);
create index if not exists idx_analysis_video_time on analysis_run(video_id, created_at desc);
create index if not exists idx_analysis_signal_time on analysis_run(signal_id, created_at desc);
create index if not exists idx_video_title_lower on source_video(lower(title));
create index if not exists idx_api_response_fingerprint on external_api_response(provider, endpoint_key, request_fingerprint, requested_at desc);
create index if not exists idx_api_call_platform_time
  on external_api_call(platform, started_at desc);
create index if not exists idx_api_response_platform_time
  on external_api_response(platform, requested_at desc);
