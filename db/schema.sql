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
  constraint research_brief_source_check check (source_type in ('low_fan', 'keyword', 'account', 'video_ids')),
  constraint research_brief_target_check check (
    (source_type = 'low_fan' and target is null) or
    (source_type in ('keyword', 'account') and target is not null and
     char_length(target) between 1 and 120) or
    (source_type = 'video_ids' and target is not null and
    char_length(target) between 15 and 519 and
     target ~ '^[0-9]{15,25}(,[0-9]{15,25})*$')
  ),
  constraint research_brief_window_check check (
    (time_window_hours in (24,72,168,720) or
     (source_type = 'video_ids' and time_window_hours = 0)) and
    (source_type <> 'video_ids' or time_window_hours = 0) and
    (source_type <> 'low_fan' or time_window_hours in (24,72,168))
  ),
  constraint research_brief_item_check check (
    max_items between 1 and 20 and
    (source_type <> 'low_fan' or max_items <= 5) and
    (depth not in ('media','review_ready') or max_items <= 5) and
    (source_type <> 'video_ids' or
     (max_items = array_length(string_to_array(target, ','), 1) and depth = 'metadata'))
  ),
  constraint research_brief_depth_check check (depth in ('metadata','comments','media','review_ready')),
  constraint research_brief_cadence_check check (
    (cadence_hours is null or cadence_hours in (6,12,24)) and
    (source_type <> 'video_ids' or cadence_hours is null)
  ),
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

create table if not exists research_subject_term (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  subject_id uuid not null,
  term text not null check (char_length(btrim(term)) between 1 and 160 and term = btrim(term)),
  term_type text not null check (term_type in ('alias', 'geographic_context', 'exclusion')),
  status text not null default 'active' check (status in ('active', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete cascade
);

create unique index if not exists uq_research_subject_term_active
  on research_subject_term(subject_id, term_type, lower(term)) where status='active';
create index if not exists idx_research_subject_term_project_subject
  on research_subject_term(project_id, subject_id) where status='active';

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

create table if not exists project_video_subject_relevance (
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  run_id uuid,
  decision text not null check (decision in ('pending', 'relevant', 'irrelevant')),
  decision_source text not null check (decision_source in ('rule', 'manual')),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  match_detail jsonb not null default '{}'::jsonb,
  reviewed_by text check (reviewed_by is null or char_length(reviewed_by) between 3 and 254),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, video_id, subject_id),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete cascade,
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete cascade,
  constraint fk_subject_relevance_run_project foreign key (run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  check ((decision_source = 'manual') = (reviewed_by is not null)),
  check ((decision_source = 'manual') = (reviewed_at is not null))
);

create index if not exists idx_project_video_subject_relevance_gate
  on project_video_subject_relevance(project_id, subject_id, decision, updated_at desc);

create table if not exists project_video_subject_relevance_audit (
  id bigserial primary key,
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  run_id uuid,
  event_type text not null check (event_type in ('rule_evaluated', 'manual_override')),
  actor text check (actor is null or char_length(actor) between 3 and 254),
  prior_decision text check (prior_decision is null or prior_decision in ('pending', 'relevant', 'irrelevant')),
  decision text not null check (decision in ('pending', 'relevant', 'irrelevant')),
  reason text check (reason is null or char_length(reason) between 1 and 500),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  match_detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  -- Historical audit deliberately does not reference the mutable current
  -- decision row: deleting a project relationship must not erase audit facts.
  constraint fk_subject_relevance_audit_run_project foreign key (run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict,
  check ((event_type = 'manual_override') = (actor is not null)),
  check ((event_type = 'manual_override') = (reason is not null))
);

create index if not exists idx_project_video_subject_relevance_audit_video
  on project_video_subject_relevance_audit(project_id, video_id, subject_id, created_at desc);

alter table research_brief add column if not exists subject_id uuid;
alter table research_brief add column if not exists subject_gate_status text not null default 'not_applicable';
alter table research_brief
  add constraint fk_research_brief_subject_project
  foreign key (subject_id, project_id)
  references research_subject(id, project_id) on delete restrict;
alter table research_brief
  add constraint research_brief_subject_scope_check
  check (subject_id is null or project_id is not null);
alter table research_brief
  add constraint research_brief_subject_gate_status_check
  check (subject_gate_status in ('not_applicable', 'ready', 'subject_required'));
alter table research_brief
  add constraint research_brief_exact_project_check
  check (source_type <> 'video_ids' or (project_id is not null and subject_id is not null));
create index if not exists idx_research_brief_project_subject_due
  on research_brief(project_id, subject_id, next_due_at, id)
  where project_id is not null and subject_id is not null and status='active';

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
      join source_video video
        on video.id = inclusion_row.video_id
      where source_project.id = p_source_project_id
        and source_project.status = 'active'
        and grant_row.status = 'active'
        and grant_row.scope = 'public_video_evidence'
        and grant_row.effective_until > now()
        and inclusion_row.status = 'accepted'
        and video.availability_status = 'available'
    ), false
  );
$$;
comment on function project_shared_video_can_read(uuid, uuid, text, uuid) is
  'Fail-closed, accepted cross-project grant for source-approved public video evidence only.';
revoke all on function project_shared_video_can_read(uuid, uuid, text, uuid) from public;

-- Fresh-volume bootstrap parity with migration 028.  The migration remains
-- the reviewed upgrade path for existing research databases.
-- A project-owned, human-operated loop from an accepted public reference to a
-- bounded action and non-personal outcome evidence.  It deliberately does not
-- attach to project_video_share_grant: cross-project shares are read-only
-- reference material and cannot become another project's action card.

create table if not exists project_decision_card (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  source_video_id uuid not null references source_video(id) on delete restrict,
  subject_id uuid,
  hypothesis text not null check (char_length(hypothesis) between 1 and 1200),
  reference_point text not null check (char_length(reference_point) between 1 and 1200),
  adaptation_difference text not null check (char_length(adaptation_difference) between 1 and 1200),
  owner_actor text not null check (char_length(owner_actor) between 3 and 254 and owner_actor = lower(owner_actor)),
  decision text not null check (decision in ('adopt', 'observe', 'exclude')),
  status text not null default 'draft'
    check (status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  review_conclusion text check (review_conclusion is null or char_length(review_conclusion) between 1 and 2000),
  review_evidence text check (review_evidence is null or char_length(review_evidence) between 1 and 1200),
  next_action text check (next_action is null or char_length(next_action) between 1 and 800),
  reviewed_by text check (reviewed_by is null or (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by))),
  reviewed_at timestamptz,
  review_observation_id uuid,
  review_observation_version integer check (review_observation_version is null or review_observation_version > 0),
  review_metric_snapshot jsonb,
  created_by text not null check (char_length(created_by) between 3 and 254 and created_by = lower(created_by)),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (
    status = 'draft' or status = 'reviewed' or status = 'archived'
    or (decision = 'adopt' and status in ('active', 'adopted'))
    or (decision = 'observe' and status = 'observing')
    or (decision = 'exclude' and status = 'excluded')
  ),
  check ((status = 'reviewed') = (review_conclusion is not null and review_evidence is not null and next_action is not null and reviewed_by is not null and reviewed_at is not null and review_observation_id is not null and review_observation_version is not null and review_metric_snapshot is not null))
);
create index if not exists idx_project_decision_card_project_status
  on project_decision_card(project_id, status, updated_at desc);
create index if not exists idx_project_decision_card_project_source_video
  on project_decision_card(project_id, source_video_id, updated_at desc);

-- The source must be an explicit, local project decision.  A canonical video
-- or a shared read-only video is insufficient.
create or replace function enforce_project_decision_card_accepted_source()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    where inclusion_row.project_id = new.project_id
      and inclusion_row.video_id = new.source_video_id
      and inclusion_row.status = 'accepted'
      and exists (
        select 1 from source_video video_row
        where video_row.id = new.source_video_id
          and video_row.availability_status = 'available'
      )
  ) then
    raise exception 'project_decision_card requires locally accepted project video';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_accepted_source on project_decision_card;
