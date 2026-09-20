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
