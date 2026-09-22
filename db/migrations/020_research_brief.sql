-- User-authored research briefs are the control plane for collection scope.
-- Provider credentials, raw responses and approval content never belong here.

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
  constraint research_brief_owner_check
    check (char_length(owner_actor) between 3 and 254),
  constraint research_brief_name_check
    check (char_length(name) between 1 and 80),
  constraint research_brief_platform_check
    check (platform = 'douyin'),
  constraint research_brief_source_check
    check (source_type in ('low_fan', 'keyword', 'account')),
  constraint research_brief_target_check
    check (
      (source_type = 'low_fan' and target is null) or
      (source_type in ('keyword', 'account') and target is not null and
       char_length(target) between 1 and 120)
    ),
  constraint research_brief_window_check
    check (time_window_hours in (24, 72, 168, 720) and
           (source_type <> 'low_fan' or time_window_hours in (24, 72, 168))),
  constraint research_brief_item_check
    check (max_items between 1 and 20 and
           (source_type <> 'low_fan' or max_items <= 5) and
           (depth not in ('media', 'review_ready') or max_items <= 5)),
  constraint research_brief_depth_check
    check (depth in ('metadata', 'comments', 'media', 'review_ready')),
  constraint research_brief_cadence_check
    check (cadence_hours is null or cadence_hours in (6, 12, 24)),
  constraint research_brief_status_check
    check (status in ('draft', 'active', 'paused', 'archived')),
  constraint research_brief_version_check
    check (config_version >= 1),
  constraint research_brief_due_check
    check ((status = 'active' and next_due_at is not null) or status <> 'active')
);

create unique index if not exists uq_research_brief_owner_name
  on research_brief(owner_actor, lower(name)) where status <> 'archived';
create index if not exists idx_research_brief_due
  on research_brief(next_due_at, id) where status = 'active';

create table if not exists research_brief_run (
  id uuid primary key default gen_random_uuid(),
  brief_id uuid not null references research_brief(id) on delete cascade,
  brief_version integer not null check (brief_version >= 1),
  dispatch_key text not null unique,
  trigger_kind text not null check (trigger_kind in ('manual', 'schedule')),
  triggered_by text not null,
  status text not null check (
    status in ('running', 'success', 'failed', 'deferred')
  ),
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