create trigger trg_project_decision_card_accepted_source
before insert or update of project_id, source_video_id on project_decision_card
for each row execute function enforce_project_decision_card_accepted_source();
drop trigger if exists trg_project_decision_card_project_id_immutable on project_decision_card;
create trigger trg_project_decision_card_project_id_immutable
before update of project_id on project_decision_card
for each row execute function enforce_project_id_immutable();

-- The assigned owner is a current member at assignment time.  This does not
-- attach a live FK to membership history: later membership revocation must not
-- erase or block the project's already-created historical review.
create or replace function enforce_project_decision_card_owner_member()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1 from research_project_member member
    where member.project_id = new.project_id
      and member.actor_id = new.owner_actor
      and member.effective_from <= now()
      and member.status = 'active'
      and (member.effective_until is null or member.effective_until > now())
      and not exists (
        select 1 from research_project_member newer
        where newer.project_id=member.project_id and newer.actor_id=member.actor_id
          and newer.effective_from <= now() and newer.effective_from>member.effective_from
      )
  ) then
    raise exception 'project_decision_card owner_actor must be an active project member';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_owner_member on project_decision_card;
create trigger trg_project_decision_card_owner_member
before insert or update of owner_actor, project_id on project_decision_card
for each row execute function enforce_project_decision_card_owner_member();

-- A completed review is evidence, not an editable draft.  This is enforced
-- below the application layer so a direct SQL update cannot rewrite history.
create or replace function reject_reviewed_project_decision_card_change()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' or old.status = 'reviewed' then
    raise exception 'reviewed project_decision_card is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_reviewed_immutable on project_decision_card;
create trigger trg_project_decision_card_reviewed_immutable
before update or delete on project_decision_card
for each row execute function reject_reviewed_project_decision_card_change();

create table if not exists project_decision_card_event (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  actor_id text not null check (char_length(actor_id) between 3 and 254 and actor_id = lower(actor_id)),
  action text not null check (action in ('created', 'updated', 'status_changed', 'reviewed')),
  from_status text check (from_status is null or from_status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  to_status text check (to_status is null or to_status in ('draft', 'active', 'observing', 'adopted', 'excluded', 'reviewed', 'archived')),
  changed_fields text[] not null default '{}'::text[] check (cardinality(changed_fields) <= 8),
  created_at timestamptz not null default now(),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict,
  check ((action in ('status_changed', 'reviewed')) = (from_status is not null and to_status is not null))
);
create index if not exists idx_project_decision_card_event_card_time
  on project_decision_card_event(project_id, decision_card_id, created_at desc, id);

create table if not exists project_publication_record (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  decision_card_id uuid,
  publication_date date not null,
  title text not null check (char_length(title) between 1 and 160),
  content_reference text not null check (char_length(content_reference) between 1 and 512),
  status text not null default 'published' check (status = 'published'),
  created_by text not null check (char_length(created_by) between 3 and 254 and created_by = lower(created_by)),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict
);
create index if not exists idx_project_publication_record_project_date
  on project_publication_record(project_id, publication_date desc, id);

create table if not exists project_publication_metric_observation (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  publication_id uuid not null,
  metric_date date not null,
  version integer not null check (version > 0),
  impressions bigint check (impressions is null or impressions >= 0),
  engagements bigint check (engagements is null or engagements >= 0),
  likes bigint check (likes is null or likes >= 0),
  comments bigint check (comments is null or comments >= 0),
  shares bigint check (shares is null or shares >= 0),
  follows bigint check (follows is null or follows >= 0),
  conversions bigint check (conversions is null or conversions >= 0),
  source text not null check (source in ('manual', 'imported_aggregate')),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  source_reported_at timestamptz not null,
  source_version_or_digest text not null check (char_length(source_version_or_digest) between 1 and 256),
  measurement_scope text not null check (char_length(measurement_scope) between 1 and 300),
  recorded_by text not null check (char_length(recorded_by) between 3 and 254 and recorded_by = lower(recorded_by)),
  recorded_at timestamptz not null default now(),
  unique (id, project_id),
  unique (publication_id, metric_date, version),
  foreign key (publication_id, project_id)
    references project_publication_record(id, project_id) on delete restrict,
  check (num_nonnulls(impressions, engagements, likes, comments, shares, follows, conversions) > 0)
);
create index if not exists idx_project_publication_metric_observation_project_date
  on project_publication_metric_observation(project_id, metric_date desc, publication_id, version desc);

create or replace function reject_project_publication_metric_observation_change()
returns trigger language plpgsql as $$
begin
  raise exception 'project_publication_metric_observation is append-only';
end;
$$;
drop trigger if exists trg_project_publication_metric_observation_append_only on project_publication_metric_observation;
create trigger trg_project_publication_metric_observation_append_only
before update or delete on project_publication_metric_observation
for each row execute function reject_project_publication_metric_observation_change();

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'project_decision_card'::regclass
      and conname = 'fk_project_decision_card_review_observation'
  ) then
    alter table project_decision_card
      add constraint fk_project_decision_card_review_observation
      foreign key (review_observation_id, project_id)
      references project_publication_metric_observation(id, project_id) on delete restrict;
  end if;
end $$;

