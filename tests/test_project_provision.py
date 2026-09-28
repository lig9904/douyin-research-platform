from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/provision-research-project.py"
SPEC = importlib.util.spec_from_file_location("provision_research_project_test", SCRIPT)
assert SPEC and SPEC.loader
provisioning = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provisioning
SPEC.loader.exec_module(provisioning)
DSN = os.environ.get("TEST_DATABASE_URL")


def _payload() -> dict[str, str]:
    return {
        "organization_slug": "test-org",
        "organization_name": "测试组织",
        "project_slug": "test-project",
        "project_name": "测试项目",
        "operator": "operator@example.com",
        "owner": "owner@example.com",
        "project_status": "active",
    }


def test_provision_validation_rejects_ambiguous_identity_and_target() -> None:
    with pytest.raises(provisioning.ProvisionError):
        provisioning._validate_slug("../test", "slug")
    with pytest.raises(provisioning.ProvisionError):
        provisioning._validate_email("not-an-email")
    with pytest.raises(provisioning.ProvisionError):
        provisioning._validate_name("   ", "name")
    assert provisioning._validate_email(" Owner@Example.com ") == "owner@example.com"


def test_apply_requires_exact_target_before_database_connection(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RESEARCH_PROVISION_DSN", "dbname=expected_db")
    result = provisioning.main(
        [
            "--expected-db", "expected_db", "--organization-slug", "test-org",
            "--organization-name", "测试组织", "--project-slug", "test-project",
            "--project-name", "测试项目", "--operator-email", "operator@example.com",
            "--owner-email", "owner@example.com", "--apply", "--confirm", "wrong/project",
        ]
    )
    assert result == 2
    assert "exact organization/project" in capsys.readouterr().err


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_provision_preview_apply_and_duplicate_refusal() -> None:
    assert DSN
    namespace = f"project_provision_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(
                """
                create table research_organization (
                  id uuid primary key default gen_random_uuid(),
                  slug text not null unique, name text not null, status text not null
                );
                create table research_project (
                  id uuid primary key default gen_random_uuid(),
                  organization_id uuid not null references research_organization(id),
                  slug text not null, name text not null, status text not null,
                  unique(organization_id,slug)
                );
                create table research_project_member (
                  project_id uuid not null references research_project(id),
                  actor_id text not null, role text not null, status text not null
                );
                create table research_user_action (
                  idempotency_key uuid primary key, actor text not null,
                  action_type text not null, payload_hash text not null,
                  outcome jsonb not null
                );
                """,
                prepare=False,
            )
            payload = _payload()
            preview = provisioning.provision(conn, payload, apply=False)
            assert preview["operation"] == "create_organization_and_project"
            assert conn.execute("select count(*) from research_project").fetchone()[0] == 0

            applied = provisioning.provision(conn, payload, apply=True)
            assert applied["project_id"]
            assert conn.execute(
                "select status from research_project where id=%s", (applied["project_id"],)
            ).fetchone()[0] == "active"
            assert conn.execute(
                "select actor_id,role,status from research_project_member"
            ).fetchone() == ("owner@example.com", "owner", "active")
            assert conn.execute(
                "select actor,action_type from research_user_action"
            ).fetchone() == ("operator@example.com", "operator_project_provision")
            with pytest.raises(provisioning.ProvisionError, match="already exists"):
                provisioning.provision(conn, payload, apply=True)
            assert conn.execute("select count(*) from research_project_member").fetchone()[0] == 1
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
