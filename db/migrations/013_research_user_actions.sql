-- Idempotent, actor-bound research dashboard write actions.

alter table collection_item
  add column if not exists account_id uuid references source_account(id) on delete cascade;

alter table collection_item
  drop constraint if exists collection_item_check;

alter table collection_item
  drop constraint if exists collection_item_one_target;

alter table collection_item
  add constraint collection_item_one_target check (
    (video_id is not null)::int
    + (account_id is not null)::int
    + (signal_id is not null)::int = 1
  );

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