create or replace function enforce_project_decision_card_review_snapshot()
returns trigger language plpgsql as $$
declare
  expected_snapshot jsonb;
begin
  if new.status = 'reviewed' and (tg_op = 'INSERT' or old.status is distinct from 'reviewed') then
    select jsonb_build_object(
      'id', metric.id, 'version', metric.version, 'metric_date', metric.metric_date,
      'impressions', metric.impressions, 'engagements', metric.engagements,
      'likes', metric.likes, 'comments', metric.comments, 'shares', metric.shares,
      'follows', metric.follows, 'conversions', metric.conversions,
      'source', metric.source, 'source_reference', metric.source_reference,
      'source_reported_at', metric.source_reported_at,
      'source_version_or_digest', metric.source_version_or_digest,
      'measurement_scope', metric.measurement_scope, 'recorded_at', metric.recorded_at
    ) into expected_snapshot
    from project_publication_metric_observation metric
    join project_publication_record publication
      on publication.id=metric.publication_id and publication.project_id=metric.project_id
    where metric.id=new.review_observation_id
      and metric.project_id=new.project_id
      and metric.version=new.review_observation_version
      and publication.decision_card_id=new.id
      and publication.status='published';
    if expected_snapshot is null or new.review_metric_snapshot is distinct from expected_snapshot then
      raise exception 'reviewed project_decision_card requires exact local observation snapshot';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_review_snapshot on project_decision_card;
create trigger trg_project_decision_card_review_snapshot
before update of status, review_observation_id, review_observation_version, review_metric_snapshot
on project_decision_card for each row execute function enforce_project_decision_card_review_snapshot();
drop trigger if exists trg_project_decision_card_review_snapshot_insert on project_decision_card;
create trigger trg_project_decision_card_review_snapshot_insert
before insert on project_decision_card
for each row execute function enforce_project_decision_card_review_snapshot();

comment on table project_decision_card is
  'Project-local action hypothesis based only on a locally accepted public video.';
comment on table project_publication_metric_observation is
  'Append-only non-personal aggregate observations. NULL means unknown, never zero; source reference/version are required evidence.';

-- Fresh-volume bootstrap parity with migration 029. The migration remains
-- the reviewed upgrade path for existing research databases.
-- A subject-scoped L1 score is an immutable project interpretation of
-- canonical public evidence. It must never be written to video_score or
-- source_video, which remain global monitoring state.

create table if not exists project_video_subject_score (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  video_id uuid not null,
  subject_id uuid not null,
  source_run_id uuid not null,
  score_type text not null default 'priority'
    check (score_type = 'priority'),
  score numeric(5,2) not null check (score >= 0 and score <= 100),
  rule_version text not null check (char_length(rule_version) between 1 and 80),
  components jsonb not null default '{}'::jsonb
    check (jsonb_typeof(components) = 'object'),
  created_at timestamptz not null default now(),
  unique (project_id, video_id, subject_id, source_run_id, score_type),
  foreign key (project_id, video_id)
    references project_video_inclusion(project_id, video_id) on delete restrict,
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  foreign key (source_run_id, project_id)
    references pipeline_run(id, project_id) on delete restrict
);
create index if not exists idx_project_video_subject_score_project_subject_score
  on project_video_subject_score(project_id, subject_id, score desc, created_at desc);
create index if not exists idx_project_video_subject_score_project_video
  on project_video_subject_score(project_id, video_id, created_at desc);

-- The Python scorer locks the current relevance row before inserting. Keep
-- the same rule below the application boundary: direct SQL cannot create an
-- actionable project score for a pending, excluded, or archived subject, a
-- rejected/archived project inclusion, or a video absent from the source run.
create or replace function enforce_project_video_subject_score_eligible()
returns trigger language plpgsql as $$
begin
  if not exists (
    select 1
    from project_video_subject_relevance relevance
    join project_video_inclusion inclusion
      on inclusion.project_id=relevance.project_id and inclusion.video_id=relevance.video_id
    join research_subject subject
      on subject.id=relevance.subject_id and subject.project_id=relevance.project_id
    join source_video video on video.id=relevance.video_id
    where relevance.project_id=new.project_id
      and relevance.video_id=new.video_id
      and relevance.subject_id=new.subject_id
      and relevance.decision='relevant'
      and inclusion.status in ('candidate', 'shortlisted', 'accepted')
      and subject.status='active'
      and video.availability_status='available'
      and exists (
        select 1 from pipeline_run_item item
        where item.run_id=new.source_run_id
          and item.entity_type='video'
          and item.entity_id=new.video_id
      )
  ) then
    raise exception 'project_video_subject_score requires active relevant subject evidence';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_video_subject_score_eligible on project_video_subject_score;
create trigger trg_project_video_subject_score_eligible
before insert on project_video_subject_score
for each row execute function enforce_project_video_subject_score_eligible();

-- Scores are evidence of one deterministic run. Re-running creates a new
-- run-scoped record; old scores are not rewritten to match later opinions.
create or replace function reject_project_video_subject_score_change()
returns trigger language plpgsql as $$
begin
  raise exception 'project_video_subject_score is immutable';
end;
$$;
drop trigger if exists trg_project_video_subject_score_immutable on project_video_subject_score;
create trigger trg_project_video_subject_score_immutable
before update or delete on project_video_subject_score
for each row execute function reject_project_video_subject_score_change();

comment on table project_video_subject_score is
  'Run-scoped deterministic L1 score for a locally included, currently relevant project subject. It never changes global monitoring state.';

-- Fresh-volume bootstrap parity with migration 030. Profiles remain local
-- interpretations, separate from canonical public evidence and media.
create table if not exists research_subject_profile_version (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  subject_id uuid not null,
  profile_kind text not null check (profile_kind in (
    'ip_narrative', 'destination_experience', 'activity_conversion', 'other'
  )),
  version_no integer not null check (version_no > 0),
  status text not null default 'draft' check (status in ('draft', 'approved', 'superseded', 'revoked')),
  summary jsonb not null check (
    jsonb_typeof(summary) = 'object'
    and summary <> '{}'::jsonb
    and summary - array[
      'target_audience', 'shootable_scenes', 'narrative_constraints',
      'forbidden_expressions', 'current_facts'
    ]::text[] = '{}'::jsonb
    and pg_column_size(summary) <= 8192
  ),
  rights_status text not null check (rights_status in (
    'unknown', 'pending', 'cleared', 'restricted', 'prohibited'
  )),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  source_digest text not null check (source_digest ~ '^[0-9a-f]{64}$'),
  content_fingerprint text not null check (content_fingerprint ~ '^[0-9a-f]{64}$'),
  approved_by text check (approved_by is null or char_length(approved_by) between 3 and 254),
  approved_at timestamptz,
  superseded_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  unique (project_id, subject_id, profile_kind, version_no),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (status not in ('approved', 'superseded') or (approved_by is not null and approved_at is not null)),
  check ((status = 'superseded') = (superseded_at is not null)),
  check ((status = 'revoked') = (revoked_at is not null))
);

