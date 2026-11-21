"""Workspace file browsing, safe downloads and immutable attachment uploads.

Routes own HTTP validation; ownership and workspace resolution come from _shared.
This module never imports the application entry point.
"""

import asyncio
import hashlib
import mimetypes
import os
import re
import uuid
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from . import project_files, project_roots, managed_files

from ._shared import (
    ServerState,
    SessionManager,
    workdir_utils,
)
from .workspace_scope import (
    _resolve_user_workdir_path,
    _session_owner_kwargs,
    _workdir_name_from_path,
    _workspace_context_from_request,
)
from ..presentation import find_presented_image_block, inspect_image_file
from .workspace_files import collect_user_visible_workspace_files

router = APIRouter()


@router.get("/api/files/history")
async def asset_history(
    request: Request,
    workspace_id: str = Query(default=""),
    run_id: str = Query(default=""),
    path: str = Query(default=""),
    change_q: Annotated[str, Query()] = "",
    change_status: Annotated[str, Query()] = "",
    change_offset: Annotated[int, Query(ge=0)] = 0,
    change_limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    from ..runtime.asset_history import history
    context = _workspace_context_from_request(request, workspace_id=workspace_id)
    workdir_utils.assert_workspace_active(ServerState.get_workdir(), context)
    root, _ = _resolve_workspace_file_path("", context)
    try:
        return await asyncio.to_thread(
            history, root, run_id, path,
            change_query=change_q, change_status=change_status,
            change_offset=change_offset, change_limit=change_limit,
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/api/files/history/runs")
async def asset_history_runs(
    request: Request,
    workspace_id: str = Query(default=""),
    q: Annotated[str, Query()] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    started_from: Annotated[float | None, Query()] = None,
    started_before: Annotated[float | None, Query()] = None,
):
    from ..runtime.asset_history import list_runs
    context = _workspace_context_from_request(request, workspace_id=workspace_id)
    workdir_utils.assert_workspace_active(ServerState.get_workdir(), context)
    root, _ = _resolve_workspace_file_path("", context)
    return await asyncio.to_thread(list_runs, root, q, offset, limit, started_from, started_before)


_UNSAFE_INLINE_SUFFIXES = {".html", ".htm", ".svg"}
_UNSAFE_INLINE_MEDIA_TYPES = {"text/html", "application/xhtml+xml", "image/svg+xml"}
def _resolve_workspace_file_path(
    path: str,
    context: workdir_utils.WorkspaceContext,
    *,
    desktop_preview: bool = False,
) -> tuple[Path, Path]:
    base_path = _resolve_user_workdir_path(
        context.workspace_id,
        context,
    ).resolve(strict=False)
    raw_path = str(path or "").strip()
    if desktop_preview and os.environ.get("TCODE_DESKTOP_MANAGED") == "1" and raw_path.startswith(("/workspace/project-inputs/", "/workspace/artifacts/jobs/", "/workspace/artifacts/local/")):
        # Unlinked desktop workspaces have no root inventory to translate managed links.
        raw_path = project_roots.managed_input_relative(raw_path, base_path) or raw_path
    try:
        full_path = (base_path / raw_path).resolve(strict=False) if raw_path else base_path
        full_path.relative_to(base_path)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "Access denied")
    return base_path, full_path


def _require_visible_workspace_file(
    path: str,
    context: workdir_utils.WorkspaceContext,
) -> tuple[Path, Path]:
    base_path, full_path = _resolve_workspace_file_path(path, context)
    if not full_path.is_file() or full_path.is_symlink():
        raise HTTPException(404, "File not found")
    visible, _scanned, _truncated = collect_user_visible_workspace_files(
        base_path,
        server_workdir=ServerState.get_workdir(),
        context=context,
    )
    resolved = full_path.resolve(strict=False)
    if not any(item.path.resolve(strict=False) == resolved for item in visible):
        raise HTTPException(403, "File is not user-visible")
    return base_path, full_path


def _is_unsafe_inline_file(filename: str, media_type: str) -> bool:
    suffix = Path(str(filename or "")).suffix.lower()
    content_type = str(media_type or "").split(";", 1)[0].strip().lower()
    return suffix in _UNSAFE_INLINE_SUFFIXES or content_type in _UNSAFE_INLINE_MEDIA_TYPES


def _search_workspace_files(
    base_dir: Path,
    *,
    query: str = "",
    limit: int = 40,
    server_workdir: str = "",
    workspace_context: workdir_utils.WorkspaceContext | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Search the uploaded/generated file allowlist for one workspace."""

    workspace_root = base_dir.resolve(strict=False)
    normalized_query = str(query or "").strip().casefold()[:200]
    query_tokens = [part for part in re.split(r"\s+", normalized_query) if part]
    bounded_limit = max(1, min(100, int(limit or 40)))
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    visible_files, scanned, truncated = collect_user_visible_workspace_files(
        workspace_root,
        server_workdir=server_workdir,
        context=workspace_context,
    )
    for candidate in visible_files:
        path = candidate.path
        relative = candidate.relative_path
        name = path.name
        try:
            stat = path.stat()
        except OSError:
            continue

        searchable_path = relative.casefold()
        searchable_name = name.casefold()
        if query_tokens and not all(token in searchable_path for token in query_tokens):
            continue

        if not normalized_query:
            score: tuple[Any, ...] = (
                0,
                -float(stat.st_mtime),
                relative.count("/"),
                searchable_path,
            )
        else:
            path_segments = searchable_path.split("/")
            if searchable_name == normalized_query or searchable_path == normalized_query:
                rank = 0
            elif searchable_name.startswith(normalized_query):
                rank = 1
            elif any(segment.startswith(normalized_query) for segment in path_segments):
                rank = 2
            elif normalized_query in searchable_name:
                rank = 3
            else:
                rank = 4
            score = (rank, relative.count("/"), searchable_path)

        candidates.append(
            (
                score,
                {
                    "name": name,
                    "path": relative,
                    "type": "file",
                    "size": int(stat.st_size),
                    "modified_at": float(stat.st_mtime),
                    "origin": candidate.origin,
                },
            )
        )

    candidates.sort(key=lambda item: item[0])
    return [item for _score, item in candidates[:bounded_limit]], scanned, truncated


@router.get("/api/files")
async def list_files(
    request: Request,
    path: str = Query(default=""),
    workspace_id: str = Query(default=""),
):
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    project = project_roots.inventory(ServerState.get_workdir(), workspace_context)
    if project_roots.uses_project_root(project, path):
        from collections import Counter
        names = Counter(Path(r["path"]).name for r in project["roots"])
        labels = {r["root_id"]: (r["path"] if names[Path(r["path"]).name] > 1 else Path(r["path"]).name)
                  for r in project["roots"]}
        if path in ("", "@roots") and (project["default_id"] != "primary" or len(project["roots"]) > 1 or path == "@roots"):
            return {"path": path, "project_bound": True, "root_labels": labels, "items": [
                {"name": labels[r["root_id"]], "path": "@roots/" + r["root_id"], "type": "directory",
                 "access": r["access"]} for r in project["roots"]]}
        row, relative = project_roots.resolve(project, path)
        items = await asyncio.to_thread(project_files.entries, row, relative)
        for item in items:
            item["path"] = "@roots/" + row["root_id"] + "/" + item["path"]
        return {"path": path, "project_bound": True, "root_labels": labels, "items": items}
    base_dir, full_path = _resolve_workspace_file_path(path, workspace_context)
    if not full_path.is_dir():
        raise HTTPException(400, "Not a directory")
    items = []
    for entry in os.scandir(full_path):
        if entry.name.startswith(".") and entry.name not in [
            ".claude",
            ".tcode",
            ".agents",
        ]:
            continue
        if entry.name in ["__pycache__", "node_modules", ".git", "venv", ".venv"]:
            continue
        rel_path = os.path.relpath(entry.path, base_dir.as_posix())
        item = {
            "name": entry.name,
            "path": rel_path,
            "type": "directory" if entry.is_dir() else "file",
        }
        if not entry.is_dir():
            try:
                item["size"] = entry.stat().st_size
            except:
                item["size"] = 0
        items.append(item)
    items.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
    return {"path": path, "items": items, "project_bound": False}


@router.get("/api/files/search")
async def search_files(
    request: Request,
    q: str = Query(default=""),
    workspace_id: str = Query(default=""),
    limit: int = Query(default=40, ge=1, le=100),
):
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    workdir_utils.assert_workspace_active(ServerState.get_workdir(), workspace_context)
    base_dir, _full_path = _resolve_workspace_file_path("", workspace_context)
    project = project_roots.inventory(ServerState.get_workdir(), workspace_context)
    unavailable = []
    if project and project["roots"]:
        items, scanned, truncated = [], 0, False
        for row in project["roots"]:
            if len(items) >= limit or scanned >= 4000:
                truncated = True
                break
            name = Path(row["path"]).name
            label = row["path"] if sum(Path(r["path"]).name == name for r in project["roots"]) > 1 else name
            try:
                hits, count, more = await asyncio.to_thread(project_files.search, row, q, limit-len(items), 4000-scanned)
            except (HTTPException, OSError) as exc:
                if isinstance(exc, HTTPException) and exc.status_code not in (404, 409):
                    raise
                unavailable.append(label)
                continue
            for hit in hits:
                hit["display_path"] = label + "/" + hit["path"]
                hit["path"] = "@roots/" + row["root_id"] + "/" + hit["path"]
                hit["root_name"] = label
            items.extend(hits)
            scanned += count
            truncated |= more
    else:
        items, scanned, truncated = await asyncio.to_thread(
            _search_workspace_files, base_dir, query=q, limit=limit,
            server_workdir=ServerState.get_workdir(), workspace_context=workspace_context,
        )
    return {
        "workspace_id": workspace_context.workspace_id,
        "query": str(q or "").strip()[:200],
        **({"unavailable_roots": unavailable} if project and project["roots"] else {}),
        "items": items,
        "scanned": scanned,
        "truncated": truncated,
    }


@router.patch("/api/files/entry")
async def rename_workspace_file(payload: dict[str, Any], request: Request):
    workspace_id = str(payload.get("workspace_id") or "")
    context = _workspace_context_from_request(request, workspace_id=workspace_id)
    name = str(payload.get("name") or "").strip()
    if not name or name != Path(name).name or name.startswith(".") or len(name) > 255:
        raise HTTPException(400, "Invalid file name")
    path = str(payload.get("path") or "")
    runtime = ServerState.get_workdir()
    project = project_roots.inventory(runtime, context)
    if project and (project["roots"] or project_roots.explicit_reference(path)):
        row, rel = project_roots.resolve(project, path, write=True)
        await asyncio.to_thread(project_files.rename_bound, runtime, context, row, rel, name)
        return {"path": str(Path(path).with_name(name)), "name": name}
    _base_path, source = await asyncio.to_thread(_require_visible_workspace_file, path, context)
    target = source.with_name(name)
    await asyncio.to_thread(managed_files.change, _base_path, source, name)
    return {"path": target.relative_to(_base_path).as_posix(), "name": target.name}


@router.delete("/api/files/entry")
async def delete_workspace_file(
    request: Request,
    path: str = Query(...),
    workspace_id: str = Query(default=""),
):
    context = _workspace_context_from_request(request, workspace_id=workspace_id)
    runtime = ServerState.get_workdir()
    project = project_roots.inventory(runtime, context)
    if project and (project["roots"] or project_roots.explicit_reference(path)):
        row, rel = project_roots.resolve(project, path, write=True)
        await asyncio.to_thread(project_files.trash_bound, runtime, context, row, rel)
        return {"deleted": True, "trashed": True, "path": path}
    _base_path, target = await asyncio.to_thread(_require_visible_workspace_file, path, context)
    await asyncio.to_thread(managed_files.change, _base_path, target)
    return {"deleted": True, "path": path}


@router.get("/api/files/content")
async def get_file_content(
    request: Request,
    path: str = Query(...),
    workspace_id: str = Query(default=""),
):
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    _base_dir, full_path = await asyncio.to_thread(_resolve_local_file_access, path, workspace_context)
    if not full_path.is_file():
        raise HTTPException(404, "File not found")
    try:
        data = await asyncio.to_thread(managed_files.read, _base_dir, full_path)
        content = data.decode("utf-8")
        if "\x00" in content[:8192]:
            raise HTTPException(400, "Binary file")
        return {"path": path, "content": content, "size": len(data)}
    except UnicodeDecodeError:
        raise HTTPException(400, "Cannot read file")


def _resolve_local_file_access(
    path: str,
    context: workdir_utils.WorkspaceContext,
) -> tuple[Path, Path]:
    runtime = ServerState.get_workdir()
    project = project_roots.inventory(runtime, context)
    if project:
        root = _resolve_user_workdir_path(context.workspace_id, context)
        relative = project_roots.managed_input_relative(path, root)
        if relative is not None:
            return root, project_files.managed_input_snapshot(root, relative)
    if project_roots.uses_project_root(project, path):
        row, rel = project_roots.resolve(project, path)
        root = _resolve_user_workdir_path(context.workspace_id, context)
        return root, project_files.snapshot(row, rel, root)
    return _resolve_workspace_file_path(path, context, desktop_preview=True)


@router.get("/api/files/download")
async def download_file(
    request: Request,
    path: str = Query(...),
    workspace_id: str = Query(default=""),
):
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    _base_dir, full_path = _resolve_local_file_access(path, workspace_context)

    if not full_path.is_file():
        raise HTTPException(404, "File not found")

    filename = full_path.name
    return managed_files.response(
        _base_dir, full_path,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/api/files/inline")
async def inline_file(
    request: Request,
    path: str = Query(...),
    workspace_id: str = Query(default=""),
):
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    _base_dir, full_path = _resolve_local_file_access(path, workspace_context)
    if not full_path.is_file():
        raise HTTPException(404, "File not found")

    filename = full_path.name
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if _is_unsafe_inline_file(filename, media_type):
        return managed_files.response(
            _base_dir, full_path,
            filename=filename,
            media_type="application/octet-stream",
        )
    return managed_files.response(
        _base_dir, full_path,
        filename=filename,
        media_type=media_type,
        content_disposition_type="inline",
    )


@router.get("/api/files/html-preview")
async def html_preview(
    request: Request,
    path: str = Query(...),
    workspace_id: str = Query(default=""),
):
    """Render workspace HTML in an opaque, offline iframe origin."""
    workspace_context = _workspace_context_from_request(request, workspace_id=workspace_id)
    _base_dir, full_path = _resolve_local_file_access(path, workspace_context)
    if not full_path.is_file() or full_path.suffix.lower() not in {".html", ".htm"}:
        raise HTTPException(404, "HTML file not found")
    return managed_files.response(
        _base_dir, full_path,
        media_type="text/html; charset=utf-8",
        content_disposition_type="inline",
        headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/api/media/{asset_id}")
async def presented_image(
    request: Request,
    asset_id: str,
    session_id: str = Query(...),
):
    """Serve only images explicitly registered on a persisted assistant message."""

    workspace_context = _workspace_context_from_request(request)
    session = await SessionManager.get(
        str(session_id or "").strip(),
        **_session_owner_kwargs(workspace_context),
    )
    if session is None:
        raise HTTPException(404, "Session not found")

    block = find_presented_image_block(
        getattr(session, "messages", []) or [],
        asset_id,
    )
    if block is None or str(block.get("session_id") or "") != str(session.id):
        raise HTTPException(404, "Registered image not found")

    session_context = _workspace_context_from_request(
        request,
        workspace_id=str(getattr(session, "workspace_id", "") or ""),
    )
    _base_dir, full_path = _resolve_workspace_file_path(
        str(block.get("path") or ""),
        session_context,
    )
    private_copy = managed_files.snapshot(_base_dir, full_path)
    try:
        try:
            inspected = inspect_image_file(private_copy)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

        expected_mime = str(block.get("mime_type") or "").strip().lower()
        detected_mime = str(inspected.get("mime_type") or "").strip().lower()
        if not expected_mime or expected_mime != detected_mime:
            raise HTTPException(409, "Registered image type changed")
        expected_size = int(block.get("size") or 0)
        if expected_size and expected_size != int(inspected.get("size") or 0):
            raise HTTPException(409, "Registered image content changed")
        expected_sha256 = str(block.get("sha256") or "").strip().lower()
        if expected_sha256 and expected_sha256 != str(inspected.get("sha256") or ""):
            raise HTTPException(409, "Registered image content changed")

        response = managed_files.SnapshotResponse(
            path=private_copy.as_posix(),
            background=BackgroundTask(private_copy.unlink, missing_ok=True),
            media_type=detected_mime,
        )
        response.headers["Content-Disposition"] = "inline"
        response.headers["Cache-Control"] = "private, no-cache"
        return response
    except BaseException:
        private_copy.unlink(missing_ok=True)
        raise


@router.post("/api/files/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Query(""),
    workdir_name: str = Query(""),
    workspace_id: str = Query(""),
    relative_path: str = Query(""),
):
    default_max_file_size = 100 * 1024 * 1024
    try:
        max_file_size = max(
            1,
            int(os.environ.get("TCODE_UPLOAD_MAX_FILE_SIZE_BYTES") or default_max_file_size),
        )
    except Exception:
        max_file_size = default_max_file_size
    filename = os.path.basename(file.filename or "uploaded_file")
    if not filename:
        filename = "uploaded_file"

    workspace_context = _workspace_context_from_request(
        request,
        workspace_id=str(workspace_id or "").strip(),
        workdir_name=str(workdir_name or "").strip(),
    )
    target_root = ""
    session = None
    if str(session_id or "").strip():
        owner_kwargs = _session_owner_kwargs(workspace_context)
        session = await SessionManager.get(str(session_id).strip(), **owner_kwargs)
        if not session:
            raise HTTPException(404, "Session not found")
        target_root = str(getattr(session, "workdir", "") or "").strip()
    elif str(workdir_name or "").strip() or str(workspace_id or "").strip():
        target_root = _resolve_user_workdir_path(workspace_context.workspace_id, workspace_context).as_posix()
    else:
        target_root = _resolve_user_workdir_path(workspace_context.workspace_id, workspace_context).as_posix()

    safe_rel = str(relative_path or "").strip().replace("\\", "/")
    if safe_rel.startswith("/") or "/../" in f"/{safe_rel}/" or safe_rel.startswith("../"):
        raise HTTPException(400, "invalid relative_path")

    target_root_path = Path(target_root).resolve()
    runtime = ServerState.get_workdir()
    bound = project_files.binding(runtime, workspace_context) if target_root_path == _resolve_user_workdir_path(workspace_context.workspace_id, workspace_context) else None
    upload_root_path = (target_root_path / "upload").resolve()
    if not upload_root_path.is_relative_to(target_root_path):
        raise HTTPException(403, "upload path escapes workdir")
    if safe_rel:
        full_path = (upload_root_path / safe_rel).resolve()
    else:
        full_path = (upload_root_path / filename).resolve()

    try:
        full_path.relative_to(upload_root_path)
    except Exception:
        raise HTTPException(403, "upload path escapes workdir")
    if full_path == upload_root_path:
        raise HTTPException(400, "invalid upload filename")

    relative_to_workdir = full_path.relative_to(target_root_path).as_posix()
    container_root = (
        str(os.environ.get("TCODE_SANDBOX_CONTAINER_WORKDIR", "/workspace") or "/workspace")
        .rstrip("/")
        or "/workspace"
    )
    container_path = f"{container_root}/{relative_to_workdir}"

    total_bytes = 0
    content_hasher = hashlib.sha256()
    with tempfile.NamedTemporaryFile(prefix="tcode-upload-", delete=False) as staging:
        temp_path = Path(staging.name)

    def _cleanup_temp_upload() -> None:
        try:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_file_size:
                    raise HTTPException(413, "single file exceeds 100MB limit")
                content_hasher.update(chunk)
                f.write(chunk)
        # Publish through pinned directory descriptors; retain the private staging
        # file for the optional desktop project copy.
        from ..runtime.sandbox.macos_native import WorkspaceFileExists

        requested_path = full_path
        while True:
            try:
                with managed_files.publish(target_root_path, full_path, temp_path) as published:
                    full_path = published
                    if bound and bound["access"] == "rw":
                        await asyncio.to_thread(project_files.mirror_upload, runtime, workspace_context, bound,
                                                full_path.relative_to(target_root_path).as_posix(), temp_path)
                break
            except (FileExistsError, WorkspaceFileExists):
                full_path = requested_path.with_name(
                    f"{requested_path.stem}-{uuid.uuid4().hex}{requested_path.suffix}"
                )
        temp_path.unlink()
        relative_to_workdir = full_path.relative_to(target_root_path).as_posix()
        container_path = f"{container_root}/{relative_to_workdir}"
        content_sha256 = content_hasher.hexdigest()
        try:
            from ..research import (
                ResearchEventInput,
                ResearchEventSink,
                trace_id_for_turn,
            )

            session_metadata = dict(getattr(session, "metadata", {}) or {})
            active_turn = dict(session_metadata.get("active_turn") or {})
            turn_id = str(active_turn.get("turn_id") or "")
            trace_id = (
                trace_id_for_turn(str(session_id or ""), turn_id)
                if turn_id
                else ""
            )
            ResearchEventSink.append(
                ResearchEventInput(
                    event_type="file.uploaded",
                    tenant_id=str(
                        getattr(session, "tenant_id", "")
                        or workspace_context.tenant_id
                    ),
                    user_id=str(
                        getattr(session, "user_id", "")
                        or workspace_context.user_id
                    ),
                    workspace_id=str(
                        getattr(session, "workspace_id", "")
                        or workspace_context.workspace_id
                    ),
                    session_id=str(session_id or "").strip(),
                    turn_id=turn_id,
                    trace_id=trace_id,
                    actor_type="user",
                    actor_id=str(
                        getattr(session, "user_id", "")
                        or workspace_context.user_id
                    ),
                    source_kind="file_upload",
                    source_id=relative_to_workdir,
                    source_ref=relative_to_workdir,
                    payload={
                        "filename": full_path.name,
                        "relative_path": relative_to_workdir,
                        "container_path": container_path,
                        "size": total_bytes,
                        "content_type": str(file.content_type or ""),
                        "sha256": content_sha256,
                    },
                    privacy_state="private",
                    stable_state_version=content_sha256,
                ),
                workdir=target_root_path.as_posix(),
            )
        except Exception:
            pass
        return {
            "path": str(full_path),
            "display_path": container_path,
            "container_path": container_path,
            "relative_path": relative_to_workdir,
            "filename": filename,
            "stored_filename": full_path.name,
            "size": total_bytes,
            "sha256": content_sha256,
            "session_id": str(session_id or "").strip() or None,
            "workdir": target_root_path.as_posix(),
            "workdir_name": _workdir_name_from_path(target_root_path.as_posix(), workspace_context),
            "message": "File uploaded successfully",
        }
    except HTTPException:
        _cleanup_temp_upload()
        raise
    except Exception as e:
        _cleanup_temp_upload()
        raise HTTPException(500, f"Upload failed: {str(e)}")


def _artifact_service_url(service_id: str = '') -> str:
    from ..artifact_transport import _artifact_service_url as service_url
    return service_url(service_id)


def _artifact_proxy_headers(service_id: str = '') -> dict[str, str]:
    from ..artifact_transport import _artifact_headers
    return _artifact_headers(service_id)


def _artifact_proxy_endpoint(service_id: str, request: Request):
    from ..artifact_transport import _artifact_endpoint
    from . import personal_artifacts
    from .auth import require_current_user
    try:
        selected = service_id or os.environ.get('TMCP_ARTIFACT_SERVICE_ID', '').strip()
        if not selected and os.environ.get('TMCP_ARTIFACT_SERVICES'):
            raise HTTPException(400, '请为本次上传选择文件服务，或在设置中指定普通上传的默认服务')
        if selected in personal_artifacts.personal_entries():
            actor = require_current_user(request)
            try:
                return personal_artifacts.endpoint(selected, {'tenant_id': actor.tenant_id, 'user_id': actor.user_id})
            except ValueError as exc:
                raise HTTPException(403, str(exc)) from None
        return _artifact_endpoint(service_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, 'Artifact 服务配置无效或来源未配置') from exc


def _artifact_proxy_result(response, service_id: str):
    from ..artifact_services import bind_artifact_source
    try:
        return bind_artifact_source(response.json(), service_id)
    except ValueError as exc:
        raise HTTPException(502, 'Artifact 响应来源与配置不一致') from exc


async def _artifact_upload_scope(request: Request, session_id: str) -> tuple[str, str, str]:
    """Return workspace, validated session, and per-user upload limiter key."""
    context = _workspace_context_from_request(request)
    normalized_session = str(session_id or "").strip()
    if normalized_session:
        session = await SessionManager.get(
            normalized_session,
            **_session_owner_kwargs(context),
        )
        if session is None:
            raise HTTPException(404, "Session not found")
        workspace_id = str(getattr(session, "workspace_id", "") or context.workspace_id)
    else:
        workspace_id = str(context.workspace_id or "local")
    owner_id = f"{context.tenant_id}:{context.user_id}"
    return workspace_id or "local", normalized_session, owner_id


def _artifact_proxy_error(response, service_id: str) -> HTTPException:
    from .personal_artifacts import personal_entries
    if service_id in personal_entries():
        # Don't proxy provider diagnostics that may contain personal credentials.
        return HTTPException(response.status_code, '个人文件服务拒绝请求或文件不可用')
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = response.text
    return HTTPException(response.status_code, detail or "Artifact service request failed")


@router.post("/api/artifacts/upload")
async def upload_artifact(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Query(""),
    service_id: str = Query(''),
):
    """Stream one browser file to the independent Artifact service."""
    import httpx

    workspace_id, normalized_session, owner_id = await _artifact_upload_scope(request, session_id)
    selected, base_url, headers = _artifact_proxy_endpoint(service_id, request)
    timeout = float(os.environ.get("TMCP_ARTIFACT_PROXY_TIMEOUT_SECONDS", "900") or 900)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base_url}/api/v1/artifacts",
            data={"workspace_id": workspace_id, "owner_id": owner_id},
            files={"file": (file.filename or "artifact.bin", file.file, file.content_type or "application/octet-stream")},
            headers=headers,
        )
    if response.is_error:
        raise _artifact_proxy_error(response, selected)
    result = _artifact_proxy_result(response, selected)
    result["session_id"] = normalized_session or None
    return result


@router.post("/api/artifacts/bundles")
async def create_artifact_bundle(request: Request, payload: dict[str, Any]):
    """Create an uploading Bundle for browser directory upload."""
    import httpx

    workspace_id, normalized_session, owner_id = await _artifact_upload_scope(
        request, str(payload.get("session_id") or "")
    )
    selected, base_url, headers = _artifact_proxy_endpoint(str(payload.get('service_id') or ''), request)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/api/v1/bundles",
            json={"workspace_id": workspace_id, "owner_id": owner_id, "name": str(payload.get("name") or "folder")},
            headers=headers,
        )
    if response.is_error:
        raise _artifact_proxy_error(response, selected)
    result = _artifact_proxy_result(response, selected)
    result["session_id"] = normalized_session or None
    return result


@router.put("/api/artifacts/bundles/{bundle_id}/files")
async def upload_artifact_bundle_file(
    request: Request,
    bundle_id: str,
    file: UploadFile = File(...),
    relative_path: str = Query(...),
    session_id: str = Query(""),
    service_id: str = Query(''),
):
    import httpx

    workspace_id, _normalized_session, owner_id = await _artifact_upload_scope(
        request, session_id
    )
    selected, base_url, headers = _artifact_proxy_endpoint(service_id, request)
    timeout = float(os.environ.get("TMCP_ARTIFACT_PROXY_TIMEOUT_SECONDS", "900") or 900)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.put(
            f"{base_url}/api/v1/bundles/{bundle_id}/files",
            params={
                "relative_path": relative_path,
                "workspace_id": workspace_id,
                "owner_id": owner_id,
            },
            files={"file": (file.filename or "artifact.bin", file.file, file.content_type or "application/octet-stream")},
            headers=headers,
        )
    if response.is_error:
        raise _artifact_proxy_error(response, selected)
    return _artifact_proxy_result(response, selected)


@router.post("/api/artifacts/bundles/{bundle_id}/complete")
async def complete_artifact_bundle(
    request: Request, bundle_id: str, payload: dict[str, Any]
):
    import httpx

    workspace_id, _normalized_session, owner_id = await _artifact_upload_scope(
        request, str(payload.get("session_id") or "")
    )
    selected, base_url, headers = _artifact_proxy_endpoint(str(payload.get('service_id') or ''), request)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/api/v1/bundles/{bundle_id}/complete",
            json={
                "expected_files": payload.get("expected_files"),
                "workspace_id": workspace_id,
                "owner_id": owner_id,
            },
            headers=headers,
        )
    if response.is_error:
        raise _artifact_proxy_error(response, selected)
    return _artifact_proxy_result(response, selected)
