"""HTTP API for plugin management."""

from __future__ import annotations

from pathlib import Path
import asyncio
import tempfile
import zipfile
from uuid import UUID
from typing import Any, Optional

from fastapi import APIRouter, Query, Request, Depends, HTTPException
from contextvars import ContextVar
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agent.agents import BuiltinAgents
from ..skill.manager import SkillManager
from ..prompt.complete import CompletePromptRegistry
from ..prompt.sections import prompt_section_contract_document
from ..runtime.plugin_admission import plugin_admission_policy, public_workspace_id
from ..runtime.permission_guard import permission_contract_document
from ..runtime.provider_api import provider_api_catalog_document
from ..runtime.sdk_readiness import plugin_sdk_v1_readiness_document
from ..runtime.tool_pipeline import tool_pipeline_contract_document
from .command_registry import PluginCommandRegistry
from .hooks import HookRegistry
from .manager import PluginManager
from .registry import PluginRegistry
from . import panel_runs
from .manifest import load_manifest


def _get_server_state():
    from ..server.app import ServerState

    return ServerState


_authorized_workdir: ContextVar[str | None] = ContextVar("authorized_plugin_workdir", default=None)


async def _authorize_plugin_request(request: Request):
    from ..server import _shared
    from ..server.auth import auth_required
    user = getattr(request.state, "current_user", None)
    supplied = str(request.query_params.get("workdir") or "").strip()
    panel_call = request.url.path.startswith("/api/plugins/panels/")
    importing = request.url.path == '/api/plugins/install'
    body = await request.json() if request.method == "POST" and not importing and not panel_call and await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object")
    supplied = str(body.get("workdir") or supplied).strip()
    if auth_required():
        if user is None:
            raise HTTPException(401, "Authentication required")
        if request.method == "POST" and not panel_call and user.role not in {"admin", "owner"}:
            raise HTTPException(403, "Plugin changes require an administrator")
        context = _shared._workspace_context_from_request(request)
        owner_root = _shared._user_workdirs_root(context).resolve()
        target = Path(supplied).expanduser().resolve() if supplied else _shared._resolve_user_workdir_path(context.workspace_id, context).resolve()
        if target == owner_root or not target.is_relative_to(owner_root):
            raise HTTPException(403, "Workspace is not owned by the current user")
    else:
        target = Path(supplied or _get_server_state().get_workdir()).resolve()
    token = _authorized_workdir.set(str(target))
    try:
        yield
    finally:
        _authorized_workdir.reset(token)


router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(_authorize_plugin_request)])


class PluginLoadRequest(BaseModel):
    name: str
    path: str
    enabled: bool = True
    config: Optional[dict[str, Any]] = None
    workdir: Optional[str] = None
    panel_grants: Optional[list[str]] = None
    approved_permissions: Optional[dict[str,list[str]]] = None


class PluginUnloadRequest(BaseModel):
    name: str
    workdir: Optional[str] = None


class PluginDeleteRequest(PluginUnloadRequest):
    confirmed: bool = False


async def _get_plugin_manager(workdir: Optional[str] = None) -> PluginManager:
    """取得供管理 API 使用的插件运行时且不在查询阶段隐式补载。

    首次创建管理器仍会执行一次确定性的配置加载；之后的列表、诊断和目录
    查询保持只读。插件状态变化只能由显式 load、unload 或 reload-config 完成。
    """

    ServerState = _get_server_state()
    target_workdir = str(_authorized_workdir.get() or workdir or ServerState.get_workdir()).strip()
    return await PluginManager.get_global(
        target_workdir or ServerState.get_workdir(),
        config_path=str(Path(target_workdir) / ".tcode" / "plugins.json") if _authorized_workdir.get() else None,
        ensure_configured=False,
    )


@router.get("/list")
async def list_plugins(workdir: Optional[str] = Query(default=None)):
    manager = await _get_plugin_manager(workdir)
    return {
        "success": True,
        "plugins": [plugin.to_dict() for plugin in PluginRegistry.list_all()],
        "errors": manager.get_load_errors(),
        "diagnostics": manager.get_load_diagnostics(),
        "workdir": manager.workdir,
    }


@router.get("/available")
async def list_available_plugins(workdir: Optional[str] = Query(default=None)):
    manager = await _get_plugin_manager(workdir)
    return {"success": True, "plugins": manager.list_available_plugins(), "workdir": manager.workdir}


