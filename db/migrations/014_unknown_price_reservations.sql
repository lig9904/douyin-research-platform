-- Known-price subtotal is not a supplier bill. Preserve historical amounts.
alter table daily_budget
  add column if not exists unknown_price_requests integer not null default 0
  check (unknown_price_requests >= 0);

comment on column daily_budget.spent_cost is
  'Known estimated reservation subtotal; not reconciled spend. See unknown_price_requests.';
comment on column daily_budget.unknown_price_requests is
  'Reservations with no quote after migration 014; historical unknown reservations cannot be inferred.';
