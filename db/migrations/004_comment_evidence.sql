-- 004_comment_evidence.sql
-- Replayable, idempotent normalized comment/reply evidence.

alter table video_comment
  add column if not exists source_endpoint text,
  add column if not exists parent_platform_comment_id text,
  add column if not exists reply_count bigint,
  add column if not exists sample_reason text not null default 'top',
  add column if not exists raw_ref text,
  add column if not exists last_seen_at timestamptz not null default now(),
  add column if not exists observation_count bigint not null default 1;

drop index if exists uq_video_comment_provider_id;

create unique index if not exists uq_video_comment_provider_video_id
  on video_comment(provider, video_id, platform_comment_id)
  where platform_comment_id is not null;

create index if not exists idx_comment_video_seen
  on video_comment(video_id, last_seen_at desc);

create index if not exists idx_comment_parent
  on video_comment(provider, video_id, parent_platform_comment_id)
  where parent_platform_comment_id is not null;