async def _panel_session(request: Request, session_id: str):
    from ..server.workspace_scope import _session_owner_kwargs, _workspace_context_from_request, _resolve_session_workdir
    from ..session.manager import SessionManager

    context = _workspace_context_from_request(request)
    session = await SessionManager.get(session_id, **_session_owner_kwargs(context))
    if not session:
        raise HTTPException(404, "会话不存在")
    from ..server.harness_api import harness_projection_for_session
    harness = harness_projection_for_session(session)
    if harness["runtime_id"] != "tcode-native":
        raise HTTPException(409, "插件面板仅在 TCode harness 中可用")
    manager = await _get_plugin_manager()
    if Path(await _resolve_session_workdir(session_id, context)).resolve() != Path(manager.workdir).resolve():
        raise HTTPException(403, "插件工作区与会话不符")
    return session, manager, harness["model_id"]


def _active_panel_plugins(manager):
    for item in manager._load_configured_plugins():
        if not item.get("enabled", True):
            continue
        plugin = PluginRegistry.get(item.get("name", ""))
        root = manager._resolve_plugin_root(item)
        if plugin and root and Path(plugin.source).resolve() == root.resolve():
            for panel in (plugin.manifest or {}).get("panels", []):
                yield plugin, root.resolve(), {**panel, "grants": sorted(set(panel.get("capabilities", [])) & set(item.get("panel_grants", [])))}


@router.get("/panels")
async def list_panels(request: Request, session_id: str):
    session, manager, _ = await _panel_session(request, session_id)
    return {"panels": [{"plugin": plugin.name, "id": panel["id"], "title": panel["title"],
                        "instance": plugin.instance_id, "running": sum(not task.done() and path.parent == panel_runs.record_directory(manager,session,plugin,panel) for path, task in panel_runs._tasks.items())}
                       for plugin, _, panel in _active_panel_plugins(manager)]}


@router.get("/panels/{plugin_name}/{panel_id}")
async def read_panel(request: Request, session_id: str, plugin_name: str, panel_id: str):
    _, _, plugin, root, panel = await _bound_panel(request, session_id, plugin_name, panel_id)
    entry = (root / panel["entry"]).resolve()
    if not entry.is_relative_to(root) or not entry.is_file():
        raise HTTPException(404, "面板文件不可用")
    if entry.stat().st_size > 4 * 1024 * 1024:
        raise HTTPException(413, "面板文件超过 4 MiB")
    return {"html": entry.read_text(encoding="utf-8"), "instance": plugin.instance_id}


async def _bound_panel(request, session_id, plugin_name, panel_id):
    session, manager, model_id = await _panel_session(request, session_id)
    for plugin, root, panel in _active_panel_plugins(manager):
        if plugin.name == plugin_name and panel["id"] == panel_id:
            instance = request.query_params.get("instance")
            if instance and instance != plugin.instance_id:
                raise HTTPException(409, "插件已重新加载，请重新打开面板")
            return session, manager, plugin, root, {**panel, "model_id": model_id}
    raise HTTPException(404, "插件面板未加载")


@router.get("/panels/{plugin_name}/{panel_id}/runs")
async def panel_history(request: Request, session_id: str, plugin_name: str, panel_id: str):
    session, manager, plugin, _, panel = await _bound_panel(request, session_id, plugin_name, panel_id)
    return {"runs": panel_runs.history(manager, session, plugin, panel), "grants": panel["grants"]}


@router.post("/panels/{plugin_name}/{panel_id}/runs")
async def panel_submit(request: Request, session_id: str, plugin_name: str, panel_id: str, turn: panel_runs.PanelTurn):
    session, manager, plugin, root, panel = await _bound_panel(request, session_id, plugin_name, panel_id)
    needed = {"team.run"}
    if turn.pdf or turn.source_run:
        needed.add("document.read")
    if not needed.issubset(panel["grants"]):
        raise HTTPException(403, "请先在插件列表授权团队运行及所选文档读取")
    return await panel_runs.start(manager, session, plugin, panel, turn)


class PanelSelection(BaseModel):
    run: UUID
    item_id: str
    text: str


