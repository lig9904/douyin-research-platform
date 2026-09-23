-- Project collaboration is opt-in. A member of the receiving project does not
-- become a member of the source project, and no private/provider data is shared.
create table if not exists project_video_share_grant (
  id uuid primary key default gen_random_uuid(),
  source_project_id uuid not null references research_project(id) on delete restrict,
  target_project_id uuid not null references research_project(id) on delete restrict,
  scope text not null default 'public_video_evidence'
    check (scope = 'public_video_evidence'),
  status text not null default 'offered'
    check (status in ('offered', 'active', 'declined', 'revoked')),
  offered_by text not null,
  offered_at timestamptz not null default now(),
  accepted_by text,
  accepted_at timestamptz,
  closed_by text,
  closed_at timestamptz,
  effective_until timestamptz not null,
  check (source_project_id <> target_project_id),
  check (effective_until > offered_at),
  check (status <> 'active' or (accepted_by is not null and accepted_at is not null)),
  check (status <> 'offered' or (accepted_by is null and accepted_at is null)),
  check ((status in ('declined', 'revoked')) = (closed_by is not null and closed_at is not null))
);
create unique index if not exists uq_project_video_share_open
  on project_video_share_grant(source_project_id, target_project_id, scope)
  where status in ('offered', 'active');
create index if not exists idx_project_video_share_target_active
  on project_video_share_grant(target_project_id, source_project_id, effective_until)
  where status = 'active';

create table if not exists project_access_event (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete restrict,
  actor_id text not null,
  action text not null check (action in (
    'member_add', 'member_role_change', 'member_revoke',
    'share_offer', 'share_accept', 'share_decline', 'share_revoke'
  )),
  subject_actor_id text,
  related_project_id uuid references research_project(id) on delete restrict,
  grant_id uuid references project_video_share_grant(id) on delete restrict,
  created_at timestamptz not null default now(),
  check ((subject_actor_id is not null) <> (grant_id is not null))
);
create index if not exists idx_project_access_event_project_time
  on project_access_event(project_id, created_at desc, id);

-- The grant can be used only when both projects remain active in the same
-- active organization, the recipient is a current member, and the source has
-- deliberately included the video. No review text, media, or L3 is exposed.
create or replace function project_shared_video_can_read(
  p_source_project_id uuid, p_target_project_id uuid,
  p_actor text, p_video_id uuid
) returns boolean language sql stable security invoker as $$
  select coalesce(
    p_source_project_id is not null
    and p_target_project_id is not null
    and p_actor is not null
    and p_video_id is not null
    and project_actor_can_read(p_target_project_id, p_actor)
    and exists (
      select 1
      from research_project source_project
      join research_project target_project
        on target_project.id = p_target_project_id
       and target_project.organization_id = source_project.organization_id
      join project_video_share_grant grant_row
        on grant_row.source_project_id = source_project.id
       and grant_row.target_project_id = target_project.id
      join project_video_inclusion inclusion_row
        on inclusion_row.project_id = source_project.id
       and inclusion_row.video_id = p_video_id
      join source_video video
        on video.id = inclusion_row.video_id
      where source_project.id = p_source_project_id
        and source_project.status = 'active'
        and grant_row.status = 'active'
        and grant_row.scope = 'public_video_evidence'
        and grant_row.effective_until > now()
        and inclusion_row.status <> 'archived'
        and video.availability_status = 'available'
    ), false
  );
$$;
comment on function project_shared_video_can_read(uuid, uuid, text, uuid) is
  'Fail-closed, accepted cross-project grant for public video evidence only.';
revoke all on function project_shared_video_can_read(uuid, uuid, text, uuid) from public;
