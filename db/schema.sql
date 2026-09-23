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
    coalesce(engine_version, ''),
    coalesce(language, ''),
    coalesce(hotword_version, ''),
    coalesce(source_provider, ''),
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

create unique index if not exists uq_l3_privacy_review_idempotency
  on human_annotation ((value->>'idempotency_key'))
  where annotation_type='l3_privacy_review'
    and value->>'reviewer_identity_source'='windmill_end_user_email_allowlist_v1';

create unique index if not exists uq_l3_privacy_review_candidate
  on human_annotation (
    video_id,
    (value->>'version'),
    (value->>'evidence_fingerprint')
  )
  where annotation_type='l3_privacy_review'
    and value->>'reviewer_identity_source'='windmill_end_user_email_allowlist_v1';

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
  account_id uuid references source_account(id) on delete cascade,
  signal_id uuid references external_signal(id) on delete cascade,
  note text,
  added_at timestamptz not null default now(),
  constraint collection_item_one_target check (
    (video_id is not null)::int
    + (account_id is not null)::int
    + (signal_id is not null)::int = 1
  )
);

create unique index if not exists uq_collection_video
  on collection_item(collection_id, video_id) where video_id is not null;
create unique index if not exists uq_collection_signal
  on collection_item(collection_id, signal_id) where signal_id is not null;
create unique index if not exists uq_collection_account
  on collection_item(collection_id, account_id) where account_id is not null;
create unique index if not exists uq_collection_owner_name
  on collection(created_by, lower(name));

create table if not exists saved_research_filter (
  id uuid primary key default gen_random_uuid(),
  actor text not null,
  view_key text not null,
  name text not null,
  filters jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint saved_research_filter_view_check
    check (view_key in ('videos', 'accounts', 'hotspots')),
  constraint saved_research_filter_name_check
    check (char_length(name) between 1 and 80),
  unique(actor, view_key, name)
);

create table if not exists research_user_action (
  idempotency_key uuid primary key,
  actor text not null,
  action_type text not null,
  payload_hash text not null,
  outcome jsonb,
  created_at timestamptz not null default now()
);

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