@router.post("/panels/{plugin_name}/{panel_id}/share")
async def share_panel_item(request: Request, session_id: str, plugin_name: str, panel_id: str, selection: PanelSelection):
    session, manager, plugin, _, panel = await _bound_panel(request, session_id, plugin_name, panel_id)
    if "selection.share" not in panel["grants"]:
        raise HTTPException(403, "未授权带入对话")
    if not selection.text.strip() or len(selection.text) > 12000 or not 0 < len(selection.item_id) <= 160:
        raise HTTPException(422, "无效的选中项")
    directory = panel_runs.record_directory(manager, session, plugin, panel)
    record = panel_runs.read_record(directory / f"{selection.run}.json")
    if record["status"] != "completed":
        raise HTTPException(409, "运行尚未完成")
    file = None
    ref = record.get("document_ref")
    if ref and "document.read" in panel["grants"]:
        from ..server import managed_files
        root = Path(manager.workdir).resolve()
        source = directory / f"{UUID(ref)}.pdf"
        if source.is_symlink() or not source.is_file():
            raise HTTPException(404, "原始文档不可用")
        target = root / "upload" / "plugin-evidence" / ref / Path(record["document_name"]).name
        with managed_files.publish(root, target, source) as published:
            file = {"name": target.name, "path": str(published.relative_to(root))}
    return {"text": "\n".join([panel["title"], record["document_name"], selection.text, f"来源 {selection.run}/{selection.item_id}"]), "file": file}


@router.post("/panels/{plugin_name}/{panel_id}/runs/{run_id}/cancel")
async def cancel_panel_run(request: Request, session_id: str, plugin_name: str, panel_id: str, run_id: UUID):
    session, manager, plugin, _, panel = await _bound_panel(request, session_id, plugin_name, panel_id)
    path = panel_runs.record_directory(manager, session, plugin, panel) / f"{run_id}.json"
    item = panel_runs.read_record(path)
    task = panel_runs._tasks.get(path)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        item = panel_runs.read_record(path)
    return panel_runs.public(item)


@router.post('/install')
async def import_plugin_package(request: Request, filename: str = Query(...)):
    """以有界流接收插件包，静态安装和登记；不隐式加载代码。"""
    from .package_install import MAX_PACKAGE_BYTES, install_package
    if not filename.lower().endswith(('.tcode-plugin', '.zip')):
        raise HTTPException(400, '请选择 .zip 或 .tcode-plugin 插件包')
    if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
        raise HTTPException(415, '插件包须以 application/octet-stream 上传')
    manager = await _get_plugin_manager()
    try:
        with tempfile.TemporaryDirectory(prefix='tcode-plugin-upload-') as directory:
            package = Path(directory) / 'upload.tcode-plugin'
            size = 0
            with package.open('wb') as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PACKAGE_BYTES:
                        raise HTTPException(413, '插件包不能超过 32 MiB')
                    stream.write(chunk)
            result = await asyncio.to_thread(install_package, manager, package)
        return {'success': True, 'installation': result, 'workdir': manager.workdir}
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, zipfile.BadZipFile, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


class DevelopmentDirectory(BaseModel):
    path: str


@router.post("/development-directory")
async def register_development_directory(payload: DevelopmentDirectory):
    manager = await _get_plugin_manager()
    workspace = Path(manager.workdir).resolve()
    root = (workspace / payload.path).resolve()
    if not root.is_relative_to(workspace) or not root.is_dir():
        raise HTTPException(422, "请选择当前工作区中的插件源码目录")
    try:
        manifest = load_manifest(root)
        if manifest.requested_trust != "isolated":
            raise ValueError("本地开发插件必须声明 isolated 信任级别")
        if any(item.get("name") == manifest.name for item in manager._load_configured_plugins()):
            raise HTTPException(409, "插件已登记，可从列表加载")
        manager.persist_plugin_enabled_state(name=manifest.name, path=str(root), enabled=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "installation":{"name":manifest.name, "version":manifest.version,
                                              "path":str(root), "async_targets":bool(manifest.async_targets)}}


