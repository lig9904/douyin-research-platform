-- DB RLS probe, intentionally separate from browser/IdP authentication.
\set ON_ERROR_STOP on
BEGIN;
INSERT INTO workspace (id, name, owner) VALUES ('local-security-acl', 'Local security ACL drill', 'admin')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;
INSERT INTO usr (workspace_id, username, email, is_admin, operator) VALUES
 ('local-security-acl','admin','admin@local-security.invalid',true,true),
 ('local-security-acl','reviewer','reviewer@local-security.invalid',false,false),
 ('local-security-acl','viewer','viewer@local-security.invalid',false,false)
ON CONFLICT (workspace_id,username) DO UPDATE SET email=EXCLUDED.email;
INSERT INTO group_ (workspace_id,name,summary) VALUES ('local-security-acl','content-reviewers','synthetic reviewer group') ON CONFLICT DO NOTHING;
INSERT INTO usr_to_group (workspace_id,group_,usr) VALUES ('local-security-acl','content-reviewers','reviewer') ON CONFLICT DO NOTHING;
INSERT INTO folder (workspace_id,name,display_name,owners,extra_perms) VALUES
 ('local-security-acl','admin-only','Admin only',ARRAY['u/admin'],'{}'::jsonb),
 ('local-security-acl','review-direct','Reviewer direct',ARRAY['u/admin'],'{"u/reviewer":true}'::jsonb),
 ('local-security-acl','review-group','Reviewer group',ARRAY['u/admin'],'{"g/content-reviewers":true}'::jsonb),
 ('local-security-acl','viewer-read','Viewer read',ARRAY['u/admin'],'{"u/viewer":true}'::jsonb)
ON CONFLICT (workspace_id,name) DO UPDATE SET owners=EXCLUDED.owners,extra_perms=EXCLUDED.extra_perms;
SET LOCAL ROLE windmill_user;
SELECT set_config('session.user','admin',true); SELECT set_config('session.pgroups','u/admin',true);
DO $$ BEGIN IF (SELECT count(*) FROM folder WHERE workspace_id='local-security-acl') <> 4 THEN RAISE EXCEPTION 'admin visibility mismatch'; END IF; END $$;
SELECT set_config('session.user','reviewer',true); SELECT set_config('session.pgroups','u/reviewer,g/content-reviewers',true);
DO $$ BEGIN IF (SELECT count(*) FROM folder WHERE workspace_id='local-security-acl') <> 2 THEN RAISE EXCEPTION 'reviewer visibility mismatch'; END IF; END $$;
SELECT set_config('session.user','viewer',true); SELECT set_config('session.pgroups','u/viewer',true);
DO $$ BEGIN
 IF (SELECT count(*) FROM folder WHERE workspace_id='local-security-acl') <> 1 THEN RAISE EXCEPTION 'viewer visibility mismatch'; END IF;
 UPDATE folder SET display_name='must-not-write' WHERE workspace_id='local-security-acl' AND name='viewer-read';
 IF FOUND THEN RAISE EXCEPTION 'viewer unexpectedly updated folder'; END IF;
END $$;
ROLLBACK;
