"""Permission catalogue and default role definitions.

Code checks *permissions* (``messages.send``), never role names. Roles are
per-organization bundles of permissions, seeded from DEFAULT_ROLES when an
organization is created and editable afterwards without code changes.

Adding a permission: add it to PERMISSIONS, decide which default roles get
it, and ship a migration calling ``sync_permission_catalogue``.
"""

from typing import Final

# code -> human description
PERMISSIONS: Final[dict[str, str]] = {
    "organization.view": "View organization profile",
    "organization.manage": "Edit organization profile and settings",
    "members.view": "View members",
    "members.manage": "Invite, remove and change roles of members",
    "roles.manage": "Create and edit roles",
    "businesses.manage": "Create and edit businesses (workspaces) and grant access to them",
    "teams.view": "View teams",
    "teams.manage": "Create and edit teams",
    "numbers.view": "View WhatsApp accounts and phone numbers",
    "numbers.manage": "Connect and synchronise WhatsApp accounts and numbers",
    "integrations.manage": "Manage provider credentials and integrations",
    "templates.view": "View message templates",
    "templates.manage": "Create, edit, submit and delete message templates",
    "contacts.view": "View contacts",
    "contacts.manage": "Create, edit and tag contacts",
    "conversations.view": "View conversations and messages",
    "conversations.assign": "Assign conversations to members or teams",
    "messages.send": "Send messages",
    "campaigns.view": "View campaigns",
    "campaigns.manage": "Create and edit campaigns",
    "campaigns.launch": "Launch, pause, resume and cancel campaigns",
    "analytics.view": "View analytics",
    "reports.export": "Export operational reports (CSV)",
    "billing.view": "View usage costs and billing",
    "billing.manage": "Manage pricing rules and billing settings",
    "audit.view": "View audit logs",
    "webhooks.view": "View raw webhook events",
    "webhooks.replay": "Replay webhook events",
    "api_keys.manage": "Create and revoke API keys",
    "notifications.view": "View own in-app notifications",
    "notifications.manage": "Mark own notifications read and manage own notification preferences",
}

_VIEW_ONLY = {code for code in PERMISSIONS if code.endswith(".view")} - {
    "audit.view",
    "billing.view",
    "webhooks.view",
    "members.view",
}

DEFAULT_ROLES: Final[dict[str, dict[str, object]]] = {
    "owner": {
        "name": "Organization Owner",
        "description": "Full control, including billing and access management.",
        "permissions": set(PERMISSIONS),
    },
    "admin": {
        "name": "Admin",
        "description": "Full operational control except billing management.",
        "permissions": set(PERMISSIONS) - {"billing.manage"},
    },
    "manager": {
        "name": "Manager",
        "description": "Runs messaging operations: templates, campaigns, assignment.",
        "permissions": _VIEW_ONLY
        | {
            "members.view",
            "teams.manage",
            "templates.manage",
            "contacts.manage",
            "conversations.assign",
            "messages.send",
            "campaigns.manage",
            "campaigns.launch",
            "billing.view",
            "reports.export",
            "notifications.manage",
        },
    },
    "agent": {
        "name": "Agent",
        "description": "Handles conversations and replies to customers.",
        "permissions": {
            "organization.view",
            "numbers.view",
            "templates.view",
            "contacts.view",
            "contacts.manage",
            "conversations.view",
            "messages.send",
            "teams.view",
            "notifications.view",
            "notifications.manage",
        },
    },
    "viewer": {
        "name": "Viewer",
        "description": "Read-only access to operational data.",
        "permissions": set(_VIEW_ONLY) | {"notifications.manage"},
    },
}

OWNER_ROLE_KEY: Final = "owner"


def validate_codes(codes: set[str]) -> None:
    unknown = codes - set(PERMISSIONS)
    if unknown:
        raise ValueError(f"unknown permission codes: {sorted(unknown)}")


for _role in DEFAULT_ROLES.values():
    validate_codes(_role["permissions"])  # type: ignore[arg-type]
