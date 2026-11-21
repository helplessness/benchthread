"""Native resource resolution, installed only by the desktop launcher."""
from __future__ import annotations

import hmac
import hashlib
import asyncio
import os
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .auth import require_current_user
from .state import ServerState
from . import workdirs, project_roots
from .workspace_scope import _workspace_context_from_request, _resolve_user_workdir_path


async def _probe_artifact_service(url: str, service_id: str = '') -> str:
    from .desktop_config import remote_services_paused
    if remote_services_paused():
        return 'unconfigured'
    if not url:
        return "unconfigured"
    try:
        from ..artifact_transport import _artifact_endpoint
        _, configured_url, headers = _artifact_endpoint(service_id)
        if configured_url != url.rstrip('/'):
            return 'disconnected'
        import httpx
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            async with client.stream('GET', f"{url.rstrip('/')}/health", headers=headers) as response:
                return "connected" if 200 <= response.status_code < 300 else "disconnected"
    except Exception:
        return "disconnected"


def desktop_router(token: str) -> APIRouter:
    router = APIRouter(prefix="/api/desktop", include_in_schema=False)

    @router.post("/projects")
    def open_project(request: Request, payload: dict):
        context = native_scope(request, "")
        runtime = Path(ServerState.get_workdir()).resolve()
        source, info = project_roots.checked_directory(runtime, str(payload.get("path") or ""))
        if "identity" in payload and payload["identity"] != [str(info.st_dev), str(info.st_ino)]:
            raise HTTPException(409, "所选文件夹已变更，请重新选择")
        identity = hashlib.sha256(os.fsencode(source) + f":{info.st_dev}:{info.st_ino}".encode()).hexdigest()[:16]
        name = f"{workdirs.sanitize_workdir_name(source.name)[:40] or 'project'}-{identity}"
        owner = dict(tenant_id=context.tenant_id, user_id=context.user_id)
        target = workdirs.resolve_user_workdir_path(str(runtime), name, **owner)
        # The binding lives outside the project. Purge only sees the managed workspace.
        with workdirs._WORKSPACE_MUTATION_LOCK:
            bound = workdirs.WorkspaceContext(context.tenant_id, context.user_id, name)
            state = workdirs.workspace_state(str(runtime), bound) or {}
            if target.exists() and state.get("status") != "purged":
                workdirs.assert_workspace_active(str(runtime), bound)
                with project_roots.database(str(runtime)) as db:
                    if not db.execute(f"SELECT 1 FROM desktop_root_sets WHERE {project_roots.KEY}", project_roots.key(runtime, bound)).fetchone():
                        raise HTTPException(409, "工作区名称已占用")
            else:
                workdirs.create_user_workdir(str(runtime), name, **owner)
            project_roots.attach(str(runtime), bound, str(source), "rw", primary=True,
                                 expected_identity=payload.get("identity", [str(info.st_dev),str(info.st_ino)]))
        return {"name": name}

    def native_scope(request, workspace_id):
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        for field in ("user_id", "tenant_id"):
            expected = request.headers.get("x-tcode-dialog-" + field.replace("_", "-"))
            if expected is not None and expected != getattr(context, field):
                raise HTTPException(409, "账号或租户已切换，操作已取消")
        return context

    @router.get("/project-roots")
    def get_project_roots(request: Request, workspace_id: str):
        from .project_files import available
        context = native_scope(request, workspace_id)
        project = project_roots.inventory(ServerState.get_workdir(), context)
        if not project:
            raise HTTPException(404, "请先打开一个项目")
        return {**project, "name": next((Path(r["path"]).name for r in project["roots"] if r["root_id"]=="primary"),
                                         context.workspace_id.rsplit("-",1)[0]), "roots": [{"id": r["root_id"], "path": r["path"],
                                      "name": Path(r["path"]).name, "access": r["access"], "available": available(r)}
                                     for r in project["roots"]]}

    @router.post("/project-roots")
    def add_project_root(request: Request, payload: dict):
        workspace = str(payload.get("workspace_id") or "")
        context = native_scope(request, workspace)
        if not isinstance(payload.get("identity"), list):
            raise HTTPException(400, "缺少所选文件夹身份，请重新选择")
        with workdirs._WORKSPACE_MUTATION_LOCK:
            current = project_roots.inventory(ServerState.get_workdir(), context)
            if not current or any(current[k] != payload.get(k) for k in ("revision","generation")):
                raise HTTPException(409, "项目已变更，请重新选择文件夹")
            project_roots.attach(ServerState.get_workdir(), context, str(payload.get("path") or ""),
                                 payload.get("access", "ro"), expected_identity=payload["identity"])
        return get_project_roots(request, workspace)

    @router.patch("/project-roots")
    def change_project_root(request: Request, payload: dict):
        workspace_id = str(payload.get("workspace_id") or "")
        context = native_scope(request, workspace_id)
        runtime = ServerState.get_workdir()
        root_id = str(payload.get("root_id") or "")
        action = payload.get("action")
        with workdirs._WORKSPACE_MUTATION_LOCK:
            current = project_roots.inventory(runtime, context)
            if not current or any(current[k] != payload.get(k) for k in ("revision","generation")):
                raise HTTPException(409, "目录列表已变更，请刷新后重试")
            if action == "remove":
                project_roots.unlink(runtime, context, root_id)
            elif action in ("ro","rw","default"):
                project_roots.update(runtime, context, root_id, access=None if action=="default" else action,
                                     make_default=action=="default")
            else:
                raise HTTPException(400, "不支持的目录操作")
            return get_project_roots(request, workspace_id)

    @router.get("/project-root")
    def project_root(request: Request, workspace_id: str):
        context = native_scope(request, workspace_id)
        runtime = ServerState.get_workdir()
        workdirs.assert_workspace_active(runtime, context)
        from .project_files import binding
        row = binding(runtime, context)
        if not row or not row["path"]:
            return {"path": None}
        source = Path(row["path"])
        try:
            info = source.stat()
        except OSError:
            raise HTTPException(409, "原项目目录不可用，请重新选择")
        if (info.st_dev, info.st_ino) != (row["device"], row["inode"]) or source.resolve() != source:
            raise HTTPException(409, "原项目目录已变更，请重新选择")
        return {"path": str(source)}

    @router.post("/project-input")
    def snapshot_project_input(request: Request, payload: dict):
        from . import project_files
        workspace_id = str(payload.get("workspace_id") or "")
        context = native_scope(request, workspace_id)
        project = project_roots.inventory(ServerState.get_workdir(), context)
        if not project:
            raise HTTPException(404, "工作区未关联项目")
        if any(project[k] != payload.get(k) for k in ("generation","revision")):
            raise HTTPException(409, "项目已变更，请重新选取文件")
        selected = str(payload.get("path") or "")
        if not Path(selected).is_absolute():
            raise HTTPException(400, "请选择项目内的文件")
        row, relative = project_roots.resolve(project, selected)
        with project_files.opened(row, relative):
            return {"relative_path": f"@roots/{row['root_id']}/{relative}"}

    @router.post("/project-export")
    def export_project_file(request: Request, payload: dict):
        from . import project_files
        from .files_api import _resolve_workspace_file_path
        from .project_files import opened
        import secrets
        import shutil
        workspace_id = str(payload.get("workspace_id") or "")
        project_root(request, workspace_id)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        row = project_files.binding(ServerState.get_workdir(), context)
        if not row:
            raise HTTPException(404, "工作区未关联项目")
        relative = str(payload.get("destination") or "")
        if not relative or any(not part or part.startswith(".") for part in relative.split("/")):
            raise HTTPException(400, "请选择项目内的可写文件")
        source_name = str(payload.get("source") or "")
        root, source = _resolve_workspace_file_path(source_name, context)
        parent, _, name = relative.rpartition("/")
        with workdirs._WORKSPACE_MUTATION_LOCK, opened(row, parent, directory=True) as directory, opened(
            {"path": str(root), "device": root.stat().st_dev, "inode": root.stat().st_ino},
            source.relative_to(root).as_posix(),
        ) as fd:
            workdirs.assert_workspace_active(ServerState.get_workdir(), context)
            project_files.check_writable(ServerState.get_workdir(), context, row)
            temporary = ".benchthread-" + secrets.token_hex(16)
            with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory), "wb") as output:
                try:
                    with os.fdopen(os.dup(fd), "rb") as content:
                        shutil.copyfileobj(content, output)
                    output.flush()
                    with opened(row, parent, directory=True) as fresh:
                        if not os.path.samestat(os.fstat(fresh), os.fstat(directory)):
                            raise HTTPException(409, "写回位置已变更，请重新选择")
                        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=fresh)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
        return {"saved": True}

    @router.post('/model-settings')
    async def model_settings_route(request: Request, payload: dict):
        if not hmac.compare_digest(request.headers.get('x-tcode-desktop-native', ''), token):
            raise HTTPException(403, '模型配置只能通过桌面设置修改')
        from .auth_api import _require_tenant_admin
        _require_tenant_admin(request)
        from .desktop_models import model_settings
        return await model_settings(payload)

    @router.get("/services")
    async def service_status(request: Request, retry: bool = False):
        require_current_user(request)
        from ..mcp.manager_auto import MCPManager
        from ..runtime.workspace_registry import registry_workspace

        state = os.environ.get("TCODE_DESKTOP_REMOTE_STATE", "unconfigured")
        if state == "configured":
            try:
                with registry_workspace(ServerState.get_workdir()):
                    await asyncio.wait_for(MCPManager.initialize(ServerState.get_workdir()), timeout=12)
                    if retry:
                        await asyncio.wait_for(MCPManager.reconnect_server("job-protocol-mcp"), timeout=12)
                    status = (await MCPManager.get_status()).get("job-protocol-mcp")
                    state = "connected" if status and status.status == "connected" else "disconnected"
            except Exception:
                state = "disconnected"
        # Do not expose endpoint credentials or raw network exception strings.
        from .desktop_config import local_development_services, remote_url
        source = Path(os.environ.get("TCODE_DESKTOP_MCP_CONFIG", ""))
        try:
            config = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {}
        except (OSError, ValueError):
            config = {}
        job = config.get("job-protocol-mcp", {}) if isinstance(config, dict) else {}
        managed = bool(os.environ.get("TCODE_INTERNAL_JOB_PROTOCOL_URL"))
        job_url = "" if managed else str(job.get("url") or "")
        try:
            from ..artifact_transport import _artifact_service_url
            artifact_url = _artifact_service_url()
        except (ValueError, RuntimeError):
            artifact_url = ''
        artifact_configured = remote_url(artifact_url, allow_loopback=local_development_services())
        artifact_state = "unconfigured"
        artifacts = []
        try:
            from .artifact_services_api import artifact_service_status
            artifacts = await artifact_service_status()
            artifact_state = next((item['state'] for item in artifacts if item['default']), 'unconfigured')
        except (ValueError, TypeError):
            artifact_state = 'invalid'
        return {"remote": state, "config_file": os.environ.get("TCODE_DESKTOP_MCP_CONFIG", ""),
                "job_protocol": {"state": state, "url": job_url, "managed": managed},
                "artifact": {"state": artifact_state, "url": artifact_url},
                "artifacts": artifacts,
                "artifact_configured": bool(artifacts) or artifact_configured,
                "dev_local_services": local_development_services(),
                "local_execution": "offline-single-process" if os.environ.get("TCODE_DESKTOP_NATIVE_READY") == "1" else "unavailable", "local_runner_required": False}

    @router.get("/services/config")
    async def read_service_config(request: Request):
        user = require_current_user(request)
        source = Path(os.environ.get("TCODE_DESKTOP_MCP_CONFIG", ""))
        config = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {}
        job = config.get("job-protocol-mcp", {}) if isinstance(config, dict) else {}
        desktop_entry = config.get("$desktop", {}) if isinstance(config, dict) else {}
        from .desktop_config import artifact_configuration
        try:
            artifacts = artifact_configuration(desktop_entry)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            **artifacts,
            "can_edit": user.role in {'owner', 'admin'},
            "job_protocol_url": str(job.get("url") or ""),
            "job_protocol_enabled": job.get("enabled", True) is not False,
            "artifact_service_url": str(desktop_entry.get("artifact_service_url", os.environ.get("TCODE_DESKTOP_ARTIFACT_SERVICE_URL", ""))),
            "transport": str(job.get("transport") or "streamable-http"),
            "restart_required": False,
        }

    @router.put("/services/config")
    async def write_service_config(request: Request):
        user = require_current_user(request)
        if user.role not in {"owner", "admin"}:
            raise HTTPException(403, "Tenant admin role is required")
        payload = await request.json()
        from .desktop_config import artifact_configuration, local_development_services, remote_url
        if not isinstance(payload, dict):
            raise HTTPException(400, '配置必须是 JSON 对象')
        has_job = any(key in payload for key in ("job_protocol_url", "job_protocol_enabled", "transport"))
        job_url = str(payload.get("job_protocol_url") or "").strip()
        artifact_url = str(payload.get("artifact_service_url") or "").strip()
        enabled = bool(payload.get("job_protocol_enabled"))
        transport = str(payload.get("transport") or "streamable-http")
        allow_loopback = local_development_services()
        if has_job and enabled and not remote_url(job_url, allow_loopback=allow_loopback):
            raise HTTPException(400, "Job Protocol 地址必须是有效的远程 HTTP(S) URL")
        if artifact_url and not remote_url(artifact_url, allow_loopback=allow_loopback):
            raise HTTPException(400, "Artifact 地址必须是有效的远程 HTTP(S) URL")
        if transport not in {"streamable-http", "sse"}:
            raise HTTPException(400, "不支持的 MCP transport")
        source = Path(os.environ.get("TCODE_DESKTOP_MCP_CONFIG", ""))
        if not os.environ.get("TCODE_DESKTOP_MCP_CONFIG"):
            raise HTTPException(409, "桌面配置文件未初始化")
        try:
            current = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {}
        except (OSError, ValueError) as exc:
            raise HTTPException(409, '无法读取现有桌面配置，未覆盖原文件') from exc
        if not isinstance(current, dict):
            raise HTTPException(409, '桌面配置格式无效，未覆盖原文件')
        existing = current.get("job-protocol-mcp", {}) if isinstance(current, dict) else {}
        if has_job:
            current["job-protocol-mcp"] = {
                **({"headers": existing["headers"]} if isinstance(existing, dict) and "headers" in existing else {}),
                "url": job_url,
                "transport": transport,
                "enabled": enabled,
                "connect_timeout": int(existing.get("connect_timeout", 10000)) if isinstance(existing, dict) else 10000,
                "timeout": int(existing.get("timeout", 300000)) if isinstance(existing, dict) else 300000,
            }
        desktop_entry = current.get("$desktop", {}) if isinstance(current, dict) else {}
        artifact_changes = {}
        if 'artifact_services' in payload:
            try:
                if not isinstance(payload['artifact_services'], dict):
                    raise ValueError('Artifact 服务配置必须是对象')
                artifact_changes = artifact_configuration({
                    'artifact_services': payload['artifact_services'],
                    'artifact_default_service_id': payload.get('artifact_default_service_id', ''),
                    'artifact_target_services': payload.get('artifact_target_services', {}),
                }, saving=True)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc)) from exc
            default_entry = artifact_changes['artifact_services'].get(artifact_changes['artifact_default_service_id'], {})
            artifact_changes['artifact_service_url'] = default_entry.get('url', '')
        elif 'artifact_service_url' in payload:
            artifact_changes = {'artifact_service_url': artifact_url}
        current["$desktop"] = {
            **(desktop_entry if isinstance(desktop_entry, dict) else {}),
            **artifact_changes,
            **({"managed_job_protocol": False} if has_job else {}),
        }
        source.parent.mkdir(parents=True, exist_ok=True)
        temp = source.with_suffix(source.suffix + ".tmp")
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(source)
        # The launcher owns environment and MCP lifecycle; apply on next restart.
        return {"saved": True, "restart_required": True}

    @router.get("/workspace")
    async def resolve_workspace(request: Request, workspace_id: str):
        # HttpOnly gate cookie alone is insufficient: only the main process
        # knows this extra credential, which is never exposed via preload.
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        workdirs.assert_workspace_active(ServerState.get_workdir(), context)
        root = _resolve_user_workdir_path(context.workspace_id, context).resolve(strict=True)
        return {"path": project_root(request, workspace_id)["path"] or str(root)}

    @router.get("/file")
    async def resolve_workspace_file(request: Request, workspace_id: str, path: str):
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        workdirs.assert_workspace_active(ServerState.get_workdir(), context)
        from .files_api import _resolve_workspace_file_path
        from . import project_files
        project = project_roots.inventory(ServerState.get_workdir(), context)
        if project:
            root = _resolve_user_workdir_path(context.workspace_id, context)
            relative = project_roots.managed_input_relative(path, root)
            if relative is not None:
                info = root.stat()
                with project_files.opened({"path": root, "device": info.st_dev, "inode": info.st_ino}, relative):
                    return {"path": str(root / relative)}
        if project_roots.uses_project_root(project, path):
            row, rel = project_roots.resolve(project, path)
            with project_files.opened(row, rel):
                return {"path": str(Path(row["path"]) / rel)}
        _root, target = await asyncio.to_thread(_resolve_workspace_file_path, path, context, desktop_preview=True)
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "File not found")
        return {"path": str(target)}

    return router
