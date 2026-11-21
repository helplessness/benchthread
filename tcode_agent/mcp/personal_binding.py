"""Ownership of a desktop personal MCP binding (ordinary MCPs have no marker)."""


def is_personal(entry):
    return "personal_artifact_id" in entry or "personal_artifact_owner" in entry


def allows_owner(entry, tenant_id, user_id):
    return not is_personal(entry) or bool(
        tenant_id and user_id and entry.get("personal_artifact_owner") == [tenant_id, user_id]
    )


def allows_workspace(entry):
    if not is_personal(entry):
        return True
    from ..runtime.workspace_registry import plugin_workspace
    from ..workspace_lifecycle import managed_workspace_owner_from_path
    from ..server.state import ServerState

    owner = managed_workspace_owner_from_path(plugin_workspace.get(), server_workdir=ServerState.get_workdir())
    return bool(owner and allows_owner(entry, *owner[:2]))
