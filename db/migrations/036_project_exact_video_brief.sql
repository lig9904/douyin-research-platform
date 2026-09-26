-- Allow a project researcher to seed previously published, exact Douyin IDs.
-- These remain candidates; public metadata collection never grants ASR/L3 approval.

alter table research_brief drop constraint if exists research_brief_source_check;
alter table research_brief add constraint research_brief_source_check
  check (source_type in ('low_fan', 'keyword', 'account', 'video_ids'));

alter table research_brief drop constraint if exists research_brief_target_check;
alter table research_brief add constraint research_brief_target_check check (
  (source_type = 'low_fan' and target is null) or
  (source_type in ('keyword', 'account') and target is not null and
   char_length(target) between 1 and 120) or
  (source_type = 'video_ids' and target is not null and
   char_length(target) between 15 and 519 and
   target ~ '^[0-9]{15,25}(,[0-9]{15,25})*$')
);

alter table research_brief drop constraint if exists research_brief_window_check;
alter table research_brief add constraint research_brief_window_check check (
  (time_window_hours in (24,72,168,720) or
   (source_type = 'video_ids' and time_window_hours = 0)) and
  (source_type <> 'video_ids' or time_window_hours = 0) and
  (source_type <> 'low_fan' or time_window_hours in (24,72,168))
);

alter table research_brief drop constraint if exists research_brief_item_check;
alter table research_brief add constraint research_brief_item_check check (
  max_items between 1 and 20 and
  (source_type <> 'low_fan' or max_items <= 5) and
  (depth not in ('media','review_ready') or max_items <= 5) and
  (source_type <> 'video_ids' or
   (max_items = array_length(string_to_array(target, ','), 1) and depth = 'metadata'))
);

alter table research_brief drop constraint if exists research_brief_cadence_check;
alter table research_brief add constraint research_brief_cadence_check check (
  (cadence_hours is null or cadence_hours in (6,12,24)) and
  (source_type <> 'video_ids' or cadence_hours is null)
);

alter table research_brief drop constraint if exists research_brief_exact_project_check;
alter table research_brief add constraint research_brief_exact_project_check
  check (source_type <> 'video_ids' or (project_id is not null and subject_id is not null));
