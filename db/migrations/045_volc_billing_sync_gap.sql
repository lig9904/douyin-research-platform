-- Non-monetary reconciliation state. A missing/degraded supplier response must
-- never become a zero-value supplier_daily_spend row. Pending days survive the
-- rolling scheduler window and are retried until a valid snapshot is saved.

create table if not exists volc_billing_sync_gap (
  account_scope text not null check (account_scope ~ '^payer:[0-9]{1,18}$'),
  billing_date date not null,
  status text not null default 'pending' check (status in ('pending', 'resolved')),
  first_seen_at timestamptz not null default now(),
  last_attempt_at timestamptz,
  attempt_count integer not null default 0 check (attempt_count >= 0),
  next_attempt_after timestamptz,
  last_success_at timestamptz,
  resolved_at timestamptz,
  last_result_code text check (last_result_code in (
    'bill_unavailable', 'snapshot_rejected', 'scope_conflict', 'execution_failed',
    'snapshot_written', 'already_final'
  )),
  primary key (account_scope, billing_date),
  check ((status = 'resolved') = (resolved_at is not null)),
  check (status = 'pending' or next_attempt_after is null)
);

create index if not exists idx_volc_billing_sync_gap_pending
  on volc_billing_sync_gap(account_scope, next_attempt_after, last_attempt_at, billing_date)
  where status = 'pending';

comment on table volc_billing_sync_gap is
  'Non-monetary Volcano account/day reconciliation queue. Pending is not zero spend; resolved does not mean supplier-finalized.';