create unique index if not exists uq_research_subject_profile_one_approved
  on research_subject_profile_version(project_id, subject_id)
  where status = 'approved';
create index if not exists idx_research_subject_profile_subject_kind_version
  on research_subject_profile_version(project_id, subject_id, profile_kind, version_no desc);

create or replace function enforce_research_subject_profile_version()
returns trigger language plpgsql as $$
declare
  computed_fingerprint text;
begin
  computed_fingerprint := encode(digest(
    new.profile_kind || E'\n' || new.summary::text || E'\n' || new.rights_status
      || E'\n' || new.source_reference || E'\n' || new.source_digest,
    'sha256'
  ), 'hex');

  if tg_op = 'INSERT' then
    if new.status <> 'draft' then
      raise exception 'research_subject_profile_version must begin as draft';
    end if;
    if new.approved_by is not null or new.approved_at is not null
       or new.superseded_at is not null or new.revoked_at is not null then
      raise exception 'draft research_subject_profile_version cannot carry lifecycle timestamps';
    end if;
  else
    if old.status = 'draft' then
      if new.status not in ('draft', 'approved', 'revoked') then
        raise exception 'draft research_subject_profile_version may only become approved or revoked';
      end if;
    elsif old.status = 'approved' then
      if new.status not in ('superseded', 'revoked') then
        raise exception 'approved research_subject_profile_version may only become superseded or revoked';
      end if;
    else
      raise exception 'superseded or revoked research_subject_profile_version is immutable';
    end if;

    if old.status <> 'draft' and (
      new.project_id, new.subject_id, new.profile_kind, new.version_no,
      new.summary, new.rights_status, new.source_reference, new.source_digest,
      new.content_fingerprint, new.approved_by, new.approved_at
    ) is distinct from (
      old.project_id, old.subject_id, old.profile_kind, old.version_no,
      old.summary, old.rights_status, old.source_reference, old.source_digest,
      old.content_fingerprint, old.approved_by, old.approved_at
    ) then
      raise exception 'approved research_subject_profile_version content is immutable';
    end if;
  end if;

  new.content_fingerprint := computed_fingerprint;
  if new.status = 'approved' then
    if new.approved_by is null then
      raise exception 'approved research_subject_profile_version requires approved_by';
    end if;
    new.approved_at := coalesce(new.approved_at, clock_timestamp());
  elsif tg_op = 'INSERT' or old.status = 'draft' then
    new.approved_by := null;
    new.approved_at := null;
  end if;
  if new.status = 'superseded' then
    new.superseded_at := coalesce(new.superseded_at, clock_timestamp());
    new.revoked_at := null;
  elsif new.status = 'revoked' then
    new.revoked_at := coalesce(new.revoked_at, clock_timestamp());
    new.superseded_at := null;
  else
    new.superseded_at := null;
    new.revoked_at := null;
  end if;
  new.updated_at := clock_timestamp();
  return new;
end;
$$;

drop trigger if exists trg_research_subject_profile_version_lifecycle on research_subject_profile_version;
create trigger trg_research_subject_profile_version_lifecycle
before insert or update on research_subject_profile_version
for each row execute function enforce_research_subject_profile_version();

create or replace function reject_research_subject_profile_version_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'research_subject_profile_version must be revoked, not deleted';
end;
$$;
drop trigger if exists trg_research_subject_profile_version_no_delete on research_subject_profile_version;
create trigger trg_research_subject_profile_version_no_delete
before delete on research_subject_profile_version
for each row execute function reject_research_subject_profile_version_delete();

comment on table research_subject_profile_version is
  'Versioned, project-local short subject brief. Approved content and its deterministic fingerprint are immutable; later changes require a new draft.';

-- Fresh-volume bootstrap parity with migration 031.
-- An adopted action must say exactly which approved, rights-cleared subject
-- brief justified it. Existing cards remain legacy records and are not
-- backfilled or invalidated.

alter table project_decision_card
  add column if not exists profile_binding_required_at timestamptz;

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'project_decision_card'::regclass
      and conname = 'uq_project_decision_card_subject_scope'
  ) then
    alter table project_decision_card
      add constraint uq_project_decision_card_subject_scope
      unique (id, project_id, subject_id);
  end if;
end $$;

create or replace function enforce_project_decision_card_profile_requirement()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.decision = 'adopt' then
      if new.subject_id is null then
        raise exception 'adopt project_decision_card requires a subject profile binding';
      end if;
      -- Never accept a caller-supplied historical cutover marker.
      new.profile_binding_required_at := clock_timestamp();
    elsif new.profile_binding_required_at is not null then
      raise exception 'only adopt project_decision_card may require a subject profile binding';
    end if;
  elsif old.profile_binding_required_at is not null then
    if new.decision <> 'adopt' or new.subject_id is null then
      raise exception 'bound adopt project_decision_card cannot remove its subject profile requirement';
    end if;
    if new.profile_binding_required_at is distinct from old.profile_binding_required_at then
      raise exception 'project_decision_card profile binding requirement is immutable';
    end if;
  elsif old.decision = 'adopt' then
    -- Pre-031 adopted cards are historical evidence.  Only their status and
    -- review may finish an old cycle; their source and action cannot change.
    if (new.project_id, new.source_video_id, new.subject_id, new.hypothesis,
        new.reference_point, new.adaptation_difference, new.owner_actor,
        new.decision, new.created_by) is distinct from
       (old.project_id, old.source_video_id, old.subject_id, old.hypothesis,
        old.reference_point, old.adaptation_difference, old.owner_actor,
        old.decision, old.created_by) then
      raise exception 'legacy adopt project_decision_card evidence is immutable';
    end if;
    if new.profile_binding_required_at is not null then
      raise exception 'legacy project_decision_card profile binding requirement cannot be forged';
    end if;
  elsif old.decision <> 'adopt' and new.decision = 'adopt' then
    if new.subject_id is null then
      raise exception 'adopt project_decision_card requires a subject profile binding';
    end if;
    new.profile_binding_required_at := clock_timestamp();
  elsif new.profile_binding_required_at is not null then
    raise exception 'legacy project_decision_card profile binding requirement cannot be forged';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_project_decision_card_profile_requirement on project_decision_card;
