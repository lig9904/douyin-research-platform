-- 004_comment_evidence.sql
-- Replayable, idempotent normalized comment/reply evidence.

alter table video_comment
  add column if not exists source_endpoint text,
  add column if not exists parent_platform_comment_id text,
  add column if not exists reply_count bigint,
  add column if not exists sample_reason text not null default 'top',
  add column if not exists raw_ref text,
  add column if not exists last_seen_at timestamptz not null default now(),
  add column if not exists observation_count bigint not null default 0;

alter table video_comment
  alter column observation_count set default 0;

drop index if exists uq_video_comment_provider_id;

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