@router.post("/load")
async def load_plugin(request: PluginLoadRequest):
    manager = await _get_plugin_manager(request.workdir)
    configured = next(
        (
            item
            for item in manager._load_configured_plugins()
            if str(item.get("name") or "").strip().lower()
            == str(request.name or "").strip().lower()
        ),
        {},
    )
    effective_config = (
        dict(request.config)
        if request.config is not None
        else dict(configured.get("config") or {})
    )
    plugin_config = {
        "name": request.name,
        "path": request.path or str(configured.get("path") or ""),
        "enabled": request.enabled,
        "config": effective_config,
        "source": "api.load",
        "permissions": dict(configured.get("permissions") or {}),
        "_source_root": str(configured.get("_source_root") or manager.workdir),
    }
    plugin_root = manager._resolve_plugin_root(plugin_config)
    if _authorized_workdir.get() and plugin_root is not None:
        bundled = (Path(__file__).resolve().parents[2] / "plugins").resolve()
        if not plugin_root.is_relative_to(Path(manager.workdir).resolve()) and not plugin_root.is_relative_to(bundled):
            raise HTTPException(403, "Plugin package must belong to this workspace or the bundled catalog")
    if request.approved_permissions is not None:
        manifest = load_manifest(plugin_root)
        declared_permissions = {item.scope:set(item.patterns) for item in manifest.permissions}
        for scope, patterns in request.approved_permissions.items():
            if scope not in declared_permissions or not set(patterns) <= declared_permissions[scope]:
                raise HTTPException(422, "授权超出插件声明")
        plugin_config["permissions"] = {scope: request.approved_permissions.get(scope, False) for scope in declared_permissions}
    if request.panel_grants is not None:
        manifest = load_manifest(plugin_root)
        declared = {cap for panel in manifest.panels for cap in panel.get("capabilities", [])}
        if not set(request.panel_grants) <= declared:
            raise HTTPException(422, "权限超出插件声明")
    try:
        plugin = await manager.load_plugin(plugin_config)
    except Exception as exc:
        diagnostic = manager.get_load_diagnostics().get(request.name, {})
        return JSONResponse(
            {
                "success": False,
                "error": f"Plugin {request.name} failed to load: {exc}",
                "diagnostic": diagnostic,
                "workdir": manager.workdir,
            },
            status_code=500,
        )
    if plugin is None:
        return JSONResponse({"success": False, "error": f"plugin disabled: {request.name}"}, status_code=400)
    manager.persist_plugin_enabled_state(
        name=request.name,
        path=str(plugin_root) if plugin_root else plugin_config["path"],
        enabled=True,
        config=effective_config, panel_grants=request.panel_grants, permissions=plugin_config["permissions"] if request.approved_permissions is not None else None,
    )
    return {
        "success": True,
        "plugin": plugin.to_dict(),
        "message": f"Plugin {request.name} loaded successfully",
        "workdir": manager.workdir,
    }


class ToolPermission(PluginUnloadRequest):
    scope: str
    granted: bool


@router.post("/tool-permission")
async def set_tool_permission(payload: ToolPermission):
    manager = await _get_plugin_manager(payload.workdir)
    for item in manager._load_configured_plugins():
        if item["name"] != payload.name:
            continue
        manifest = load_manifest(manager._resolve_plugin_root(item))
        permission = next((p for p in manifest.permissions if p.scope == payload.scope), None)
        if permission is None:
            raise HTTPException(422, "权限超出插件声明")
        if PluginRegistry.get(payload.name) and not await manager.unload_plugin(payload.name):
            raise HTTPException(409, "请先停用插件")
        grants = {**item.get("permissions", {}), permission.scope:list(permission.patterns) if payload.granted else False}
        manager.persist_plugin_enabled_state(name=payload.name, enabled=False, permissions=grants)
        return {"success":True}
    raise HTTPException(404, "插件未登记")


class PanelGrants(PluginUnloadRequest):
    grants: list[str]


@router.post("/panel-grants")
async def update_panel_grants(payload: PanelGrants):
    manager = await _get_plugin_manager(payload.workdir)
    for item in manager._load_configured_plugins():
        if item["name"] != payload.name:
            continue
        manifest = load_manifest(manager._resolve_plugin_root(item))
        declared = {cap for panel in manifest.panels for cap in panel.get("capabilities", [])}
        if not set(payload.grants) <= declared:
            raise HTTPException(422, "权限超出插件声明")
        manager.persist_plugin_enabled_state(name=payload.name, enabled=item["enabled"], panel_grants=payload.grants)
        return {"success": True}
    raise HTTPException(404, "插件未安装")


@router.get("/activity/{name}")
async def plugin_activity(name: str):
    manager = await _get_plugin_manager()
    return {"running": panel_runs.active_count(manager.workdir, name)}


@router.post("/unload")
async def unload_plugin(request: PluginUnloadRequest):
    manager = await _get_plugin_manager(request.workdir)
    ok = await manager.unload_plugin(request.name)
    if not ok:
        return JSONResponse({"success": False, "error": f"plugin not found: {request.name}"}, status_code=404)
    manager.persist_plugin_enabled_state(name=request.name, enabled=False)
    return {
        "success": True,
        "message": f"Plugin {request.name} unloaded successfully",
        "workdir": manager.workdir,
    }


@router.post('/delete')
async def delete_plugin(request: PluginDeleteRequest):
    if not request.confirmed:
        raise HTTPException(400, '删除插件需要明确确认')
    from .package_install import delete_package
    manager = await _get_plugin_manager(request.workdir)
    try:
        result = await asyncio.to_thread(delete_package, manager, request.name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'success': True, **result}


