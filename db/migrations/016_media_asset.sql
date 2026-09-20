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
