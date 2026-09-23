-- Multi-project and multi-account foundation.
-- This migration deliberately creates no project relationships for legacy rows.
-- Canonical public identities remain in source_account; no credential material is
-- stored in these tables.

create table if not exists research_organization (
  id uuid primary key default gen_random_uuid(),
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text not null check (char_length(name) between 1 and 160),
  status text not null default 'active'
    check (status in ('active', 'suspended', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (slug)
);

create table if not exists research_project (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references research_organization(id) on delete restrict,
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name text not null check (char_length(name) between 1 and 160),
  status text not null default 'draft'
    check (status in ('draft', 'active', 'paused', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, slug),
  unique (id, organization_id)
);

create table if not exists research_project_member (
  project_id uuid not null references research_project(id) on delete cascade,
  actor_id text not null check (char_length(actor_id) between 3 and 254 and actor_id = lower(actor_id)),
  role text not null check (role in ('owner', 'admin', 'researcher', 'analyst', 'viewer')),
  status text not null default 'active' check (status in ('active', 'suspended', 'revoked')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (project_id, actor_id, effective_from),
  check (effective_until is null or effective_until > effective_from)
);

create index if not exists idx_research_project_member_active
  on research_project_member(project_id, actor_id, effective_until)
  where status = 'active';
-- Supports the actor-first lookup used to resolve the latest applicable ACL.
create index if not exists idx_research_project_member_actor_project_effective
  on research_project_member(actor_id, project_id, effective_from desc);

create table if not exists research_subject (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  parent_subject_id uuid,
  name text not null check (char_length(name) between 1 and 160),
  subject_type text not null check (subject_type in (
    'destination', 'ip', 'character', 'product', 'activity', 'brand', 'account', 'topic', 'other'
  )),
  status text not null default 'active' check (status in ('active', 'archived')),
  external_ref text check (external_ref is null or char_length(external_ref) <= 512),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  foreign key (parent_subject_id, project_id)
    references research_subject(id, project_id) on delete restrict
);

create unique index if not exists uq_research_subject_project_type_name
  on research_subject(project_id, subject_type, lower(name));
create index if not exists idx_research_subject_project_parent
  on research_subject(project_id, parent_subject_id);

create table if not exists project_account_relation (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  subject_id uuid,
  source_account_id uuid not null references source_account(id) on delete restrict,
  relation_type text not null check (relation_type in (
    'official', 'ip_character', 'employee_store', 'managed_matrix', 'authorized_partner',
    'unverified_partner', 'competitor', 'media_reference', 'ugc_reference', 'other'
  )),
  task_roles text[] not null check (cardinality(task_roles) > 0 and task_roles <@ array[
    'publish_channel', 'distribution_partner', 'benchmark_sample', 'comment_observer',
    'conversion_entry', 'research_reference'
  ]::text[]),
  purpose text check (purpose is null or char_length(purpose) <= 500),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  verification_status text not null default 'proposed'
    check (verification_status in ('proposed', 'pending', 'verified', 'rejected', 'revoked')),
  verified_by text check (verified_by is null or char_length(verified_by) between 3 and 254),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id),
  unique (id, project_id, source_account_id),
  foreign key (subject_id, project_id)
    references research_subject(id, project_id) on delete restrict,
  check (effective_until is null or effective_until > effective_from),
  check (verification_status <> 'verified' or verified_by is not null)
);

create unique index if not exists uq_project_account_relation_idempotency
  on project_account_relation(project_id, idempotency_key);
create index if not exists idx_project_account_relation_project_account
  on project_account_relation(project_id, source_account_id, verification_status, effective_until);
create index if not exists idx_project_account_relation_subject
  on project_account_relation(project_id, subject_id) where subject_id is not null;

create table if not exists account_group (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references research_project(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 160),
  group_type text not null check (group_type in ('matrix', 'campaign', 'cohort', 'other')),
  status text not null default 'active' check (status in ('active', 'archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, project_id)
);

create unique index if not exists uq_account_group_project_name
  on account_group(project_id, lower(name));

create table if not exists account_group_member (
  group_id uuid not null,
  project_id uuid not null,
  source_account_id uuid not null references source_account(id) on delete restrict,
  role text not null default 'member' check (role in ('owner', 'member', 'featured')),
  status text not null default 'active' check (status in ('active', 'removed')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (group_id, source_account_id, role, effective_from),
  foreign key (group_id, project_id)
    references account_group(id, project_id) on delete cascade,
  check (effective_until is null or effective_until > effective_from)
);

create index if not exists idx_account_group_member_project_account
  on account_group_member(project_id, source_account_id, effective_until)
  where status = 'active';

create table if not exists account_identity_link (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  left_account_id uuid not null references source_account(id) on delete restrict,
  right_account_id uuid not null references source_account(id) on delete restrict,
  relation_type text not null check (relation_type in ('same_brand', 'same_person', 'same_operator')),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  verification_status text not null default 'pending'
    check (verification_status in ('pending', 'verified', 'rejected', 'revoked')),
  verified_by text check (verified_by is null or char_length(verified_by) between 3 and 254),
  status text not null default 'active' check (status in ('active', 'revoked')),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (left_account_id < right_account_id),
  check (effective_until is null or effective_until > effective_from),
  check (verification_status <> 'verified' or verified_by is not null),
  check ((status = 'revoked') = (revoked_at is not null))
);

create unique index if not exists uq_account_identity_link_idempotency
  on account_identity_link(idempotency_key);
create index if not exists idx_account_identity_link_verified
  on account_identity_link(left_account_id, right_account_id, effective_until)
  where status = 'active' and verification_status = 'verified';

-- An identity link is only for a cross-platform real-world identity. Account
-- relationships within one platform belong in project_account_relation/group.
create or replace function enforce_account_identity_link_cross_platform()
returns trigger language plpgsql as $$
declare
  left_platform text;
  right_platform text;
begin
  select platform into left_platform from source_account where id = new.left_account_id;
  select platform into right_platform from source_account where id = new.right_account_id;
  if left_platform is null or right_platform is null or left_platform = right_platform then
    raise exception 'account_identity_link must join two distinct platforms';
  end if;
  return new;
end;
$$;
drop trigger if exists trg_account_identity_link_cross_platform on account_identity_link;
create trigger trg_account_identity_link_cross_platform
before insert or update of left_account_id, right_account_id on account_identity_link
for each row execute function enforce_account_identity_link_cross_platform();

create table if not exists account_authorization (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null,
  project_id uuid not null,
  idempotency_key text not null check (
    char_length(idempotency_key) between 8 and 128
    and idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
  ),
  project_account_relation_id uuid not null,
  source_account_id uuid not null references source_account(id) on delete restrict,
  provider text not null check (char_length(provider) between 1 and 80),
  authorization_kind text not null check (authorization_kind in ('project_account')),
  purpose text not null check (purpose in ('read_metrics', 'analyze_content', 'publish', 'sync')),
  field_allowlist text[] not null check (cardinality(field_allowlist) > 0),
  operation_allowlist text[] not null check (cardinality(operation_allowlist) > 0),
  credential_ref text not null check (
    char_length(credential_ref) between 1 and 512
    and credential_ref ~ '^(secret|vault|windmill)://[A-Za-z0-9][A-Za-z0-9/_-]*$'
  ),
  status text not null default 'draft' check (status in ('draft', 'active', 'expired', 'revoked')),
  granted_by text not null check (char_length(granted_by) between 3 and 254),
  evidence_ref text not null check (char_length(evidence_ref) between 1 and 512),
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (project_id, organization_id)
    references research_project(id, organization_id) on delete restrict,
  foreign key (project_account_relation_id, project_id, source_account_id)
    references project_account_relation(id, project_id, source_account_id) on delete restrict,
  check (effective_until is null or effective_until > effective_from),
  check ((status = 'revoked') = (revoked_at is not null)),
  check (metadata::text !~* '"(secret|token|password|api[_-]?key|cookie)"[[:space:]]*:')
);

create unique index if not exists uq_account_authorization_idempotency
  on account_authorization(project_id, idempotency_key);
create index if not exists idx_account_authorization_active
  on account_authorization(project_id, provider, purpose, effective_until)
  where status = 'active';

-- Any loss of verified business relationship must immediately make provider
-- grants beneath it unusable.  This is DB-enforced so a future adapter cannot
-- leave a still-active grant behind on a relation update.
create or replace function revoke_authorizations_for_inactive_relation()
returns trigger language plpgsql as $$
begin
  if new.verification_status <> 'verified'
     and old.verification_status is distinct from new.verification_status then
    update account_authorization
       set status = 'revoked',
           revoked_at = coalesce(revoked_at, now()),
           updated_at = now()
     where project_account_relation_id = new.id
       and status in ('draft', 'active');
  end if;
  return new;
end;
$$;
drop trigger if exists trg_revoke_authorizations_for_inactive_relation on project_account_relation;
create trigger trg_revoke_authorizations_for_inactive_relation
after update of verification_status on project_account_relation
for each row execute function revoke_authorizations_for_inactive_relation();

create or replace function enforce_authorization_relation_not_inactive()
returns trigger language plpgsql as $$
declare
  relation_status text;
begin
  if new.status = 'active' then
    select verification_status into relation_status
      from project_account_relation where id = new.project_account_relation_id for share;
    if relation_status is distinct from 'verified' then
      raise exception 'authorization can only be active for a verified relation';
    end if;
  elsif new.status = 'draft' then
    select verification_status into relation_status
      from project_account_relation where id = new.project_account_relation_id for share;
    if relation_status in ('rejected', 'revoked') then
      raise exception 'authorization cannot be draft for a rejected or revoked relation';
    end if;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_enforce_authorization_relation_not_inactive on account_authorization;
create trigger trg_enforce_authorization_relation_not_inactive
before insert or update of status, project_account_relation_id on account_authorization
for each row execute function enforce_authorization_relation_not_inactive();

comment on table project_account_relation is
  'Project-scoped business relationship to one canonical public source_account; does not grant provider access.';
comment on table account_authorization is
  'Authorization metadata only. credential_ref is an opaque controlled-backend reference; never store secrets, tokens, cookies or raw credentials here.';
