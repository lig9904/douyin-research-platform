-- Supplier-reported daily totals are snapshots, not a per-request bill allocation.
create table if not exists supplier_daily_spend (
  provider text not null,
  account_scope text not null default 'default',
  billing_date date not null,
  cost_currency text not null,
  billing_timezone text not null,
  total_cost numeric not null check (total_cost >= 0),
  balance_cost numeric not null check (balance_cost >= 0),
  free_credit_cost numeric not null check (free_credit_cost >= 0),
  total_requests integer not null check (total_requests >= 0),
  paid_requests integer not null check (paid_requests >= 0 and paid_requests <= total_requests),
  fetched_at timestamptz not null,
  primary key (provider, account_scope, billing_date, cost_currency)
);

comment on table supplier_daily_spend is
  'Supplier-reported daily cost snapshots. They are not allocated to individual API calls.';