create table if not exists research_task_cost (
  id uuid primary key default gen_random_uuid(),
  task_key text not null unique,
  task_type text not null,
  task_version text not null,
  pipeline_run_id uuid references pipeline_run(id) on delete set null,
  video_id uuid references source_video(id) on delete cascade,
  status text not null
    check (status in ('completed', 'failed', 'cancelled')),
  input_fingerprint text not null,
  output_fingerprint text,
  api_cost numeric(14,6) check (api_cost >= 0),
  asr_cost numeric(14,6) check (asr_cost >= 0),
  llm_cost numeric(14,6) check (llm_cost >= 0),
  total_cost numeric(14,6) generated always as (
    case
      when api_cost is null or asr_cost is null or llm_cost is null
        then null
      else api_cost + asr_cost + llm_cost
    end
  ) stored,
  cost_currency text not null,
  cost_basis text not null
    check (cost_basis in ('actual', 'estimated', 'mixed', 'unknown')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_research_task_cost_video_time
  on research_task_cost(video_id, created_at desc);

create table if not exists asr_execution_job (
  id uuid primary key,
  task_key text not null unique,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  model_id text not null,
  model_revision text not null,
  engine_version text not null,
  source_fingerprint text not null,
  media_ref_fingerprint text not null,
  status text not null check (
    status in ('submitting', 'submitted', 'running', 'completed', 'failed')
  ),
  provider_task_ref text,
  submission_count integer not null default 0
    check (submission_count between 0 and 1),
  poll_count integer not null default 0 check (poll_count >= 0),
  estimated_api_cost numeric(14,6) check (estimated_api_cost >= 0),
  estimated_asr_cost numeric(14,6) check (estimated_asr_cost >= 0),
  cost_currency text not null,
  budget_date date not null,
  budget_key text not null,
  task_cost_id uuid unique
    references research_task_cost(id) on delete set null,
  error_code text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_asr_execution_video_time
  on asr_execution_job(video_id, created_at desc);

alter table transcript
  add column if not exists task_cost_id uuid
    references research_task_cost(id) on delete cascade;

create unique index if not exists uq_transcript_task_cost
  on transcript(task_cost_id)
  where task_cost_id is not null;

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
  source_run_id uuid not null references pipeline_run(id) on delete cascade,
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
    source_run_id, quota_date, quota_key, rule_version,
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
  unknown_price_requests integer not null default 0 check (unknown_price_requests >= 0),
  cost_currency text not null default 'USD',
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


-- Versioned L3 result/task-cost linkage. Kept after both base tables so the
-- foreign key is valid on a clean schema bootstrap.
alter table analysis_run
  add column if not exists model_revision text;
alter table analysis_run
  add column if not exists input_fingerprint text;
alter table analysis_run
  add column if not exists output_fingerprint text;
alter table analysis_run
  add column if not exists task_cost_id uuid
    references research_task_cost(id) on delete cascade;

create unique index if not exists uq_analysis_run_task_cost
  on analysis_run(task_cost_id)
  where task_cost_id is not null;

create unique index if not exists uq_l3_analysis_version_input
  on analysis_run(
    video_id,
    analysis_type,
    coalesce(model, ''),
    coalesce(model_revision, ''),
    coalesce(prompt_version, ''),
    coalesce(schema_version, ''),
    coalesce(input_fingerprint, '')
  )
  where analysis_level='L3' and status='completed';


create table if not exists l3_execution_job (
  id uuid primary key,
  task_key text not null unique,
  video_id uuid not null references source_video(id) on delete cascade,
  provider text not null,
  model_id text not null,
  model_revision text not null,
  prompt_version text not null,
  schema_version text not null,
  input_fingerprint text not null,
  status text not null check (
    status in ('running', 'completed', 'failed')
  ),
  attempt_count integer not null default 0
    check (attempt_count between 0 and 1),
  estimated_llm_cost numeric(14,6)
    check (estimated_llm_cost >= 0),
  cost_currency text not null,
  budget_date date not null,
  budget_key text not null,
  task_cost_id uuid unique
    references research_task_cost(id) on delete set null,
  error_code text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_l3_execution_video_time
  on l3_execution_job(video_id, created_at desc);


-- Supplier-reported daily totals are snapshots, not a per-request bill allocation.
create table if not exists supplier_daily_spend (
  provider text not null,
  account_scope text not null default 'default',
  billing_date date not null,
  cost_currency text not null,
  billing_timezone text not null,
  bill_scope_key text not null default 'account',
  scope_kind text not null default 'account_total'
    check (scope_kind in ('account_total', 'product_subset')),
  scope_label text not null default '账户总费用',
  total_cost numeric not null,
  balance_cost numeric check (balance_cost >= 0),
  free_credit_cost numeric check (free_credit_cost >= 0),
  payable_cost numeric,
  paid_cost numeric,
  unpaid_cost numeric,
  total_requests integer,
  paid_requests integer,
  billing_finality text not null default 'preliminary'
    check (billing_finality in ('preliminary', 'final')),
  source_warning text,
  fetched_at timestamptz not null,
  check ((total_requests is null and paid_requests is null) or
         (total_requests is not null and paid_requests is not null and
          total_requests >= 0 and paid_requests >= 0 and paid_requests <= total_requests)),
  check ((scope_kind = 'account_total' and bill_scope_key = 'account') or
         (scope_kind = 'product_subset' and bill_scope_key like 'product:%' and length(bill_scope_key) > 8)),
  check ((payable_cost is null and paid_cost is null and unpaid_cost is null) or
         (payable_cost is not null and paid_cost is not null and unpaid_cost is not null)),
  check (payable_cost is null or total_cost = payable_cost),
  check (source_warning is null or length(source_warning) between 1 and 1000),
  primary key (provider, account_scope, bill_scope_key, billing_date, cost_currency)
);

comment on table supplier_daily_spend is
  'Supplier-reported daily bill snapshots by explicit account or product scope. Not per-request allocation.';

-- User-authored research briefs are the control plane for collection scope.
create table if not exists research_brief (
  id uuid primary key default gen_random_uuid(),
  owner_actor text not null,
  name text not null,
  platform text not null,
  source_type text not null,
  target text,
  time_window_hours integer not null,
  max_items integer not null,
  depth text not null,
  cadence_hours integer,
  status text not null default 'draft',
  config_version integer not null default 1,
  next_due_at timestamptz,
  last_dispatched_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint research_brief_owner_check check (char_length(owner_actor) between 3 and 254),
  constraint research_brief_name_check check (char_length(name) between 1 and 80),
  constraint research_brief_platform_check check (platform = 'douyin'),
  constraint research_brief_source_check check (source_type in ('low_fan', 'keyword', 'account')),
  constraint research_brief_target_check check (
    (source_type = 'low_fan' and target is null) or
    (source_type in ('keyword', 'account') and target is not null and
     char_length(target) between 1 and 120)
  ),
  constraint research_brief_window_check check (
    time_window_hours in (24,72,168,720) and
    (source_type <> 'low_fan' or time_window_hours in (24,72,168))
  ),
  constraint research_brief_item_check check (
    max_items between 1 and 20 and
    (source_type <> 'low_fan' or max_items <= 5) and
    (depth not in ('media','review_ready') or max_items <= 5)
  ),
  constraint research_brief_depth_check check (depth in ('metadata','comments','media','review_ready')),
  constraint research_brief_cadence_check check (cadence_hours is null or cadence_hours in (6,12,24)),
  constraint research_brief_status_check check (status in ('draft','active','paused','archived')),
  constraint research_brief_version_check check (config_version >= 1),
  constraint research_brief_due_check check ((status='active' and next_due_at is not null) or status <> 'active')
);
create unique index if not exists uq_research_brief_owner_name
  on research_brief(owner_actor, lower(name)) where status <> 'archived';
create index if not exists idx_research_brief_due
  on research_brief(next_due_at, id) where status='active';

create table if not exists research_brief_run (
  id uuid primary key default gen_random_uuid(),
  brief_id uuid not null references research_brief(id) on delete cascade,
  brief_version integer not null check (brief_version >= 1),
  dispatch_key text not null unique,
  trigger_kind text not null check (trigger_kind in ('manual','schedule')),
  triggered_by text not null,
  status text not null check (status in ('running','success','failed','deferred')),
  source_run_id uuid references pipeline_run(id) on delete set null,
  config_snapshot jsonb not null,
  summary jsonb,
  error_code text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists idx_research_brief_run_time
  on research_brief_run(brief_id, started_at desc);

comment on table research_brief is
  'User-authored, bounded research scope. Activation authorizes scheduled discovery only; ASR/L3 remain separately review-gated.';
comment on table research_brief_run is
  'Immutable configuration snapshot and safe aggregate result for one research brief execution.';

-- Only confirmed private objects belong here. No delivery URLs or credentials.
create table if not exists media_asset (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references source_video(id) on delete cascade,
  kind text not null check (kind in ('video', 'audio', 'image')),
  storage_location text not null,
  bucket text not null,
  object_key text not null,
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint not null check (size_bytes > 0),
  content_type text not null,
  source_response_id bigint references external_api_response(id) on delete set null,
  parent_asset_id uuid,
  created_at timestamptz not null default now(),
  unique (id, video_id),
  unique (video_id, kind, storage_location, bucket, content_sha256),
  foreign key (parent_asset_id, video_id) references media_asset(id, video_id),
  check (object_key = 'sha256/' || left(content_sha256, 2) || '/' || content_sha256)
);

create index if not exists idx_media_asset_video_time
  on media_asset(video_id, kind, created_at desc);

comment on table media_asset is
  'Confirmed private media objects; content may be shared between videos. URLs are generated on demand.';

-- Human approval is bound to immutable asset metadata and delivery origin,
-- not to an expiring signed URL. Scheduled workers only consume these rows.
create table if not exists asr_media_review (
  asset_id uuid not null references media_asset(id) on delete cascade,
  review_version text not null,
  asset_fingerprint text not null check (asset_fingerprint ~ '^[0-9a-f]{64}$'),
  delivery_origin text not null,
  reviewed_by text not null,
  identity_source text not null check (identity_source='windmill_end_user_email_allowlist_v1'),
  approved_at timestamptz not null default now(),
  active boolean not null default true,
  primary key (asset_id, review_version)
);

-- One display record per video; raw snapshots remain immutable.
-- Source priority is a presentation policy, not a claim that other zeros are invalid.
create or replace view merged_video_metric as
with candidates as (
  select m.video_id, m.id, m.captured_at, f.field, f.value,
    case when m.source_endpoint='douyin.billboard.low_fan' then 'billboard'
      when m.source_endpoint in ('douyin.app.multi_video_v2','douyin.app.multi_video',
        'douyin.app.one_video','douyin.app.video_statistics','douyin.app.multi_video_statistics') then 'detail'
      else 'other' end as source_kind,
    case when f.field in ('play_count','author_follower_count')
      and m.source_endpoint='douyin.billboard.low_fan' then 0 else 1 end as source_priority
  from metric_snapshot m
  cross join lateral (values
    ('play_count',m.play_count), ('like_count',m.like_count),
    ('comment_count',m.comment_count), ('share_count',m.share_count),
    ('collect_count',m.collect_count), ('author_follower_count',m.author_follower_count)
  ) f(field,value)
  where f.value is not null
), ranked as (
  select *, row_number() over (
    partition by video_id,field order by source_priority,captured_at desc,id desc
  ) as position from candidates
), selected as (
  select * from ranked where position=1
)
select video_id,
  max(value) filter(where field='play_count') as play_count,
  max(value) filter(where field='like_count') as like_count,
  max(value) filter(where field='comment_count') as comment_count,
  max(value) filter(where field='share_count') as share_count,
  max(value) filter(where field='collect_count') as collect_count,
  max(value) filter(where field='author_follower_count') as author_follower_count,
  max(captured_at) as captured_at,
  min(captured_at) as oldest_field_captured_at,
  'merged'::text as metric_source_kind,
  jsonb_object_agg(field,jsonb_build_object(
    'source_kind',source_kind,'captured_at',captured_at
  )) as metric_provenance
from selected group by video_id;

-- Preserve access for the existing default local reviewer, without creating roles.
do $$
begin
  if exists(select 1 from pg_roles where rolname='l3_local_reviewer') then
    if has_table_privilege('l3_local_reviewer','public.metric_snapshot','SELECT') then
      grant select on public.merged_video_metric to l3_local_reviewer;
    end if;
  end if;
end $$;

-- Multi-project and multi-account foundation.
-- This migration deliberately creates no project relationships for legacy rows.
-- Canonical public identities remain in source_account; no credential material is
-- stored in these tables.

create table if not exists research_organization (
  id uuid primary key default gen_random_uuid(),
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text not null check (char_length(name) between 1 and 160),
  status text not null default 'active'
    check (status in ('active', 'suspended', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (slug)
);

create table if not exists research_project (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references research_organization(id) on delete restrict,
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text not null check (char_length(name) between 1 and 160),
  status text not null default 'draft'
    check (status in ('draft', 'active', 'paused', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, slug),
  unique (id, organization_id)
);

create table if not exists research_project_member (
  project_id uuid not null references research_project(id) on delete cascade,
  actor_id text not null check (char_length(actor_id) between 3 and 254 and actor_id = lower(actor_id)),
  role text not null check (role in ('owner', 'admin', 'researcher', 'analyst', 'viewer')),
  status text not null default 'active' check (status in ('active', 'suspended', 'revoked')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, actor_id, effective_from),
  check (effective_until is null or effective_until > effective_from)
);

create index if not exists idx_research_project_member_active
  on research_project_member(project_id, actor_id, effective_until)
  where status = 'active';
-- Supports the actor-first lookup used to resolve the latest applicable ACL.
create index if not exists idx_research_project_member_actor_project_effective
  on research_project_member(actor_id, project_id, effective_from desc);

create table if not exists research_subject (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  parent_subject_id uuid,
  name text not null check (char_length(name) between 1 and 160),
  subject_type text not null check (subject_type in (
    'destination', 'ip', 'character', 'product', 'activity', 'brand', 'account', 'topic', 'other'
  )),
  status text not null default 'active' check (status in ('active', 'archived')),
  external_ref text check (external_ref is null or char_length(external_ref) <= 512),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (parent_subject_id, project_id)
    references research_subject(id, project_id) on delete restrict
);

create unique index if not exists uq_research_subject_project_type_name
  on research_subject(project_id, subject_type, lower(name));
create index if not exists idx_research_subject_project_parent
  on research_subject(project_id, parent_subject_id);

create table if not exists project_account_relation (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  subject_id uuid,
  source_account_id uuid not null references source_account(id) on delete restrict,
  relation_type text not null check (relation_type in (
    'official', 'ip_character', 'employee_store', 'managed_matrix', 'authorized_partner',
    'unverified_partner', 'competitor', 'media_reference', 'ugc_reference', 'other'
  )),
  task_roles text[] not null check (cardinality(task_roles) > 0 and task_roles <@ array[
    'publish_channel', 'distribution_partner', 'benchmark_sample', 'comment_observer',
    'conversion_entry', 'research_reference'
  ]::text[]),
  purpose text check (purpose is null or char_length(purpose) <= 500),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  verification_status text not null default 'proposed'
    check (verification_status in ('proposed', 'pending', 'verified', 'rejected', 'revoked')),
  verified_by text check (verified_by is null or char_length(verified_by) between 3 and 254),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  unique (id, project_id, source_account_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (effective_until is null or effective_until > effective_from),
  check (verification_status <> 'verified' or verified_by is not null)
);

create unique index if not exists uq_project_account_relation_idempotency
  on project_account_relation(project_id, idempotency_key);
create index if not exists idx_project_account_relation_project_account
  on project_account_relation(project_id, source_account_id, verification_status, effective_until);
create index if not exists idx_project_account_relation_verified_cursor
  on project_account_relation(project_id, id)
  where verification_status = 'verified';
create index if not exists idx_project_account_relation_subject
  on project_account_relation(project_id, subject_id) where subject_id is not null;

create table if not exists account_group (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 160),
  group_type text not null check (group_type in ('matrix', 'campaign', 'cohort', 'other')),
  status text not null default 'active' check (status in ('active', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id)
);

create unique index if not exists uq_account_group_project_name
  on account_group(project_id, lower(name));

create table if not exists account_group_member (
  group_id uuid not null,
  project_id uuid not null,
  source_account_id uuid not null references source_account(id) on delete restrict,
  role text not null default 'member' check (role in ('owner', 'member', 'featured')),
  status text not null default 'active' check (status in ('active', 'removed')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (group_id, source_account_id, role, effective_from),
  foreign key (group_id, project_id)
    references account_group(id, project_id) on delete cascade,
  check (effective_until is null or effective_until > effective_from)
);

create index if not exists idx_account_group_member_project_account
  on account_group_member(project_id, source_account_id, effective_until)
  where status = 'active';

create table if not exists account_identity_link (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  left_account_id uuid not null references source_account(id) on delete restrict,
  right_account_id uuid not null references source_account(id) on delete restrict,
  relation_type text not null check (relation_type in ('same_brand', 'same_person', 'same_operator')),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  verification_status text not null default 'pending'
    check (verification_status in ('pending', 'verified', 'rejected', 'revoked')),
  verified_by text check (verified_by is null or char_length(verified_by) between 3 and 254),
  status text not null default 'active' check (status in ('active', 'revoked')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (left_account_id < right_account_id),
  check (effective_until is null or effective_until > effective_from),
  check (verification_status <> 'verified' or verified_by is not null),
  check ((status = 'revoked') = (revoked_at is not null))
);

create unique index if not exists uq_account_identity_link_idempotency
  on account_identity_link(idempotency_key);
create index if not exists idx_account_identity_link_verified
  on account_identity_link(left_account_id, right_account_id, effective_until)
  where status = 'active' and verification_status = 'verified';

-- An identity link is only for a cross-platform real-world identity. Account
-- relationships within one platform belong in project_account_relation/group.
create or replace function enforce_account_identity_link_cross_platform()
returns trigger language plpgsql as $$
declare
  left_platform text;
  right_platform text;
begin
  select platform into left_platform from source_account where id = new.left_account_id;
  select platform into right_platform from source_account where id = new.right_account_id;
  if left_platform is null or right_platform is null or left_platform = right_platform then
    raise exception 'account_identity_link must join two distinct platforms';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_account_identity_link_cross_platform on account_identity_link;
create trigger trg_account_identity_link_cross_platform
before insert or update of left_account_id, right_account_id on account_identity_link
for each row execute function enforce_account_identity_link_cross_platform();

create table if not exists account_authorization (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null,
  project_id uuid not null,
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  project_account_relation_id uuid not null,
  source_account_id uuid not null references source_account(id) on delete restrict,
  provider text not null check (char_length(provider) between 1 and 80),
  authorization_kind text not null check (authorization_kind in ('project_account')),
  purpose text not null check (purpose in ('read_metrics', 'analyze_content', 'publish', 'sync')),
  field_allowlist text[] not null check (cardinality(field_allowlist) > 0),
  operation_allowlist text[] not null check (cardinality(operation_allowlist) > 0),
  credential_ref text not null check (
    char_length(credential_ref) between 1 and 512
    and credential_ref ~ '^(secret|vault|windmill)://[A-Za-z0-9][A-Za-z0-9/_-]*$'
  ),
  status text not null default 'draft' check (status in ('draft', 'active', 'expired', 'revoked')),
  granted_by text not null check (char_length(granted_by) between 3 and 254),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (project_id, organization_id)
    references research_project(id, organization_id) on delete restrict,
  foreign key (project_account_relation_id, project_id, source_account_id)
    references project_account_relation(id, project_id, source_account_id) on delete restrict,
  check (effective_until is null or effective_until > effective_from),
  check ((status = 'revoked') = (revoked_at is not null)),
  check (metadata::text !~* '"(secret|token|password|api[_-]?key|cookie)"[[:space:]]*:')
);

create unique index if not exists uq_account_authorization_idempotency
  on account_authorization(project_id, idempotency_key);
create index if not exists idx_account_authorization_active
  on account_authorization(project_id, provider, purpose, effective_until)
  where status = 'active';

-- Any loss of verified business relationship must immediately make provider
-- grants beneath it unusable.  This is DB-enforced so a future adapter cannot
-- leave a still-active grant behind on a relation update.
create or replace function revoke_authorizations_for_inactive_relation()
returns trigger language plpgsql as $$
begin
  if (new.verification_status <> 'verified'
      and old.verification_status is distinct from new.verification_status)
     or old.effective_from is distinct from new.effective_from
     or old.effective_until is distinct from new.effective_until then
    update account_authorization
       set status = 'revoked',
           revoked_at = coalesce(revoked_at, now()),
           updated_at = now()
     where project_account_relation_id = new.id
       and status in ('draft', 'active');
  end if;
  return new;
end;
$$;
drop trigger if exists trg_revoke_authorizations_for_inactive_relation on project_account_relation;
create trigger trg_revoke_authorizations_for_inactive_relation
after update of verification_status, effective_from, effective_until on project_account_relation
for each row execute function revoke_authorizations_for_inactive_relation();

create or replace function enforce_authorization_relation_not_inactive()
returns trigger language plpgsql as $$
declare
  relation_status text;
  relation_from timestamptz;
  relation_until timestamptz;
begin
  if tg_op = 'UPDATE' and old.status in ('expired', 'revoked')
     and new.status in ('draft', 'active') then
    raise exception 'expired or revoked authorization cannot be reactivated';
  end if;
  if new.status = 'active' then
    select verification_status, effective_from, effective_until
      into relation_status, relation_from, relation_until
      from project_account_relation where id = new.project_account_relation_id for share;
    if relation_status is distinct from 'verified'
       or relation_from > now()
       or (relation_until is not null and relation_until <= now()) then
      raise exception 'authorization can only be active for a verified relation in its effective window';
    end if;
    if new.effective_from < relation_from
       or (relation_until is not null
           and (new.effective_until is null or new.effective_until > relation_until)) then
      raise exception 'authorization window must be within its relation window';
    end if;
  elsif new.status = 'draft' then
    select verification_status into relation_status
      from project_account_relation where id = new.project_account_relation_id for share;
    if relation_status in ('rejected', 'revoked') then
      raise exception 'authorization cannot be draft for a rejected or revoked relation';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_enforce_authorization_relation_not_inactive on account_authorization;
create trigger trg_enforce_authorization_relation_not_inactive
before insert or update of status, project_account_relation_id, effective_from, effective_until on account_authorization
for each row execute function enforce_authorization_relation_not_inactive();

-- Status alone is never a usable grant: expiry is time-driven and cannot be
-- maintained by a trigger. Provider adapters must resolve through this view.
create or replace view effective_account_authorization as
select a.* from account_authorization a
join project_account_relation r on r.id = a.project_account_relation_id
join research_project p on p.id = a.project_id
join research_organization o on o.id = a.organization_id
where a.status = 'active' and a.effective_from <= now()
  and (a.effective_until is null or a.effective_until > now())
  and r.verification_status = 'verified' and r.effective_from <= now()
  and (r.effective_until is null or r.effective_until > now())
  and p.status = 'active' and o.status = 'active';

comment on table project_account_relation is
  'Project-scoped business relationship to one canonical public source_account; does not grant provider access.';
comment on table account_authorization is
  'Authorization metadata only. credential_ref is an opaque controlled-backend reference; never store secrets, tokens, cookies or raw credentials here.';

-- Project ownership for new research control-plane records and candidate
-- inclusions. Legacy rows remain project_id NULL; canonical source tables are
-- shared observations and are never project-owned.
alter table research_brief
  add column if not exists project_id uuid references research_project(id) on delete restrict;
drop index if exists uq_research_brief_owner_name;
create unique index if not exists uq_research_brief_owner_name
  on research_brief(owner_actor, lower(name))
  where status <> 'archived' and project_id is null;
create unique index if not exists uq_research_brief_project_name
  on research_brief(project_id, lower(name))
  where status <> 'archived' and project_id is not null;
alter table research_brief
  add constraint uq_research_brief_id_project unique (id, project_id);
create index if not exists idx_research_brief_project_due
  on research_brief(project_id, next_due_at, id)
  where project_id is not null and status = 'active';

alter table pipeline_run
  add column if not exists project_id uuid references research_project(id) on delete restrict;
alter table pipeline_run
  add constraint uq_pipeline_run_id_project unique (id, project_id);
create index if not exists idx_pipeline_run_project_time
  on pipeline_run(project_id, started_at desc)
  where project_id is not null;

alter table research_brief_run
  add column if not exists project_id uuid references research_project(id) on delete restrict;
alter table research_brief_run
  add constraint uq_research_brief_run_id_project unique (id, project_id);
alter table research_brief_run
  add constraint fk_research_brief_run_project_brief
    foreign key (brief_id, project_id)
    references research_brief(id, project_id) on delete cascade;
alter table research_brief_run
  add constraint fk_research_brief_run_project_source_run
    foreign key (source_run_id, project_id)
    references pipeline_run(id, project_id) on delete set null (source_run_id);
create index if not exists idx_research_brief_run_project_time
  on research_brief_run(project_id, started_at desc)
  where project_id is not null;

create or replace function enforce_research_brief_run_project_scope()
returns trigger language plpgsql as $$
declare
  brief_project_id uuid;
  source_project_id uuid;
begin
  select project_id into brief_project_id from research_brief where id = new.brief_id;
  if brief_project_id is distinct from new.project_id then
    raise exception 'research_brief_run project_id must match research_brief project_id';
  end if;
  if new.source_run_id is not null then
    select project_id into source_project_id from pipeline_run where id = new.source_run_id;
    if source_project_id is distinct from new.project_id then
      raise exception 'research_brief_run project_id must match source pipeline_run project_id';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_research_brief_run_project_scope on research_brief_run;
create trigger trg_research_brief_run_project_scope
before insert or update of brief_id, source_run_id, project_id on research_brief_run
for each row execute function enforce_research_brief_run_project_scope();

create or replace function enforce_project_id_immutable()
returns trigger language plpgsql as $$
begin
  if old.project_id is distinct from new.project_id then
    raise exception '% project_id is immutable after insert', tg_table_name;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_research_brief_project_id_immutable on research_brief;
create trigger trg_research_brief_project_id_immutable
before update of project_id on research_brief
for each row execute function enforce_project_id_immutable();
drop trigger if exists trg_pipeline_run_project_id_immutable on pipeline_run;
create trigger trg_pipeline_run_project_id_immutable
before update of project_id on pipeline_run
for each row execute function enforce_project_id_immutable();
drop trigger if exists trg_research_brief_run_project_id_immutable on research_brief_run;
create trigger trg_research_brief_run_project_id_immutable
before update of project_id on research_brief_run
for each row execute function enforce_project_id_immutable();

create table if not exists project_video_inclusion (
  project_id uuid not null references research_project(id) on delete cascade,
  video_id uuid not null references source_video(id) on delete restrict,
  source_run_id uuid,
  brief_run_id uuid,
  source_type text not null default 'pipeline_run'
    check (source_type in ('pipeline_run', 'research_brief_run', 'manual', 'import')),
  source_ref text check (source_ref is null or char_length(source_ref) between 1 and 512),
  status text not null default 'candidate'
    check (status in ('candidate', 'shortlisted', 'accepted', 'rejected', 'archived')),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, video_id),
  foreign key (source_run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  foreign key (brief_run_id, project_id)
    references research_brief_run(id, project_id) on delete restrict,
  check (last_seen_at >= first_seen_at),
  check (
    (source_type = 'pipeline_run' and source_run_id is not null) or
    (source_type = 'research_brief_run' and brief_run_id is not null) or
    (source_type in ('manual', 'import') and source_run_id is null and brief_run_id is null)
  )
);
create index if not exists idx_project_video_inclusion_project_status_seen
  on project_video_inclusion(project_id, status, last_seen_at desc);
create index if not exists idx_project_video_inclusion_video
  on project_video_inclusion(video_id, project_id);

drop trigger if exists trg_project_video_inclusion_project_id_immutable on project_video_inclusion;
create trigger trg_project_video_inclusion_project_id_immutable
before update of project_id on project_video_inclusion
for each row execute function enforce_project_id_immutable();

comment on table project_video_inclusion is
  'Project-scoped candidate state and provenance only. source_video remains canonical and is never backfilled or project-owned.';

-- Read-only project ACL primitives. Application routes must invoke these for
-- every project-scoped record read; missing context and ambiguous membership
-- resolve to false.
create or replace function project_actor_can_read(p_project_id uuid, p_actor text)
returns boolean
language sql
stable
security invoker
as $$
  select coalesce((
    select member_row.status = 'active'
       and (member_row.effective_until is null or member_row.effective_until > now())
    from research_project project_row
    join research_organization organization_row
      on organization_row.id = project_row.organization_id
    join lateral (
      select member.status, member.effective_until
      from research_project_member member
      where member.project_id = project_row.id
        and member.actor_id = p_actor
        and member.effective_from <= now()
      order by member.effective_from desc
      limit 1
    ) member_row on true
    where project_row.id = p_project_id
      and project_row.status = 'active'
      and organization_row.status = 'active'
  ), false);
$$;

create or replace function project_video_can_read(
  p_project_id uuid,
  p_actor text,
  p_video_id uuid
)
returns boolean
language sql
stable
security invoker
as $$
  select coalesce(
    p_project_id is not null
    and p_actor is not null
    and p_video_id is not null
    and project_actor_can_read(p_project_id, p_actor)
    and exists (
      select 1
      from project_video_inclusion inclusion_row
      where inclusion_row.project_id = p_project_id
        and inclusion_row.video_id = p_video_id
        and inclusion_row.status <> 'archived'
    ),
    false
  );
$$;

comment on function project_actor_can_read(uuid, text) is
  'Fail-closed project read predicate. It resolves the latest effective member row before checking status and expiry.';
comment on function project_video_can_read(uuid, text, uuid) is
  'Fail-closed project video read predicate. Requires active organization/project/member and a non-archived project inclusion.';

-- Project collaboration is opt-in. A member of the receiving project does not
-- become a member of the source project, and no private/provider data is shared.
create table if not exists project_video_share_grant (
  id uuid primary key default gen_random_uuid(),
  source_project_id uuid not null references research_project(id) on delete restrict,
  target_project_id uuid not null references research_project(id) on delete restrict,
  scope text not null default 'public_video_evidence'
    check (scope = 'public_video_evidence'),
  status text not null default 'offered'
    check (status in ('offered', 'active', 'declined', 'revoked')),
  offered_by text not null,
  offered_at timestamptz not null default now(),
  accepted_by text,
  accepted_at timestamptz,
  closed_by text,
  closed_at timestamptz,
  effective_until timestamptz not null,
  check (source_project_id <> target_project_id),
  check (effective_until > offered_at),
  check (status <> 'active' or (accepted_by is not null and accepted_at is not null)),
  check (status <> 'offered' or (accepted_by is null and accepted_at is null)),
  check ((status in ('declined', 'revoked')) = (closed_by is not null and closed_at is not null))
);
create unique index if not exists uq_project_video_share_open
  on project_video_share_grant(source_project_id, target_project_id, scope)
  where status in ('offered', 'active');
create index if not exists idx_project_video_share_target_active
  on project_video_share_grant(target_project_id, source_project_id, effective_until)
  where status = 'active';

create table if not exists project_access_event (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  actor_id text not null,
  action text not null check (action in (
    'member_add', 'member_role_change', 'member_revoke',
    'share_offer', 'share_accept', 'share_decline', 'share_revoke'
  )),
  subject_actor_id text,
  related_project_id uuid references research_project(id) on delete restrict,
  grant_id uuid references project_video_share_grant(id) on delete restrict,
  created_at timestamptz not null default now(),
  check ((subject_actor_id is not null) <> (grant_id is not null))
);
create index if not exists idx_project_access_event_project_time
  on project_access_event(project_id, created_at desc, id);

-- The grant can be used only when both projects remain active in the same
-- active organization, the recipient is a current member, and the source has
-- deliberately included the video. No review text, media, or L3 is exposed.
create or replace function project_shared_video_can_read(
  p_source_project_id uuid, p_target_project_id uuid,
  p_actor text, p_video_id uuid
) returns boolean language sql stable security invoker as $$
  select coalesce(
    p_source_project_id is not null
    and p_target_project_id is not null
    and p_actor is not null
    and p_video_id is not null
    and project_actor_can_read(p_target_project_id, p_actor)
    and exists (
      select 1
      from research_project source_project
      join research_project target_project
        on target_project.id = p_target_project_id
       and target_project.organization_id = source_project.organization_id
      join project_video_share_grant grant_row
        on grant_row.source_project_id = source_project.id
       and grant_row.target_project_id = target_project.id
      join project_video_inclusion inclusion_row
        on inclusion_row.project_id = source_project.id
       and inclusion_row.video_id = p_video_id
      where source_project.id = p_source_project_id
        and source_project.status = 'active'
        and grant_row.status = 'active'
        and grant_row.scope = 'public_video_evidence'
        and grant_row.effective_until > now()
        and inclusion_row.status <> 'archived'
    ), false
  );
$$;
comment on function project_shared_video_can_read(uuid, uuid, text, uuid) is
  'Fail-closed, accepted cross-project grant for public video evidence only.';
revoke all on function project_shared_video_can_read(uuid, uuid, text, uuid) from public;
