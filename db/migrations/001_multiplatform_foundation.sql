-- 001_multiplatform_foundation.sql
-- Safe migration from the original Douyin-first schema to explicit multi-platform dimensions.

create table if not exists platform_registry (
  platform_key text primary key,
  display_name text not null,
  enabled boolean not null default false,
  provider_status text not null default 'planned',
  sort_order integer not null default 100,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into platform_registry(platform_key, display_name, enabled, provider_status, sort_order)
values
  ('douyin', '抖音', true, 'active', 10),
  ('kuaishou', '快手', false, 'planned', 20),
  ('wechat_channels', '视频号', false, 'planned', 30),
  ('xiaohongshu', '小红书', false, 'planned', 40),
  ('bilibili', 'B站', false, 'planned', 50),
  ('weibo', '微博', false, 'planned', 60)
on conflict(platform_key) do update set
  display_name=excluded.display_name,
  sort_order=excluded.sort_order;

alter table source_account alter column platform drop default;
alter table source_video alter column platform drop default;
alter table external_signal alter column platform drop default;

alter table pipeline_run
  add column if not exists platform text;

alter table api_endpoint_registry
  add column if not exists platform text;

alter table external_api_response
  add column if not exists platform text;

alter table external_api_call
  add column if not exists platform text;

update external_api_response set platform='douyin' where platform is null;
update external_api_call set platform='douyin' where platform is null;

alter table external_api_response alter column platform set not null;
alter table external_api_call alter column platform set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='source_account'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table source_account
      add constraint fk_source_account_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='source_video'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table source_video
      add constraint fk_source_video_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='external_signal'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table external_signal
      add constraint fk_external_signal_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='pipeline_run'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table pipeline_run
      add constraint fk_pipeline_run_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='api_endpoint_registry'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table api_endpoint_registry
      add constraint fk_api_endpoint_registry_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='external_api_response'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table external_api_response
      add constraint fk_external_api_response_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;

  if not exists (
    select 1 from pg_constraint
    where contype='f' and conrelid='external_api_call'::regclass
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (platform)%REFERENCES platform_registry%'
  ) then
    alter table external_api_call
      add constraint fk_external_api_call_platform
      foreign key(platform) references platform_registry(platform_key);
  end if;
end $$;

create index if not exists idx_video_platform_published
  on source_video(platform, published_at desc);

create index if not exists idx_account_platform_seen
  on source_account(platform, last_seen_at desc);

create index if not exists idx_api_call_platform_time
  on external_api_call(platform, started_at desc);

create index if not exists idx_api_response_platform_time
  on external_api_response(platform, requested_at desc);
