"""Project-scoped account relationship rules.

This package is deliberately persistence-agnostic.  Database and UI adapters
must validate their inputs here before they decide what a project may see or
ask a provider to do.
"""

from .account_rules import (
    AccountAuthorization,
    AccountIdentityLink,
    AuthorizationStatus,
    AuthorizationPurpose,
    ProjectAccountRelation,
    ProjectAccountRole,
    RelationKind,
    VerificationStatus,
    IdentityLinkStatus,
    active_relations,
    can_use_private_account_data,
    can_use_project_account,
    dedupe_external_call_ids,
    dedupe_work_ids,
    make_identity_link,
)

__all__ = [
    "AccountAuthorization",
    "AccountIdentityLink",
    "AuthorizationStatus",
    "AuthorizationPurpose",
    "ProjectAccountRelation",
    "ProjectAccountRole",
    "RelationKind",
    "VerificationStatus",
    "IdentityLinkStatus",
    "active_relations",
    "can_use_private_account_data",
    "can_use_project_account",
    "dedupe_external_call_ids",
    "dedupe_work_ids",
    "make_identity_link",
]
