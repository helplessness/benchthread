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
from . import workdirs
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
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request)
        raw = Path(str(payload.get("path") or ""))
        if not raw.is_absolute() or not raw.is_dir():
            raise HTTPException(400, "请选择存在的项目目录")
        source = raw.resolve(strict=True)
        runtime = Path(ServerState.get_workdir()).resolve()
        if source.is_relative_to(runtime) or runtime.is_relative_to(source):
            raise HTTPException(400, "请选择独立于应用运行目录的项目目录")
        info = source.stat()
        identity = hashlib.sha256(os.fsencode(source) + f":{info.st_dev}:{info.st_ino}".encode()).hexdigest()[:16]
        name = f"{workdirs.sanitize_workdir_name(source.name)[:40] or 'project'}-{identity}"
        owner = dict(tenant_id=context.tenant_id, user_id=context.user_id)
        target = workdirs.resolve_user_workdir_path(str(runtime), name, **owner)
        # The binding lives outside the project. Purge only sees the managed workspace.
        from contextlib import closing
        with workdirs._WORKSPACE_MUTATION_LOCK, closing(workdirs._workspace_state_connection(str(runtime))) as db:
            db.execute("CREATE TABLE IF NOT EXISTS desktop_projects (tenant TEXT, user TEXT, workspace TEXT, generation INTEGER, path TEXT, device INTEGER, inode INTEGER, PRIMARY KEY(tenant,user,workspace))")
            key = (context.tenant_id, context.user_id, name)
            bound = workdirs.WorkspaceContext(*key)
            workdirs.assert_workspace_active(str(runtime), bound)
            generation = workdirs.workspace_generation(str(runtime), bound)
            row = db.execute("SELECT * FROM desktop_projects WHERE tenant=? AND user=? AND workspace=?", key).fetchone()
            if target.exists():
                if not row or row["generation"] != generation or row["path"] not in ("", str(source)):
                    raise HTTPException(409, "工作区名称已占用")
                if not row["path"]:
                    db.execute("UPDATE desktop_projects SET path=? WHERE tenant=? AND user=? AND workspace=?", (str(source), *key))
                    db.commit()
            else:
                workdirs.create_user_workdir(str(runtime), name, **owner)
                generation = workdirs.workspace_generation(str(runtime), bound)
                db.execute("INSERT OR REPLACE INTO desktop_projects VALUES(?,?,?,?,?,?,?)",
                           (*key, generation, str(source), info.st_dev, info.st_ino))
                db.commit()
        return {"name": name}

    @router.delete("/project-binding")
    def unlink_project(request: Request, workspace_id: str):
        from contextlib import closing
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        runtime = ServerState.get_workdir()
        with workdirs._WORKSPACE_MUTATION_LOCK, closing(workdirs._workspace_state_connection(runtime)) as db:
            db.execute("UPDATE desktop_projects SET path='' WHERE tenant=? AND user=? AND workspace=? AND generation=?",
                       (context.tenant_id, context.user_id, context.workspace_id, workdirs.workspace_generation(runtime, context)))
            db.commit()
        return {"unlinked": True}

    @router.get("/project-root")
    def project_root(request: Request, workspace_id: str):
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        runtime = ServerState.get_workdir()
        workdirs.assert_workspace_active(runtime, context)
        from contextlib import closing
        with closing(workdirs._workspace_state_connection(runtime)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_projects'").fetchone():
                return {"path": None}
            row = db.execute("SELECT * FROM desktop_projects WHERE tenant=? AND user=? AND workspace=? AND generation=?",
                             (context.tenant_id, context.user_id, context.workspace_id,
                              workdirs.workspace_generation(runtime, context))).fetchone()
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
        project_root(request, workspace_id)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        row = project_files.binding(ServerState.get_workdir(), context)
        if not row:
            raise HTTPException(404, "工作区未关联项目")
        root = _resolve_user_workdir_path(workspace_id, context)
        target = project_files.snapshot(row, str(payload.get("relative_path") or ""), root)
        return {"relative_path": target.relative_to(root).as_posix()}

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
        with opened(row, parent, directory=True) as directory, opened(
            {"path": str(root), "device": root.stat().st_dev, "inode": root.stat().st_ino},
            source.relative_to(root).as_posix(),
        ) as fd:
            temporary = ".benchthread-" + secrets.token_hex(16)
            with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory), "wb") as output:
                try:
                    with os.fdopen(os.dup(fd), "rb") as content:
                        shutil.copyfileobj(content, output)
                    output.flush()
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
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
                "artifact_configured": artifact_configured,
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
            "artifact_service_url": str(desktop_entry.get("artifact_service_url") or os.environ.get("TCODE_DESKTOP_ARTIFACT_SERVICE_URL", "")),
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
                })
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
        return {"path": str(root)}

    @router.get("/file")
    async def resolve_workspace_file(request: Request, workspace_id: str, path: str):
        if not hmac.compare_digest(request.headers.get("x-tcode-desktop-native", ""), token):
            raise HTTPException(403, "Native desktop access required")
        require_current_user(request)
        context = _workspace_context_from_request(request, workspace_id=workspace_id)
        workdirs.assert_workspace_active(ServerState.get_workdir(), context)
        from .files_api import _resolve_workspace_file_path
        _root, target = await asyncio.to_thread(_resolve_workspace_file_path, path, context)
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "File not found")
        return {"path": str(target)}

    return router
