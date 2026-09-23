-- Keyset pagination for the project-scoped, verified account matrix.
-- Only the relation UUID is used as a stable cursor; canonical account
-- nickname and other public facts may change between page requests.
create index if not exists idx_project_account_relation_verified_cursor
  on project_account_relation(project_id, id)
  where verification_status = 'verified';
