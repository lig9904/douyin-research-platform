#!/usr/bin/env python3
"""Emit the shared audit queries for psql; no connection or credentials."""
import importlib.util
from pathlib import Path


def build_sql():
    path = Path(__file__).with_name("test-server-restored-business-audit.py")
    spec = importlib.util.spec_from_file_location("restore_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    invalid = " + ".join(f"({sql})" for sql in module.BAD_LINK_SQL.values())
    return f"""begin transaction isolation level repeatable read read only;
set local search_path=public;
do $$ begin
  if current_database() <> 'test_server_research_restore' then
    raise exception 'fixed restore database required';
  end if;
end $$;
select 'RESTORED_BUSINESS_SQL ' || json_build_object(
 'status', case when ({invalid}) <> 0 then 'invalid_associations'
                when ({module.FULL_CHAIN_SQL}) = 0 then 'insufficient_samples'
                else 'business_chain_present' end,
 'invalid_link_count', ({invalid}),
 'full_chain_videos', ({module.FULL_CHAIN_SQL}),
 'v1_release_accepted', false)::text;
rollback;
"""


if __name__ == "__main__":
    print(build_sql())