create trigger trg_project_decision_card_profile_requirement
before insert or update
on project_decision_card
for each row execute function enforce_project_decision_card_profile_requirement();

create table if not exists project_decision_card_profile_binding (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  subject_id uuid not null,
  profile_id uuid not null,
  profile_content_fingerprint text not null check (profile_content_fingerprint ~ '^[0-9a-f]{64}$'),
  profile_source_digest text not null check (profile_source_digest ~ '^[0-9a-f]{64}$'),
  profile_kind text not null check (profile_kind in (
    'ip_narrative', 'destination_experience', 'activity_conversion', 'other'
  )),
  profile_version_no integer not null check (profile_version_no > 0),
  rights_status_at_binding text not null check (rights_status_at_binding = 'cleared'),
  profile_approved_at timestamptz not null,
  bound_by text not null check (char_length(bound_by) between 3 and 254 and bound_by = lower(bound_by)),
  bound_at timestamptz not null default now(),
  unique (decision_card_id, project_id),
  foreign key (decision_card_id, project_id, subject_id)
    references project_decision_card(id, project_id, subject_id) on delete restrict,
  foreign key (profile_id, project_id)
    references research_subject_profile_version(id, project_id) on delete restrict
);
create index if not exists idx_project_decision_card_profile_binding_profile
  on project_decision_card_profile_binding(project_id, subject_id, profile_id);

create or replace function enforce_project_decision_card_profile_binding()
returns trigger language plpgsql as $$
declare
  card_row project_decision_card%rowtype;
  profile_row research_subject_profile_version%rowtype;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_profile_binding is immutable';
  end if;

  -- Lock both rows while deriving the snapshot.  An approval/revocation that
  -- races this insert either happens first (and rejects the bind) or waits
  -- until this exact approved-and-cleared evidence has been committed.
  select * into card_row
  from project_decision_card
  where id = new.decision_card_id and project_id = new.project_id
  for update;
  if not found then
    raise exception 'project_decision_card_profile_binding requires a local decision card';
  end if;
  if card_row.decision <> 'adopt'
     or card_row.profile_binding_required_at is null
     or card_row.subject_id is null
     or card_row.subject_id <> new.subject_id then
    raise exception 'project_decision_card_profile_binding requires a bound adopt decision card subject';
  end if;

  select * into profile_row
  from research_subject_profile_version
  where id = new.profile_id and project_id = new.project_id
  for update;
  if not found
     or profile_row.subject_id <> new.subject_id
     or profile_row.status <> 'approved'
     or profile_row.rights_status <> 'cleared'
     or profile_row.approved_at is null then
    raise exception 'project_decision_card_profile_binding requires an approved, rights-cleared local subject profile';
  end if;

  if not exists (
    select 1 from research_project_member member
    where member.project_id = new.project_id
      and member.actor_id = new.bound_by
      and member.effective_from <= now()
      and member.status = 'active'
      and member.role in ('owner', 'admin')
      and (member.effective_until is null or member.effective_until > now())
      and not exists (
        select 1 from research_project_member newer
        where newer.project_id = member.project_id
          and newer.actor_id = member.actor_id
          and newer.effective_from <= now()
          and newer.effective_from > member.effective_from
      )
  ) then
    raise exception 'project_decision_card_profile_binding bound_by must be an active project owner or admin';
  end if;

  -- All evidence fields are DB-derived; callers cannot manufacture a better
  -- rights state, a different fingerprint, or an earlier binding time.
  new.profile_content_fingerprint := profile_row.content_fingerprint;
  new.profile_source_digest := profile_row.source_digest;
  new.profile_kind := profile_row.profile_kind;
  new.profile_version_no := profile_row.version_no;
  new.rights_status_at_binding := profile_row.rights_status;
  new.profile_approved_at := profile_row.approved_at;
  new.bound_at := clock_timestamp();
  return new;
end;
$$;

drop trigger if exists trg_project_decision_card_profile_binding_immutable on project_decision_card_profile_binding;
create trigger trg_project_decision_card_profile_binding_immutable
before insert or update or delete on project_decision_card_profile_binding
for each row execute function enforce_project_decision_card_profile_binding();

create or replace function enforce_project_decision_card_adopt_profile_binding()
returns trigger language plpgsql as $$
begin
  -- NULL means a pre-031 card.  It remains readable historical evidence, but
  -- cannot become a new adopted action without passing the new gate.
  if new.profile_binding_required_at is not null and not exists (
    select 1 from project_decision_card_profile_binding binding
    where binding.project_id = new.project_id
      and binding.decision_card_id = new.id
      and binding.subject_id = new.subject_id
  ) then
    raise exception 'adopt project_decision_card requires an immutable approved subject profile binding';
  end if;
  return null;
end;
$$;

drop trigger if exists trg_project_decision_card_adopt_profile_binding on project_decision_card;
create constraint trigger trg_project_decision_card_adopt_profile_binding
after insert or update of decision, subject_id, profile_binding_required_at
on project_decision_card deferrable initially deferred
for each row execute function enforce_project_decision_card_adopt_profile_binding();

comment on table project_decision_card_profile_binding is
  'Immutable point-in-time binding from a post-031 adopted action card to one approved, rights-cleared local subject profile version.';

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
  -- Full MediaAssetReference plus normalized HTTPS delivery origin fingerprint.
  -- It is calculated by the review backend, not from content_sha256 alone.
  asset_manifest_fingerprint text not null check (asset_manifest_fingerprint ~ '^[0-9a-f]{64}$'),
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
    new.project_id, new.video_id, new.asset_id, new.review_version, new.media_fingerprint, new.asset_manifest_fingerprint,
    new.delivery_origin, new.identity_source, new.review_statement, new.reviewed_by, new.reviewed_at
  ) is distinct from (
    old.project_id, old.video_id, old.asset_id, old.review_version, old.media_fingerprint, old.asset_manifest_fingerprint,
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
  asset_manifest_fingerprint text not null check (asset_manifest_fingerprint ~ '^[0-9a-f]{64}$'),
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
       or review_row.asset_manifest_fingerprint <> new.asset_manifest_fingerprint
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

-- Fresh-volume bootstrap parity with migration 033. Existing installations
-- apply the reviewed migration; a new volume receives the same write gates.
alter table project_decision_card
  add column if not exists evaluation_metric text,
  add column if not exists success_rule text,
  add column if not exists observation_window_days integer,
  add column if not exists comparison_basis text,
  add column if not exists confounder_plan text,
  add column if not exists review_verdict text;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_decision_card'::regclass
    and conname='project_decision_card_experiment_contract_check') then
    alter table project_decision_card
      add constraint project_decision_card_experiment_contract_check check (
        (evaluation_metric is null and success_rule is null and observation_window_days is null
          and comparison_basis is null and confounder_plan is null)
        or
        (num_nulls(evaluation_metric, success_rule, observation_window_days,
          comparison_basis, confounder_plan) = 0
          and char_length(btrim(evaluation_metric)) between 1 and 160
          and char_length(btrim(success_rule)) between 1 and 500
          and observation_window_days between 1 and 90
          and char_length(btrim(comparison_basis)) between 1 and 500
          and char_length(btrim(confounder_plan)) between 1 and 500)
      );
  end if;