@router.post("/reload-config")
async def reload_plugins_from_config(workdir: Optional[str] = Query(default=None)):
    manager = await _get_plugin_manager(workdir)
    plugins = await manager.reload_all()
    return {
        "success": True,
        "loaded_count": len(plugins),
        "plugins": [plugin.to_dict() for plugin in plugins],
        "errors": manager.get_load_errors(),
        "diagnostics": manager.get_load_diagnostics(),
        "workdir": manager.workdir,
    }


@router.get("/tools")
async def list_plugin_tools(workdir: Optional[str] = Query(default=None)):
    await _get_plugin_manager(workdir)
    tools = []
    for plugin in PluginRegistry.list_all():
        for tool in plugin.tools:
            tools.append(
                {
                    "id": tool.id,
                    "description": tool.description,
                    "plugin": plugin.name,
                    "parameters": getattr(tool.parameters, "__name__", str(tool.parameters)),
                }
            )
    return {"success": True, "tools": tools}


@router.get("/commands")
async def list_plugin_commands(workdir: Optional[str] = Query(default=None)):
    await _get_plugin_manager(workdir)
    return {
        "success": True,
        "commands": [command.to_dict() for command in PluginCommandRegistry.list_all()],
    }


@router.get("/skills")
async def list_plugin_skills(workdir: Optional[str] = Query(default=None)):
    await _get_plugin_manager(workdir)
    return {
        "success": True,
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "plugin": skill.plugin,
                "location": skill.location,
            }
            for skill in SkillManager.all()
            if skill.plugin
        ],
    }


@router.get("/agents")
async def list_plugin_agents(workdir: Optional[str] = Query(default=None)):
    await _get_plugin_manager(workdir)
    plugin_names = {plugin.name for plugin in PluginRegistry.list_all()}
    return {
        "success": True,
        "agents": [
            {
                "name": agent.name,
                "description": agent.description,
                "mode": agent.mode.value,
            }
            for agent in BuiltinAgents.list()
            if agent.name in plugin_names or any(
                plugin.name == agent.name or agent.name in plugin.agents
                for plugin in PluginRegistry.list_all()
            )
        ],
    }


@router.get("/doctor")
async def plugin_doctor(workdir: Optional[str] = Query(default=None)):
    manager = await _get_plugin_manager(workdir)
    return {"success": True, "reports": manager.get_doctor_report(), "workdir": manager.workdir}


@router.get("/runtime")
async def plugin_runtime_contract(
    workdir: Optional[str] = Query(default=None),
):
    """返回第二批单轨运行时的无业务载荷契约。

    参数:
        workdir: 用于选择受管插件运行时的可选工作区。

    返回:
        Tool、Permission、Prompt 与 Event 唯一 Consumer 契约，不包含工具名、
        参数、Prompt 正文、会话 ID、路径、权限响应或结果正文。
    """

    manager = await _get_plugin_manager(workdir)
    workspace_id = public_workspace_id(manager.workdir)
    return {
        "success": True,
        "policy": plugin_admission_policy(),
        "tool_pipeline": tool_pipeline_contract_document(),
        "permission": permission_contract_document(),
        "prompt_sections": prompt_section_contract_document(),
        "complete_prompt_provider": CompletePromptRegistry.provider_identity(),
        "event_pipeline": {
            "consumer": "HookRegistry",
            "single_track": True,
            "event_count": len(HookRegistry.catalog()),
        },
        "workspace_id": workspace_id,
    }


@router.get("/providers")
async def provider_preview_catalog():
    return {
        "success": True,
        "provider_api": provider_api_catalog_document(),
    }


@router.get("/sdk-readiness")
async def plugin_sdk_readiness():
    return {
        "success": True,
        "readiness": plugin_sdk_v1_readiness_document(),
    }


@router.get("/health")
async def plugin_health(
    workdir: Optional[str] = Query(default=None),
    refresh: bool = Query(default=False),
):
    manager = await _get_plugin_manager(workdir)
    if refresh:
        await manager.refresh_all_plugin_health()
    return {
        "success": True,
        "instances": manager.get_instance_statuses(),
        "workdir": manager.workdir,
    }


@router.get("/hooks")
async def list_plugin_hooks(workdir: Optional[str] = Query(default=None)):
    await _get_plugin_manager(workdir)
    from .hooks import HookRegistry

    return {
        "success": True,
        "hooks": HookRegistry.list_hooks(),
        "catalog": HookRegistry.catalog(),
    }
