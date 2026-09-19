-- 005_comment_l2_features.sql
-- Immutable, replay-safe deterministic L2 aggregates over stored comment evidence.

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
  metadata jsonb not null default '{}'::jsonb,
  unique(video_id, feature_version, evidence_fingerprint)
);

create index if not exists idx_comment_feature_video_time
  on video_comment_feature_snapshot(video_id, calculated_at desc, id desc);