end $$;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_decision_card'::regclass
    and conname='project_decision_card_review_verdict_check') then
    alter table project_decision_card
      add constraint project_decision_card_review_verdict_check check (
        (review_verdict is null or (evaluation_metric is not null and status='reviewed'
          and review_verdict in ('supported','not_supported','inconclusive')))
        and (evaluation_metric is null or
          ((status='reviewed') = (review_verdict is not null)))
      );
  end if;
end $$;
create or replace function enforce_project_decision_card_experiment_contract()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.evaluation_metric is null or new.success_rule is null
      or new.observation_window_days is null or new.comparison_basis is null
      or new.confounder_plan is null then
      raise exception 'new project decision card requires preregistered experiment contract';
    end if;
  elsif new.status='reviewed' and old.status is distinct from 'reviewed'
    and old.evaluation_metric is null then
    raise exception 'legacy project decision card cannot be reviewed without preregistration';
  elsif (new.evaluation_metric, new.success_rule, new.observation_window_days,
         new.comparison_basis, new.confounder_plan)
        is distinct from
        (old.evaluation_metric, old.success_rule, old.observation_window_days,
         old.comparison_basis, old.confounder_plan) then
    raise exception 'project decision card experiment contract is immutable';
  elsif old.evaluation_metric is not null
    and (new.hypothesis, new.reference_point, new.adaptation_difference,
         new.decision, new.subject_id)
        is distinct from
        (old.hypothesis, old.reference_point, old.adaptation_difference,
         old.decision, old.subject_id) then
    raise exception 'preregistered project decision hypothesis is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_experiment_contract on project_decision_card;
create trigger trg_project_decision_card_experiment_contract
before insert or update on project_decision_card
for each row execute function enforce_project_decision_card_experiment_contract();

alter table project_publication_record
  add column if not exists platform text,
  add column if not exists account_reference text,
  add column if not exists platform_content_id text,
  add column if not exists content_version text,
  add column if not exists distribution_mode text;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_publication_record'::regclass
    and conname='project_publication_provenance_check') then
    alter table project_publication_record
      add constraint project_publication_provenance_check check (
        (platform is null and account_reference is null and platform_content_id is null
          and content_version is null and distribution_mode is null)
        or
        (num_nulls(platform, account_reference, platform_content_id,
          content_version, distribution_mode) = 0
          and platform in ('douyin', 'xiaohongshu', 'kuaishou', 'bilibili', 'other')
          and char_length(btrim(account_reference)) between 1 and 160
          and char_length(btrim(platform_content_id)) between 1 and 160
          and char_length(btrim(content_version)) between 1 and 160
          and distribution_mode in ('organic', 'paid', 'mixed'))
      );
  end if;
end $$;
create or replace function enforce_project_publication_provenance()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.decision_card_id is null or new.platform is null
      or new.account_reference is null or new.platform_content_id is null
      or new.content_version is null or new.distribution_mode is null then
      raise exception 'new project publication requires linked action and real platform provenance';
    end if;
    if not exists (
      select 1 from project_decision_card card
      where card.id=new.decision_card_id and card.project_id=new.project_id
        and card.evaluation_metric is not null
        and ((card.decision='adopt' and card.status in ('active','adopted'))
          or (card.decision='observe' and card.status='observing'))
    ) then
      raise exception 'new project publication requires active preregistered adopted or observing action';
    end if;
  elsif (new.platform, new.account_reference, new.platform_content_id,
         new.content_version, new.distribution_mode, new.decision_card_id)
        is distinct from
        (old.platform, old.account_reference, old.platform_content_id,
         old.content_version, old.distribution_mode, old.decision_card_id) then
    raise exception 'project publication provenance is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_publication_provenance on project_publication_record;
create trigger trg_project_publication_provenance
before insert or update on project_publication_record
for each row execute function enforce_project_publication_provenance();
create or replace function reject_preregistered_publication_change()
returns trigger language plpgsql as $$
begin
  if old.platform_content_id is not null then
    raise exception 'preregistered project publication is immutable';
  end if;
  return old;
end;
$$;
drop trigger if exists trg_preregistered_publication_immutable on project_publication_record;
create trigger trg_preregistered_publication_immutable
before update or delete on project_publication_record
for each row execute function reject_preregistered_publication_change();
create unique index if not exists uq_project_publication_platform_content
  on project_publication_record(project_id, platform, platform_content_id)
  where platform_content_id is not null;
comment on column project_decision_card.success_rule is
  'Human-preregistered falsifiable success rule. Never inferred from outcome data.';
comment on column project_publication_record.platform_content_id is
  'Actual published platform work ID, not a planned or simulated content ID.';

-- Fresh-volume bootstrap parity with migration 034. A reported platform time
-- is still human-entered evidence, not independent platform verification.
alter table project_publication_record
  add column if not exists published_at timestamptz;
create or replace function enforce_project_decision_card_experiment_contract()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.evaluation_metric is null or new.success_rule is null
      or new.observation_window_days is null or new.comparison_basis is null
      or new.confounder_plan is null then
      raise exception 'new project decision card requires preregistered experiment contract';
    end if;
  elsif new.status='reviewed' and old.status is distinct from 'reviewed'
    and old.evaluation_metric is null then
    raise exception 'legacy project decision card cannot be reviewed without preregistration';
  elsif (new.evaluation_metric, new.success_rule, new.observation_window_days,
         new.comparison_basis, new.confounder_plan)
        is distinct from
        (old.evaluation_metric, old.success_rule, old.observation_window_days,
         old.comparison_basis, old.confounder_plan) then
    raise exception 'project decision card experiment contract is immutable';
  elsif old.evaluation_metric is not null
    and (new.hypothesis, new.reference_point, new.adaptation_difference,
         new.decision, new.subject_id, new.source_video_id, new.created_at)
        is distinct from
        (old.hypothesis, old.reference_point, old.adaptation_difference,
         old.decision, old.subject_id, old.source_video_id, old.created_at) then
    raise exception 'preregistered project decision hypothesis and source are immutable';
  end if;
  return new;
