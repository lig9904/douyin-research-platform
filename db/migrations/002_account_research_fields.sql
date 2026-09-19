-- 002_account_research_fields.sql
-- Account-library fields used by the multi-platform research UI.

alter table source_account
  add column if not exists bio text,
  add column if not exists location_text text,
  add column if not exists account_type text,
  add column if not exists certification_type text,
  add column if not exists research_level smallint not null default 0,
  add column if not exists monitoring_status text not null default 'untracked',
  add column if not exists monitoring_priority numeric,
  add column if not exists next_due_at timestamptz;

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

create index if not exists idx_account_monitoring
  on source_account(platform, monitoring_status, research_level);
