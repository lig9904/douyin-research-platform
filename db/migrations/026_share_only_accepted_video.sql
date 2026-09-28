-- A discovery candidate, including one explicitly rejected by the source
-- project, is not evidence that the source project chose to share.  Keep the
-- existing grant model but require an affirmative project decision per video.
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
        and inclusion_row.status = 'accepted'
        and video.availability_status = 'available'
    ), false
  );
$$;
comment on function project_shared_video_can_read(uuid, uuid, text, uuid) is
  'Fail-closed, accepted cross-project grant for source-approved public video evidence only.';
revoke all on function project_shared_video_can_read(uuid, uuid, text, uuid) from public;
