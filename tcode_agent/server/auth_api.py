"""Authentication, tenant membership and tenant lifecycle endpoints.

Routes retain their original paths and are registered by app.py.
"""

import asyncio
import json
import shutil
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..runtime.sandbox import get_sandbox_runtime
from ..runtime.shared_tracker import SharedTracker
from ..scheduled_tasks import get_scheduled_task_service
from ..session.manager import SessionManager
from . import workdirs as workdir_utils
from .auth import (
    auth_cookie_name,
    auth_required,
    change_account_password,
    desktop_password_setup_pending,
    current_user_from_request,
    previously_used_desktop_accounts,
    CurrentUserContext,
    add_tenant_member,
    authenticate,
    clear_auth_cookie,
    clear_login_session,
    create_login_session,
    create_tenant,
    create_user_account,
    delete_tenant_record,
    list_tenant_members,
    list_user_tenants,
    remove_tenant_member,
    require_current_user,
    serialize_user,
    set_auth_cookie,
    set_tenant_status,
    switch_login_session_tenant,
    update_tenant_member_role,
    update_tenant_name,
    validate_tenant_deletion,
)
from .kanban import KanbanHiddenStore
from .state import ServerState
from .wiki_shares import WikiShareStore

router = APIRouter()


def _session_user(user: CurrentUserContext) -> dict[str, Any]:
    result = serialize_user(user)
    if auth_required() and user.email and user.tenant_status == "active":
        result["password_mode"] = "change"
    return result


@router.post("/api/auth/login")
async def auth_login(payload: dict[str, Any], request: Request):
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    if not email or not password:
        raise HTTPException(400, "email and password are required")
    gate = getattr(request.state, "desktop_gate", None)
    if gate and current_user_from_request(request) and email.lower() not in previously_used_desktop_accounts():
        raise HTTPException(409, "请先退出当前账号，再登录其他账号")
    user = authenticate(email, password)
    session_id = create_login_session(user)
    if gate:
        gate.set_automatic_entry(False)
    clear_login_session(str(request.cookies.get(auth_cookie_name()) or ""))
    response = Response(
        content=json.dumps({"user": _session_user(user)}),
        media_type="application/json",
    )
    set_auth_cookie(request, response, session_id)
    return response


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    gate = getattr(request.state, "desktop_gate", None)
    if gate:
        gate.set_automatic_entry(False)
    clear_login_session(str(request.cookies.get(auth_cookie_name()) or ""))
    response = Response(
        content=json.dumps({"status": "ok"}),
        media_type="application/json",
    )
    clear_auth_cookie(response)
    return response


@router.get("/api/auth/desktop-accounts")
async def desktop_accounts(request: Request):
    gate = getattr(request.state, "desktop_gate", None)
    if not gate:
        raise HTTPException(404)
    return {
        "accounts": previously_used_desktop_accounts(),
        "local_workspace": gate.local_user is not None and not gate.local_user.email,
        "can_register": gate.local_user is not None and current_user_from_request(request) is None,
    }


@router.post("/api/auth/desktop-accounts", status_code=201)
async def register_desktop_account(payload: dict[str, Any], request: Request):
    gate = getattr(request.state, "desktop_gate", None)
    if not gate:
        raise HTTPException(404)
    if current_user_from_request(request):
        raise HTTPException(409, "请先退出当前账号")
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(400, "请填写邮箱和密码")
    account = create_user_account(
        tenant_id=gate.local_user.tenant_id,
        user_id=f"local-{uuid4().hex}",
        email=email,
        password=password,
        role="member",
    )
    return {"user": serialize_user(account)}


@router.post("/api/auth/local-workspace")
async def enter_local_workspace(request: Request):
    gate = getattr(request.state, "desktop_gate", None)
    if not gate or gate.local_user.email:
        raise HTTPException(404)
    if current_user_from_request(request):
        raise HTTPException(409, "请先退出当前账号")
    gate.set_automatic_entry(True)
    response = Response(status_code=204)
    clear_auth_cookie(response)
    return response


@router.post("/api/auth/desktop-password", status_code=204)
async def desktop_password(payload: dict[str, Any], request: Request):
    if not getattr(request.state, "desktop_gate", None):
        raise HTTPException(404)
    return await account_password(payload, request)


@router.post("/api/auth/password", status_code=204)
async def account_password(payload: dict[str, Any], request: Request):
    if not auth_required():
        raise HTTPException(403, "请先登录账号")
    user = require_current_user(request)
    new_password = payload.get("new_password")
    current_password = payload.get("current_password", "")
    if not isinstance(new_password, str) or not isinstance(current_password, str):
        raise HTTPException(400, "请填写有效的密码")
    automatic_identity = bool(getattr(request.state, "desktop_gate", None)
                              and getattr(request.state, "desktop_default_identity", False))
    change_account_password(user, new_password, current_password, automatic_identity=automatic_identity,
                            session_id=request.cookies.get(auth_cookie_name(), ""))
    return Response(status_code=204)


