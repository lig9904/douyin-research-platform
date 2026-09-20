-- One-time test-server correction from TikHub account usage log, 2026-09-20.
-- Two calls: low-fan USD 0.001 + batch detail USD 0.050 = USD 0.051.
-- Retain original entries and append the correction provenance. No API calls.
\set ON_ERROR_STOP on
begin;
select pg_advisory_xact_lock(hashtext('douyin_research:manual_golden_intake'));
do $$
begin
  if current_database() <> 'douyin_research' then
    raise exception 'wrong target database';
  end if;
  if not exists (
    select 1 from external_api_call where id=2
      and endpoint_key='douyin.app.multi_video_v2' and provider='tikhub'
      and status='success' and not cached
      and started_at='2026-09-20 13:12:27.409317+00'
      and actual_cost in (0.001,0.05)
  ) then
    raise exception 'expected test call does not match';
  end if;
  if not exists (select 1 from pipeline_run
      where id='f779e998-632b-4d7f-9fd5-41d60e16ffe6'
      and status='success' and api_cost in (0,0.051)) then
    raise exception 'expected test run does not match';
  end if;
  if not exists (select 1 from daily_budget
      where budget_date='2026-09-20' and provider='tikhub'
      and budget_key='golden-local' and used_requests=2
      and spent_cost in (0.002,0.051)) then
    raise exception 'expected budget row does not match';
  end if;
end $$;
update external_api_call
set actual_cost=0.050,
    metadata=coalesce(metadata,'{}'::jsonb) || jsonb_build_object(
      'cost_basis','provider_bill_reconciled',
      'price_source','tikhub.usage_log',
      'pricing_version','usage-log-2026-09-20',
      'previous_actual_cost',actual_cost,
      'reconciled_at',now(),
      'reconciliation_note','Account usage log: one batch detail request USD 0.050')
where id=2 and actual_cost=0.001;
update pipeline_run
set api_cost=0.051, cost_currency='USD',
    summary=summary || jsonb_build_object(
      'cost_reconciliation','tikhub-usage-log-2026-09-20',
      'previous_api_cost',api_cost,'previous_cost_currency',cost_currency)
where id='f779e998-632b-4d7f-9fd5-41d60e16ffe6' and api_cost=0;
update daily_budget
set spent_cost=0.051, updated_at=now()
where budget_date='2026-09-20' and provider='tikhub'
  and budget_key='golden-local' and spent_cost=0.002 and used_requests=2;
commit;
select endpoint_key,estimated_cost,actual_cost,metadata->>'cost_basis' as cost_basis
from external_api_call where id in (1,2) order by id;
