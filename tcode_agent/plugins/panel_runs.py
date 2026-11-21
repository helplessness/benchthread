"""Workspace-scoped, model-only panel turns. The iframe never receives a provider or tool token."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from ..atomic_storage import atomic_write_json, file_transaction
from ..providers import create_provider
from ..providers.base import ChatMessage
from ..agent.agents import BuiltinAgents
from .registry import PluginRegistry

_BOOT = uuid4().hex
_tasks = {}


class PanelTurn(BaseModel):
    request_id: UUID
    question: str = Field(min_length=1, max_length=12000)
    document_name: str = Field(default="", max_length=240)
    pdf: str = Field(default="", max_length=12_000_000)
    source_run: UUID | None = None
    item_id: str = Field(default="", max_length=160)
    selection: str = Field(default="", max_length=12000)


def record_directory(manager, session, plugin, panel):
    identity = [session.tenant_id, session.user_id, session.id, plugin.name, panel["id"]]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    root = Path(manager.workdir).resolve()
    directory = root / ".tcode" / "plugin-runs" / key
    if directory.resolve() != directory:
        raise HTTPException(409, "无效的运行目录")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def active_count(workdir, plugin_name):
    root = Path(workdir).resolve() / ".tcode" / "plugin-runs"
    return sum(not task.done() and task.get_name() == f"panel:{plugin_name}" and path.is_relative_to(root)
               for path, task in _tasks.items())


def read_record(path):
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "运行记录不存在")
    item = json.loads(path.read_text())
    if item["status"] == "running" and item["boot"] != _BOOT:
        item.update(status="interrupted", error="服务已重启，请重新提交")
    return item


def public(item):
    return {key: item.get(key) for key in ("id", "status", "question", "document_name", "result", "error", "source_run", "item_id", "selection", "artifact")}


def history(manager, session, plugin, panel):
    directory = record_directory(manager, session, plugin, panel)
    return [public(read_record(path)) for path in sorted(directory.glob("*.json"), key=lambda f:f.stat().st_mtime, reverse=True)[:32]]


def _pdf_text(encoded):
    from pypdf import PdfReader
    data = base64.b64decode(encoded, validate=True)
    reader = PdfReader(io.BytesIO(data))
    pages = []
    size = 0
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        size += len(text)
        if size > 100_000 or index > 240:
            raise ValueError("文档超过当前 240 页或 100000 字输入上限，请选取相关页")
        pages.append(f"[第 {index} 页]\n{text}")
    if not any(page.partition("\n")[2].strip() for page in pages):
        raise ValueError("PDF 无可提取文本")
    return "\n\n".join(pages)


async def start(manager, session, plugin, panel, turn):
    directory = record_directory(manager, session, plugin, panel)
    path = directory / f"{turn.request_id}.json"
    fingerprint = hashlib.sha256(turn.model_dump_json().encode()).hexdigest()
    if path.exists():
        previous = read_record(path)
        if previous["fingerprint"] != fingerprint:
            raise HTTPException(409, "同一提交标识已用于其他输入")
        return public(previous)
    agents = {name: BuiltinAgents.get(name) for name in plugin.agents}
    lead = agents.get(panel.get("agent"))
    if lead is None or lead.name not in plugin.agents:
        raise HTTPException(409, "此面板没有已加载的声明式 Agent")
    members = (lead.options.get("team") or {}).get("worker_names", [])
    roles = [agents[name] for name in members] + [lead]
    pdf_bytes = b""
    document_ref = ""
    if turn.source_run:
        source = read_record(directory / f"{turn.source_run}.json")
        if source["status"] != "completed":
            raise HTTPException(409, "来源运行尚未完成")
        text, name = source["document"], source["document_name"]
        document_ref = source.get("document_ref", "")
    else:
        try:
            text = await asyncio.to_thread(_pdf_text, turn.pdf) if turn.pdf else ""
            pdf_bytes = base64.b64decode(turn.pdf, validate=True)
            document_ref = str(turn.request_id) if pdf_bytes else ""
        except Exception as exc:
            raise HTTPException(422, f"无法读取 PDF：{exc}") from exc
        name = Path(turn.document_name).name
    record = {"id":str(turn.request_id), "boot":_BOOT, "status":"running", "fingerprint":fingerprint,
              "document":text, "document_name":name, "question":turn.question,
              "source_run":str(turn.source_run or ""), "item_id":turn.item_id, "selection":turn.selection, "document_ref":document_ref}
    with file_transaction(path):
        if path.exists():
            previous = read_record(path)
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "提交标识冲突")
            return public(previous)
        if pdf_bytes:
            with NamedTemporaryFile(dir=directory, delete=False) as out:
                out.write(pdf_bytes)
            Path(out.name).replace(path.with_suffix(".pdf"))
        atomic_write_json(path, record)
    # Bind this submission to the confirmed harness selection, even before the next chat turn.
    model_ref = panel.get("model_id") or (session.metadata or {}).get("model") or None

    async def run():
        provider = None
        try:
            provider = create_provider(workdir=manager.workdir, model_ref=model_ref)
            context = json.dumps({"question":turn.question, "document":text, "selection":turn.selection}, ensure_ascii=False)
            responses = []
            for role in roles:
                configured = next((item for item in manager._load_configured_plugins() if item["name"] == plugin.name), {})
                grants = set(configured.get("panel_grants", []))
                if PluginRegistry.get(plugin.name) is not plugin or "team.run" not in grants or (text and "document.read" not in grants):
                    record.update(status="interrupted", error="插件已停用或权限已撤销")
                    return
                reply = await asyncio.wait_for(provider.chat(messages=[
                    ChatMessage(role="system", content=role.prompt),
                    ChatMessage(role="user", content=context + "\n" + "\n".join(responses)),
                ], tools=None, max_tokens=3200), 180)
                content = (reply.content or "").strip()
                if not content:
                    raise ValueError(f"{role.name} 未返回内容")
                responses.append(content)
            record.update(status="completed", result=responses[-1])
            from ..server import managed_files
            workspace = Path(manager.workdir).resolve()
            target = workspace / "download" / "plugin-results" / plugin.name / directory.name / f"{turn.request_id}.json"
            with NamedTemporaryFile(mode="w", encoding="utf-8") as output:
                json.dump(public(record), output, ensure_ascii=False, indent=2)
                output.flush()
                with managed_files.publish(workspace, target, Path(output.name)) as artifact:
                    record["artifact"] = artifact.relative_to(workspace).as_posix()
        except asyncio.CancelledError:
            record.update(status="cancelled")
            raise
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}：团队执行失败，请检查模型配置和服务状态")
        finally:
            atomic_write_json(path, record)
            if provider and getattr(provider, "close", None):
                await provider.close()
    task = asyncio.create_task(run(), name=f"panel:{plugin.name}")
    _tasks[path] = task
    def finished(done):
        _tasks.pop(path, None)
        if done.cancelled() and read_record(path)["status"] == "running":
            record.update(status="cancelled")
            atomic_write_json(path, record)
    task.add_done_callback(finished)
    return public(record)
