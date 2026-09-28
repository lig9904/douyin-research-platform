-- Follow-up to 033: a self-reported publication cannot predate its locked
-- action, and the accepted reference and creation timestamp cannot be changed.
-- Existing publication rows remain historical (published_at NULL); they do
-- not become verified preregistered experiments by migration.

alter table project_publication_record
  add column if not exists published_at timestamptz;

create or replace function enforce_project_decision_card_experiment_contract()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.evaluation_metric is null or new.success_rule is null
      or new.observation_window_days is null or new.comparison_basis is null
      or new.confounder_plan is null then
      raise exception 'new project decision card requires preregistered experiment contract';
    end if;
  elsif new.status='reviewed' and old.status is distinct from 'reviewed'
    and old.evaluation_metric is null then
    raise exception 'legacy project decision card cannot be reviewed without preregistration';
  elsif (new.evaluation_metric, new.success_rule, new.observation_window_days,
         new.comparison_basis, new.confounder_plan)
        is distinct from
        (old.evaluation_metric, old.success_rule, old.observation_window_days,
         old.comparison_basis, old.confounder_plan) then
    raise exception 'project decision card experiment contract is immutable';
  elsif old.evaluation_metric is not null
    and (new.hypothesis, new.reference_point, new.adaptation_difference,
         new.decision, new.subject_id, new.source_video_id, new.created_at)
        is distinct from
        (old.hypothesis, old.reference_point, old.adaptation_difference,
         old.decision, old.subject_id, old.source_video_id, old.created_at) then
    raise exception 'preregistered project decision hypothesis and source are immutable';
  end if;
  return new;
end;
$$;

create or replace function enforce_project_publication_provenance()
returns trigger language plpgsql as $$
declare
  card_created_at timestamptz;
begin
  if tg_op = 'INSERT' then
    if new.decision_card_id is null or new.platform is null
      or new.account_reference is null or new.platform_content_id is null
      or new.content_version is null or new.distribution_mode is null
      or new.published_at is null then
      raise exception 'new project publication requires linked action and self-reported platform provenance';
    end if;
    select card.created_at into card_created_at
    from project_decision_card card
    where card.id=new.decision_card_id and card.project_id=new.project_id
      and card.evaluation_metric is not null
      and ((card.decision='adopt' and card.status in ('active','adopted'))
        or (card.decision='observe' and card.status='observing'));
    if card_created_at is null then
      raise exception 'new project publication requires active preregistered adopted or observing action';
    end if;
    if new.published_at < card_created_at or new.published_at > now()
      or new.publication_date <> (new.published_at at time zone 'Asia/Shanghai')::date then
      raise exception 'project publication time must follow preregistration and match local publication date';
    end if;
  elsif (new.platform, new.account_reference, new.platform_content_id,
         new.content_version, new.distribution_mode, new.decision_card_id,
         new.published_at)
        is distinct from
        (old.platform, old.account_reference, old.platform_content_id,
         old.content_version, old.distribution_mode, old.decision_card_id,
         old.published_at) then
    raise exception 'project publication provenance is immutable';
  end if;
  return new;
end;
$$;

comment on column project_publication_record.published_at is
  'Human-entered platform publication time, not independently verified; NULL marks a pre-034 historical row.';
