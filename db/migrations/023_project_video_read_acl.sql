-- Read-only project ACL primitives.  These functions intentionally grant no
-- table privileges: callers must still be authorized by their application
-- layer, while every project-scoped read can share this fail-closed predicate.

create or replace function project_actor_can_read(p_project_id uuid, p_actor text)
returns boolean
language sql
stable
security invoker
as $$
  select coalesce((
    select member_row.status = 'active'
       and (member_row.effective_until is null or member_row.effective_until > now())
    from research_project project_row
    join research_organization organization_row
      on organization_row.id = project_row.organization_id
    join lateral (
      select member.status, member.effective_until
      from research_project_member member
      where member.project_id = project_row.id
        and member.actor_id = p_actor
        and member.effective_from <= now()
      order by member.effective_from desc
      limit 1
    ) member_row on true
    where project_row.id = p_project_id
      and project_row.status = 'active'
      and organization_row.status = 'active'
  ), false);
$$;

create or replace function project_video_can_read(
  p_project_id uuid,
  p_actor text,
  p_video_id uuid
)
returns boolean
language sql
stable
security invoker
as $$
  select coalesce(
    p_project_id is not null
    and p_actor is not null
    and p_video_id is not null
    and project_actor_can_read(p_project_id, p_actor)
    and exists (
      select 1
      from project_video_inclusion inclusion_row
      where inclusion_row.project_id = p_project_id
        and inclusion_row.video_id = p_video_id
        and inclusion_row.status <> 'archived'
    ),
    false
  );
$$;

comment on function project_actor_can_read(uuid, text) is
  'Fail-closed project read predicate. It resolves the latest effective member row before checking status and expiry.';
comment on function project_video_can_read(uuid, text, uuid) is
  'Fail-closed project video read predicate. Requires active organization/project/member and a non-archived project inclusion.';
