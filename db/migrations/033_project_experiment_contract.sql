-- Preregister a reusable project experiment before an action is executed.
-- Existing 028 records remain readable as legacy evidence; new rows cannot
-- silently inherit invented goals or publication provenance.

alter table project_decision_card
  add column if not exists evaluation_metric text,
  add column if not exists success_rule text,
  add column if not exists observation_window_days integer,
  add column if not exists comparison_basis text,
  add column if not exists confounder_plan text,
  add column if not exists review_verdict text;

do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_decision_card'::regclass
    and conname='project_decision_card_experiment_contract_check') then
    alter table project_decision_card
  add constraint project_decision_card_experiment_contract_check check (
    (evaluation_metric is null and success_rule is null and observation_window_days is null
      and comparison_basis is null and confounder_plan is null)
    or
    (num_nulls(evaluation_metric, success_rule, observation_window_days,
      comparison_basis, confounder_plan) = 0
      and char_length(btrim(evaluation_metric)) between 1 and 160
      and char_length(btrim(success_rule)) between 1 and 500
      and observation_window_days between 1 and 90
      and char_length(btrim(comparison_basis)) between 1 and 500
      and char_length(btrim(confounder_plan)) between 1 and 500)
  );
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_decision_card'::regclass
    and conname='project_decision_card_review_verdict_check') then
    alter table project_decision_card
      add constraint project_decision_card_review_verdict_check check (
        (review_verdict is null or (evaluation_metric is not null and status='reviewed'
          and review_verdict in ('supported','not_supported','inconclusive')))
        and (evaluation_metric is null or
          ((status='reviewed') = (review_verdict is not null)))
      );
  end if;
end $$;

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
         new.decision, new.subject_id)
        is distinct from
        (old.hypothesis, old.reference_point, old.adaptation_difference,
         old.decision, old.subject_id) then
    raise exception 'preregistered project decision hypothesis is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_decision_card_experiment_contract on project_decision_card;
create trigger trg_project_decision_card_experiment_contract
before insert or update on project_decision_card
for each row execute function enforce_project_decision_card_experiment_contract();

alter table project_publication_record
  add column if not exists platform text,
  add column if not exists account_reference text,
  add column if not exists platform_content_id text,
  add column if not exists content_version text,
  add column if not exists distribution_mode text;

do $$ begin
  if not exists (select 1 from pg_constraint where conrelid='project_publication_record'::regclass
    and conname='project_publication_provenance_check') then
    alter table project_publication_record
  add constraint project_publication_provenance_check check (
    (platform is null and account_reference is null and platform_content_id is null
      and content_version is null and distribution_mode is null)
    or
    (num_nulls(platform, account_reference, platform_content_id,
      content_version, distribution_mode) = 0
      and platform in ('douyin', 'xiaohongshu', 'kuaishou', 'bilibili', 'other')
      and char_length(btrim(account_reference)) between 1 and 160
      and char_length(btrim(platform_content_id)) between 1 and 160
      and char_length(btrim(content_version)) between 1 and 160
      and distribution_mode in ('organic', 'paid', 'mixed'))
  );
  end if;
end $$;

create or replace function enforce_project_publication_provenance()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.decision_card_id is null or new.platform is null
      or new.account_reference is null or new.platform_content_id is null
      or new.content_version is null or new.distribution_mode is null then
      raise exception 'new project publication requires linked action and real platform provenance';
    end if;
    if not exists (
      select 1 from project_decision_card card
      where card.id=new.decision_card_id and card.project_id=new.project_id
        and card.evaluation_metric is not null
        and ((card.decision='adopt' and card.status in ('active','adopted'))
          or (card.decision='observe' and card.status='observing'))
    ) then
      raise exception 'new project publication requires active preregistered adopted or observing action';
    end if;
  elsif (new.platform, new.account_reference, new.platform_content_id,
         new.content_version, new.distribution_mode, new.decision_card_id)
        is distinct from
        (old.platform, old.account_reference, old.platform_content_id,
         old.content_version, old.distribution_mode, old.decision_card_id) then
    raise exception 'project publication provenance is immutable';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_project_publication_provenance on project_publication_record;
create trigger trg_project_publication_provenance
before insert or update on project_publication_record
for each row execute function enforce_project_publication_provenance();

create or replace function reject_preregistered_publication_change()
returns trigger language plpgsql as $$
begin
  if old.platform_content_id is not null then
    raise exception 'preregistered project publication is immutable';
  end if;
  return old;
end;
$$;
drop trigger if exists trg_preregistered_publication_immutable on project_publication_record;
create trigger trg_preregistered_publication_immutable
before update or delete on project_publication_record
for each row execute function reject_preregistered_publication_change();

create unique index if not exists uq_project_publication_platform_content
  on project_publication_record(project_id, platform, platform_content_id)
  where platform_content_id is not null;

comment on column project_decision_card.success_rule is
  'Human-preregistered falsifiable success rule. Never inferred from outcome data.';
comment on column project_publication_record.platform_content_id is
  'Actual published platform work ID, not a planned or simulated content ID.';
