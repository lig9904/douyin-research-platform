-- 003_signal_research_fields.sql
-- Hotspot/signal fields required by the research UI.

alter table external_signal
  add column if not exists description text,
  add column if not exists research_level smallint not null default 0,
  add column if not exists monitoring_status text not null default 'untracked',
  add column if not exists monitoring_priority numeric,
  add column if not exists next_due_at timestamptz;

create index if not exists idx_signal_monitoring
  on external_signal(platform, monitoring_status, research_level, last_seen_at desc);