@router.get("/api/auth/me")
async def auth_me(request: Request):
    user = require_current_user(request)
    result = _session_user(user)
    if getattr(request.state, "desktop_default_identity", False):
        result["desktop_default"] = True
    if getattr(request.state, "desktop_gate", None) and user.email and user.tenant_status == "active":
        initial_setup = result.get("desktop_default") and desktop_password_setup_pending(user)
        result["password_mode"] = "setup" if initial_setup else "change"
        result["desktop_password_mode"] = result["password_mode"]
    return {"user": result}


@router.get("/api/tenants")
async def tenant_list(request: Request):
    actor = require_current_user(request)
    return {
        "current_tenant_id": actor.tenant_id,
        "tenants": list_user_tenants(actor.user_id),
    }


@router.post("/api/tenants")
async def tenant_create(payload: dict[str, Any], request: Request):
    actor = require_current_user(request)
    tenant = create_tenant(
        actor_user_id=actor.user_id,
        tenant_id=str(payload.get("tenant_id") or ""),
        name=str(payload.get("name") or ""),
    )
    return {"tenant": tenant}


@router.patch("/api/tenants/{tenant_id}")
async def tenant_update(tenant_id: str, payload: dict[str, Any], request: Request):
    actor = require_current_user(request)
    tenant = update_tenant_name(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        name=str(payload.get("name") or ""),
    )
    return {"tenant": tenant}


@router.post("/api/tenants/{tenant_id}/disable")
async def tenant_disable(tenant_id: str, request: Request):
    actor = require_current_user(request)
    tenant = set_tenant_status(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        status="inactive",
    )
    try:
        active_session_ids = sorted(
            session_id
            for session_id, runtime in ServerState.sessions.items()
            if isinstance(runtime, dict)
            and str(runtime.get("tenant_id") or "") == tenant_id
            and ServerState.is_running(session_id)
        )
        if active_session_ids:
            raise HTTPException(
                409,
                "Stop running sessions before disabling the tenant: "
                + ", ".join(active_session_ids[:5]),
            )
        runtimes = await get_sandbox_runtime().list_persistent_runtimes(
            tenant_id=tenant_id
        )
        active_runtime_ids = [
            str(item.get("runtime_id") or "")
            for item in runtimes
            if int(item.get("active_jobs") or 0) > 0
        ]
        if active_runtime_ids:
            raise HTTPException(
                409,
                "Stop active sandbox jobs before disabling the tenant: "
                + ", ".join(active_runtime_ids[:5]),
            )
        scheduled_store = get_scheduled_task_service().store
        if scheduled_store.tenant_has_inflight_runs(tenant_id=tenant_id):
            raise HTTPException(
                409,
                "Wait for running scheduled tasks before disabling the tenant",
            )
        suspended_tasks = scheduled_store.set_tenant_suspended(
            tenant_id=tenant_id,
            suspended=True,
        )
    except Exception:
        set_tenant_status(
            actor_user_id=actor.user_id,
            tenant_id=tenant_id,
            status="active",
        )
        raise
    return {"tenant": tenant, "suspended_scheduled_tasks": suspended_tasks}


@router.post("/api/tenants/{tenant_id}/restore")
async def tenant_restore(tenant_id: str, request: Request):
    actor = require_current_user(request)
    tenant = set_tenant_status(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        status="active",
    )
    resumed_tasks = get_scheduled_task_service().store.set_tenant_suspended(
        tenant_id=tenant_id,
        suspended=False,
    )
    return {"tenant": tenant, "resumed_scheduled_tasks": resumed_tasks}


@router.post("/api/tenants/{tenant_id}/switch")
async def tenant_switch(tenant_id: str, request: Request):
    actor = require_current_user(request)
    user = switch_login_session_tenant(
        session_id=str(request.cookies.get(auth_cookie_name()) or ""),
        user_id=actor.user_id,
        tenant_id=tenant_id,
    )
    request.state.current_user = user
    return {"user": _session_user(user)}


@router.get("/api/tenants/{tenant_id}/members")
async def tenant_member_list(tenant_id: str, request: Request):
    actor = require_current_user(request)
    return {
        "members": list_tenant_members(
            actor_user_id=actor.user_id,
            tenant_id=tenant_id,
        )
    }


@router.post("/api/tenants/{tenant_id}/members")
async def tenant_member_add(
    tenant_id: str,
    payload: dict[str, Any],
    request: Request,
):
    actor = require_current_user(request)
    member = add_tenant_member(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        email=str(payload.get("email") or ""),
        role=str(payload.get("role") or "member"),
        password=str(payload.get("password") or ""),
        name=str(payload.get("name") or ""),
        user_id=str(payload.get("user_id") or ""),
    )
    return {"member": member}