end;
$$;
create or replace function enforce_project_publication_provenance()
returns trigger language plpgsql as $$
declare
  card_created_at timestamptz;
begin
  if tg_op = 'INSERT' then
    if new.decision_card_id is null or new.platform is null
      or new.account_reference is null or new.platform_content_id is null
      or new.content_version is null or new.distribution_mode is null
      or new.published_at is null then
      raise exception 'new project publication requires linked action and self-reported platform provenance';
    end if;
    select card.created_at into card_created_at
    from project_decision_card card
    where card.id=new.decision_card_id and card.project_id=new.project_id
      and card.evaluation_metric is not null
      and ((card.decision='adopt' and card.status in ('active','adopted'))
        or (card.decision='observe' and card.status='observing'));
    if card_created_at is null then
      raise exception 'new project publication requires active preregistered adopted or observing action';
    end if;
    if new.published_at < card_created_at or new.published_at > now()
      or new.publication_date <> (new.published_at at time zone 'Asia/Shanghai')::date then
      raise exception 'project publication time must follow preregistration and match local publication date';
    end if;
  elsif (new.platform, new.account_reference, new.platform_content_id,
         new.content_version, new.distribution_mode, new.decision_card_id,
         new.published_at)
        is distinct from
        (old.platform, old.account_reference, old.platform_content_id,
         old.content_version, old.distribution_mode, old.decision_card_id,
         old.published_at) then
    raise exception 'project publication provenance is immutable';
  end if;
  return new;
end;
$$;
comment on column project_publication_record.published_at is
  'Human-entered platform publication time, not independently verified; NULL marks a pre-034 historical row.';

-- Fresh-volume bootstrap parity with migration 035.
create table if not exists project_decision_card_evidence_ref (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null,
  decision_card_id uuid not null,
  position smallint not null check (position between 1 and 12),
  video_id uuid not null references source_video(id) on delete restrict,
  role text not null check (role in ('comparable', 'counterexample')),
  reason text not null check (char_length(reason) between 1 and 500 and reason = btrim(reason)),
  video_title_at_binding text,
  account_name_at_binding text,
  created_at timestamptz not null default now(),
  unique (decision_card_id, video_id),
  unique (decision_card_id, position),
  foreign key (decision_card_id, project_id)
    references project_decision_card(id, project_id) on delete restrict
);
create index if not exists idx_project_decision_card_evidence_ref_project_card
  on project_decision_card_evidence_ref(project_id, decision_card_id, position);
create or replace function enforce_project_decision_card_evidence_ref()
returns trigger language plpgsql as $$
declare
  primary_video uuid;
  card_created_at timestamptz;
  card_status text;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_evidence_ref is append-only';
  end if;
  -- Serializes concurrent additions to one card, including the 12-ref ceiling.
  select card.source_video_id, card.created_at, card.status
    into primary_video, card_created_at, card_status
  from project_decision_card card
  where card.id=new.decision_card_id and card.project_id=new.project_id
  for update;
  if primary_video is null then
    raise exception 'project_decision_card_evidence_ref requires a local decision card';
  end if;
  if card_created_at is distinct from transaction_timestamp()
     or card_status not in ('active', 'observing', 'excluded') then
    raise exception 'project_decision_card_evidence_ref must be bound when the card is created';
  end if;
  if new.video_id=primary_video then
    raise exception 'project_decision_card_evidence_ref cannot repeat primary video';
  end if;
  if (select count(*) from project_decision_card_evidence_ref ref
      where ref.decision_card_id=new.decision_card_id) >= 12 then
    raise exception 'project_decision_card_evidence_ref exceeds 12 videos';
  end if;
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    join source_video video_row on video_row.id=inclusion_row.video_id
    where inclusion_row.project_id=new.project_id
      and inclusion_row.video_id=new.video_id
      and inclusion_row.status='accepted'
      and video_row.availability_status='available'
  ) then
    raise exception 'project_decision_card_evidence_ref requires locally accepted available video';
  end if;
  select video_row.title, account_row.nickname
    into new.video_title_at_binding, new.account_name_at_binding
  from source_video video_row
  left join source_account account_row on account_row.id=video_row.account_id
  where video_row.id=new.video_id;
  return new;
end;
$$;

drop trigger if exists trg_project_decision_card_evidence_ref on project_decision_card_evidence_ref;
create trigger trg_project_decision_card_evidence_ref
before insert or update or delete on project_decision_card_evidence_ref
for each row execute function enforce_project_decision_card_evidence_ref();
comment on table project_decision_card_evidence_ref is
  'Immutable project-local comparison or counterexample public video bound to a decision card; later withdrawal remains visible.';

-- Fresh-volume bootstrap parity with migration 037. Keep earlier migration
-- snapshots above this marker unchanged for historical-upgrade tests.
create table if not exists project_video_case_review (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  video_id uuid not null references source_video(id) on delete restrict,
  version_no integer not null check (version_no > 0),
  status text not null check (status in ('partial', 'complete', 'insufficient')),
  source_reference text not null check (char_length(source_reference) between 1 and 512),
  observed_at timestamptz not null,
  video_coverage text not null check (video_coverage in ('none', 'partial', 'complete')),
  audio_coverage text not null check (audio_coverage in ('none', 'partial', 'complete', 'not_applicable')),
  audio_not_applicable_reason text check (audio_not_applicable_reason is null or char_length(audio_not_applicable_reason) between 1 and 1000),
  key_event_complete boolean,
  verified_facts text not null default '' check (char_length(verified_facts) <= 4000),
  evidence_gaps text not null default '' check (char_length(evidence_gaps) <= 4000),
  counterevidence text not null default '' check (char_length(counterevidence) <= 4000),
  comparability_note text not null default '' check (char_length(comparability_note) <= 4000),
  reviewed_by text not null check (char_length(reviewed_by) between 3 and 254 and reviewed_by = lower(reviewed_by)),
  created_at timestamptz not null default now(),
  unique (project_id, video_id, version_no),
  check ((audio_coverage = 'not_applicable') = (audio_not_applicable_reason is not null)),
  check (key_event_complete is distinct from false or status = 'insufficient'),
  check (status <> 'complete' or (video_coverage = 'complete'
    and (audio_coverage = 'complete' or (audio_coverage = 'not_applicable' and audio_not_applicable_reason is not null))
    and key_event_complete is true and char_length(trim(verified_facts)) > 0
    and char_length(trim(comparability_note)) > 0))
);
create index if not exists idx_project_video_case_review_latest
  on project_video_case_review(project_id, video_id, version_no desc);
