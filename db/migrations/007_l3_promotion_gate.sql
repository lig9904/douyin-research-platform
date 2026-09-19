-- 007_l3_promotion_gate.sql
-- Deterministic L2-to-L3 selection with an operator-configured daily quota.

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
