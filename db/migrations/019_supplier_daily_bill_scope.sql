alter table supplier_daily_spend
  add column if not exists bill_scope_key text not null default 'account',
  add column if not exists scope_kind text not null default 'account_total',
  add column if not exists scope_label text not null default '账户总费用',
  add column if not exists payable_cost numeric,
  add column if not exists paid_cost numeric,
  add column if not exists unpaid_cost numeric,
  add column if not exists billing_finality text not null default 'preliminary',
  add column if not exists source_warning text;

alter table supplier_daily_spend alter column balance_cost drop not null;
alter table supplier_daily_spend alter column free_credit_cost drop not null;
alter table supplier_daily_spend alter column total_requests drop not null;
alter table supplier_daily_spend alter column paid_requests drop not null;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_total_cost_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_pkey;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_scope_kind_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_finality_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_request_pair_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_scope_key_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_bill_amounts_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_payable_total_check;
alter table supplier_daily_spend drop constraint if exists supplier_daily_spend_source_warning_check;
alter table supplier_daily_spend
  add constraint supplier_daily_spend_scope_kind_check
    check (scope_kind in ('account_total', 'product_subset')),
  add constraint supplier_daily_spend_finality_check
    check (billing_finality in ('preliminary', 'final')),
  add constraint supplier_daily_spend_request_pair_check
    check ((total_requests is null and paid_requests is null) or
           (total_requests is not null and paid_requests is not null and
            total_requests >= 0 and paid_requests >= 0 and paid_requests <= total_requests)),
  add constraint supplier_daily_spend_scope_key_check
    check ((scope_kind = 'account_total' and bill_scope_key = 'account') or
           (scope_kind = 'product_subset' and bill_scope_key like 'product:%' and length(bill_scope_key) > 8)),
  add constraint supplier_daily_spend_bill_amounts_check
    check ((payable_cost is null and paid_cost is null and unpaid_cost is null) or
           (payable_cost is not null and paid_cost is not null and unpaid_cost is not null)),
  add constraint supplier_daily_spend_payable_total_check
    check (payable_cost is null or total_cost = payable_cost),
  add constraint supplier_daily_spend_source_warning_check
    check (source_warning is null or length(source_warning) between 1 and 1000),
  add primary key (provider, account_scope, bill_scope_key, billing_date, cost_currency);

comment on table supplier_daily_spend is
  'Supplier-reported daily bill snapshots by explicit account or product scope. Not per-request allocation.';
comment on column supplier_daily_spend.total_cost is
  'Signed supplier bill amount for the declared scope; refunds or adjustments may be negative.';
comment on column supplier_daily_spend.bill_scope_key is
  'Stable scope identity such as account or product:<provider product code>.';