@router.patch("/api/tenants/{tenant_id}/members/{user_id}")
async def tenant_member_update(
    tenant_id: str,
    user_id: str,
    payload: dict[str, Any],
    request: Request,
):
    actor = require_current_user(request)
    member = update_tenant_member_role(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=str(payload.get("role") or ""),
    )
    return {"member": member}


@router.delete("/api/tenants/{tenant_id}/members/{user_id}")
async def tenant_member_remove(tenant_id: str, user_id: str, request: Request):
    actor = require_current_user(request)
    remove_tenant_member(
        actor_user_id=actor.user_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    wiki_shares_deleted = WikiShareStore(ServerState.get_workdir()).delete_member_data(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return {"status": "removed", "wiki_shares_deleted": wiki_shares_deleted}


async def _delete_tenant_runtime_data(tenant_id: str) -> dict[str, Any]:
    active_session_ids = sorted(
        session_id
        for session_id, runtime in ServerState.sessions.items()
        if isinstance(runtime, dict)
        and str(runtime.get("tenant_id") or "") == tenant_id
        and ServerState.is_running(session_id)
    )
    if active_session_ids:
        raise HTTPException(
            409,
            "Tenant has running sessions: " + ", ".join(active_session_ids[:5]),
        )

    sandbox_runtime = get_sandbox_runtime()
    runtimes = await sandbox_runtime.list_persistent_runtimes(tenant_id=tenant_id)
    active_runtime_ids = [
        str(item.get("runtime_id") or "")
        for item in runtimes
        if int(item.get("active_jobs") or 0) > 0
    ]
    if active_runtime_ids:
        raise HTTPException(
            409,
            "Tenant has active sandbox jobs: " + ", ".join(active_runtime_ids[:5]),
        )

    for runtime in runtimes:
        runtime_id = str(runtime.get("runtime_id") or "").strip()
        if runtime_id:
            await sandbox_runtime.manage_persistent_runtime(
                runtime_id,
                "delete",
                owner={"tenant_id": tenant_id},
                force=True,
                purge_data=True,
            )

    sessions = await SessionManager.list(tenant_id=tenant_id)
    for session in sessions:
        await SessionManager.delete(
            session.id,
            tenant_id=tenant_id,
            user_id=session.user_id,
            workspace_id=session.workspace_id,
        )
        await ServerState.cleanup_session(session.id)

    scheduled_deleted = get_scheduled_task_service().store.delete_tenant_data(
        tenant_id=tenant_id
    )
    tracker_deleted = SharedTracker.delete_tenant_data(
        tenant_id,
        workdir=ServerState.get_workdir(),
    )
    hidden_kanban_deleted = KanbanHiddenStore.delete_tenant_data(
        ServerState.get_workdir(),
        tenant_id=tenant_id,
    )
    wiki_shares_deleted = WikiShareStore(ServerState.get_workdir()).delete_tenant_data(
        tenant_id
    )
    tenant_root = workdir_utils.tenant_workdirs_root(
        ServerState.get_workdir(),
        tenant_id=tenant_id,
    )
    if tenant_root.exists():
        await asyncio.to_thread(shutil.rmtree, tenant_root)
    workdir_utils.delete_tenant_workspace_states(ServerState.get_workdir(), tenant_id)
    return {
        "sessions": len(sessions),
        "sandbox_runtimes": len(runtimes),
        "scheduled_records": sum(scheduled_deleted.values()),
        "tracker_records": sum(tracker_deleted.values()),
        "hidden_kanban_records": hidden_kanban_deleted,
        "wiki_share_records": sum(wiki_shares_deleted.values()),
        "tenant_root": tenant_root.as_posix(),
    }


@router.delete("/api/tenants/{tenant_id}")
async def tenant_delete(tenant_id: str, payload: dict[str, Any], request: Request):
    actor = require_current_user(request)
    if str(payload.get("confirm_tenant_id") or "") != tenant_id:
        raise HTTPException(400, "confirm_tenant_id must exactly match tenant_id")
    validate_tenant_deletion(
        actor_user_id=actor.user_id,
        current_tenant_id=actor.tenant_id,
        tenant_id=tenant_id,
    )
    deleted = await _delete_tenant_runtime_data(tenant_id)
    delete_tenant_record(actor_user_id=actor.user_id, tenant_id=tenant_id)
    return {"status": "deleted", "tenant_id": tenant_id, "deleted": deleted}


def _require_tenant_admin(request: Request) -> CurrentUserContext:
    actor = require_current_user(request)
    if actor.role not in {"owner", "admin"}:
        raise HTTPException(403, "Tenant admin role is required")
    return actor


@router.post("/api/admin/users")
async def admin_create_user(payload: dict[str, Any], request: Request):
    actor = _require_tenant_admin(request)
    created = create_user_account(
        tenant_id=actor.tenant_id,
        email=str(payload.get("email") or ""),
        password=str(payload.get("password") or ""),
        name=str(payload.get("name") or ""),
        role=str(payload.get("role") or "member"),
        user_id=str(payload.get("user_id") or ""),
    )
    return {"user": serialize_user(created)}