create or replace function reject_project_video_case_review_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'project_video_case_review is append-only';
end;
$$;
drop trigger if exists trg_project_video_case_review_append_only on project_video_case_review;
create trigger trg_project_video_case_review_append_only
before update or delete on project_video_case_review
for each row execute function reject_project_video_case_review_mutation();
comment on table project_video_case_review is
  'Project-private human Case viewing history. No accepted, ASR or L3 state is inferred from these rows.';

alter table project_decision_card
  add column if not exists source_case_review_id uuid references project_video_case_review(id) on delete restrict;
alter table project_decision_card_evidence_ref
  add column if not exists case_review_id_at_binding uuid references project_video_case_review(id) on delete restrict;
create or replace function reject_project_case_binding_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'bound project case review is immutable';
end;
$$;
drop trigger if exists trg_project_decision_card_case_binding_immutable on project_decision_card;
create trigger trg_project_decision_card_case_binding_immutable
before update of source_case_review_id on project_decision_card
for each row execute function reject_project_case_binding_mutation();

create or replace function enforce_project_decision_card_accepted_source()
returns trigger language plpgsql as $$
declare
  bound_review uuid;
begin
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    where inclusion_row.project_id = new.project_id
      and inclusion_row.video_id = new.source_video_id
      and inclusion_row.status = 'accepted'
      and exists (select 1 from source_video video_row
                  where video_row.id=new.source_video_id and video_row.availability_status='available')
  ) then
    raise exception 'project_decision_card requires current complete project case review';
  end if;
  select review.id into bound_review from project_video_case_review review
  where review.project_id=new.project_id and review.video_id=new.source_video_id
  order by review.version_no desc limit 1;
  if bound_review is null or
     (select review.status from project_video_case_review review where review.id=bound_review) <> 'complete' then
    raise exception 'project_decision_card requires current complete project case review';
  end if;
  new.source_case_review_id := bound_review;
  return new;
end;
$$;

create or replace function enforce_project_decision_card_evidence_ref()
returns trigger language plpgsql as $$
declare
  primary_video uuid;
  card_created_at timestamptz;
  card_status text;
  bound_review uuid;
begin
  if tg_op <> 'INSERT' then
    raise exception 'project_decision_card_evidence_ref is append-only';
  end if;
  select card.source_video_id, card.created_at, card.status
    into primary_video, card_created_at, card_status
  from project_decision_card card
  where card.id=new.decision_card_id and card.project_id=new.project_id
  for update;
  if primary_video is null then
    raise exception 'project_decision_card_evidence_ref requires a local decision card';
  end if;
  if card_created_at is distinct from transaction_timestamp()
     or card_status not in ('active', 'observing', 'excluded') then
    raise exception 'project_decision_card_evidence_ref must be bound when the card is created';
  end if;
  if new.video_id=primary_video then
    raise exception 'project_decision_card_evidence_ref cannot repeat primary video';
  end if;
  if (select count(*) from project_decision_card_evidence_ref ref
      where ref.decision_card_id=new.decision_card_id) >= 12 then
    raise exception 'project_decision_card_evidence_ref exceeds 12 videos';
  end if;
  if not exists (
    select 1 from project_video_inclusion inclusion_row
    join source_video video_row on video_row.id=inclusion_row.video_id
    where inclusion_row.project_id=new.project_id
      and inclusion_row.video_id=new.video_id
      and inclusion_row.status='accepted'
      and video_row.availability_status='available'
  ) then
    raise exception 'project_decision_card_evidence_ref requires current complete project case review';
  end if;
  select review.id into bound_review from project_video_case_review review
  where review.project_id=new.project_id and review.video_id=new.video_id
  order by review.version_no desc limit 1;
  if bound_review is null or
     (select review.status from project_video_case_review review where review.id=bound_review) <> 'complete' then
    raise exception 'project_decision_card_evidence_ref requires current complete project case review';
  end if;
  new.case_review_id_at_binding := bound_review;
  select video_row.title, account_row.nickname
    into new.video_title_at_binding, new.account_name_at_binding
  from source_video video_row
  left join source_account account_row on account_row.id=video_row.account_id
  where video_row.id=new.video_id;
  return new;
end;
$$;

-- Continuing an action requires the same reviewed evidence versions and a
-- still-approved, rights-cleared profile when the action adopts an IP.
create or replace function project_action_evidence_is_current(p_project uuid, p_card uuid)
returns boolean language sql stable as $$
  select exists (
    select 1 from project_decision_card card
    left join project_decision_card_profile_binding binding
      on binding.project_id=card.project_id and binding.decision_card_id=card.id
    left join research_subject_profile_version profile
      on profile.project_id=card.project_id and profile.id=binding.profile_id
    where card.id=p_card and card.project_id=p_project
      and (card.decision <> 'adopt' or
           (profile.status='approved' and profile.rights_status='cleared'))
  ) and not exists (
    select 1 from (
      select card.source_video_id as video_id, card.source_case_review_id as bound_id
      from project_decision_card card where card.id=p_card and card.project_id=p_project
      union all
      select ref.video_id, ref.case_review_id_at_binding
      from project_decision_card_evidence_ref ref
      where ref.decision_card_id=p_card and ref.project_id=p_project
    ) source_row
    left join project_video_inclusion inclusion_row
      on inclusion_row.project_id=p_project and inclusion_row.video_id=source_row.video_id
    left join source_video video_row on video_row.id=source_row.video_id
    left join lateral (
      select review.id, review.status from project_video_case_review review
      where review.project_id=p_project and review.video_id=source_row.video_id
      order by review.version_no desc limit 1
    ) latest_review on true
    where inclusion_row.project_id is null or inclusion_row.status <> 'accepted'
       or video_row.availability_status is distinct from 'available'
       or source_row.bound_id is null or latest_review.id is distinct from source_row.bound_id
       or latest_review.status is distinct from 'complete'
  );
$$;

create or replace function enforce_project_action_current_evidence()
returns trigger language plpgsql as $$
begin
  if tg_table_name='project_publication_record' then
    if new.decision_card_id is not null and
       not project_action_evidence_is_current(new.project_id,new.decision_card_id) then
      raise exception 'project action evidence is no longer current';
    end if;
  elsif new.status='adopted' and old.status is distinct from new.status and
        not project_action_evidence_is_current(new.project_id,new.id) then
    raise exception 'project action evidence is no longer current';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_publication_current_evidence on project_publication_record;
create trigger trg_project_publication_current_evidence
before insert on project_publication_record
for each row execute function enforce_project_action_current_evidence();
drop trigger if exists trg_project_decision_card_current_evidence on project_decision_card;
create trigger trg_project_decision_card_current_evidence
before update of status on project_decision_card
for each row execute function enforce_project_action_current_evidence();

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
