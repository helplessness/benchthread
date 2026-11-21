"""A reviewed foreground argv, never a network-enabled shell."""

import asyncio
import base64
import ctypes
import ipaddress
import os
from pathlib import Path
import re
import shlex
import secrets
import signal
import socket
import shutil
import tempfile
from urllib.parse import urlsplit

from ...types import ToolResult
from .macos_native import MAX_BYTES, _excluded

PROJECT_TRANSFER_HELP = (
    "In a linked desktop folder, download a public repository by running a standalone "
    "git clone https://host/owner/repo command in the foreground, or download a public HTTPS "
    "file with curl -o filename URL. Use the default working directory. Do not wrap in a shell "
    "or combine with setup/check commands: those use the ordinary offline sandbox. "
    "A successful transfer is already saved in the linked folder. To remove one named project "
    "file or folder, run standalone trash relative/path (recoverable; never use rm)."
)


def transfer_argv(command):
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    if len(args) == 2 and args[0] == "trash":
        if all(re.fullmatch(r"[\w][\w. -]*", p) and not _excluded(p) for p in args[1].split("/")):
            return "trash", "", args[1], False
        return None
    if args[:2] == ["git", "clone"]:
        rest = args[2:]
        shallow = rest[:2] == ["--depth", "1"]
        rest = rest[2:] if shallow else rest
        if len(rest) not in (1, 2):
            return None
        url = rest[0]
        dest = (
            rest[-1]
            if len(rest) == 2
            else url.rstrip("/").split("/")[-1].removesuffix(".git")
        )
        kind = "git"
    elif args[:1] == ["curl"]:
        rest = args[1:]
        while rest and re.fullmatch(r"-[fsSL]+", rest[0]):
            rest = rest[1:]
        if len(rest) != 3:
            return None
        if rest[0] in ("-o", "--output"):
            _, dest, url = rest
        elif rest[1] in ("-o", "--output"):
            url, _, dest = rest
        else:
            return None
        kind, shallow = "curl", False
    else:
        return None
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::443)?/[A-Za-z0-9_./%~+-]+", url):
        return None
    if not all(
        re.fullmatch(r"[\w][\w. -]*", p) and not _excluded(p) for p in dest.split("/")
    ):
        return None
    return kind, url, dest, shallow


async def run_project_transfer(parsed, context, tool):
    if (
        os.environ.get("TCODE_DESKTOP_MANAGED") != "1"
        or getattr(parsed, "wait", True) is False
    ):
        return None
    spec = transfer_argv(parsed.command)
    if not spec or parsed.workdir not in (
        None,
        "",
        ".",
        "/workspace",
        (context.extra or {}).get("workdir"),
    ):
        return None
    from ...server import project_files, workdirs
    from ...tools.path_policy import resolve_workspace_io_path
    from ..command_policy import (
        evaluate_command_policy,
        CommandPolicyAudit,
        _permission_action,
        profile_allows_tool,
    )
    from fastapi import HTTPException

    kind, url, dest, shallow = spec
    extra = context.extra or {}
    try:
        # Only the parsed shortcut destination uses file-write routing, never arbitrary shell commands.
        target = resolve_workspace_io_path(dest, context, tool_id="write", operation="write")
        if target.blocked:
            raise ValueError(target.reason)
        grant = project_files.selected_tool_grant(target, extra)
        if not grant:
            return None
        if target.relative_path != dest:
            raise ValueError("请选择已关联的项目和普通相对目标路径")
        decision = evaluate_command_policy(
            parsed.command,
            context=context,
            tool_name=tool,
            requested_workdir=parsed.workdir,
        )
        CommandPolicyAudit.record(decision, context, tool)
        if (
            set(decision.violations)
            - ({"write_approval"} if kind == "trash" else {"sandbox_network_none", "write_approval", "network_approval"})
            or _permission_action(context, "write") == "deny"
            or (kind != "trash" and _permission_action(context, "network") == "deny")
            or not profile_allows_tool(extra.get("execution_profile") or "", "write")
            or not (extra.get("command_policy") or {}).get(
                "allow_workspace_write", True
            )
            or not callable(extra.get("__permission_ask__"))
        ):
            raise ValueError("当前命令策略不允许这次项目操作")
        scope = workdirs.resolve_workspace_context(
            **{k: extra[k] for k in ("workspace_id", "tenant_id", "user_id")}
        )
        runtime = os.environ["TCODE_WORKDIR"]
        label = f"{parsed.command}  [工作目录: {grant['path']}; 目标: {dest}]"
        for permission in (("write",) if kind == "trash" else ("network", "write")):
            if not await context.ask(permission, [label]):
                raise ValueError("本次操作未获授权")

        def cancelled():
            if context.abort and context.abort.is_set():
                raise ValueError("任务已取消")

        cancelled()
        if kind == "trash":
            project_files.trash_bound(runtime, scope, grant, dest)
            return ToolResult(title=parsed.command, output=f"已移到废纸篓，原位置：{Path(grant['path']) / dest}",
                              metadata={"exitCode": 0, "project_path": f"@roots/{grant['root_id']}/{dest}"})
        if target.host_path.exists():
            raise ValueError("托管工作区存在同名路径，未覆盖")
        # A default change is fine; revoking the grant shown in the prompt is not.
        project_files.selected_tool_grant(target, extra)
        host = urlsplit(url).hostname
        records = await asyncio.get_running_loop().getaddrinfo(
            host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        ips = [ipaddress.ip_address(r[4][0]) for r in records]
        if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
            raise ValueError("仅允许公开 HTTPS 来源")
        # Seatbelt can restrict a remote port to localhost, not to an arbitrary IP.
        # Proxy only CONNECT for the approved host; connect to the prevalidated numeric IP.
        password = secrets.token_hex(24)
        auth = b"basic " + base64.b64encode(f"project:{password}".encode())

        async def tunnel(reader, writer):
            upstream = None
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                if header.split(b"\r\n", 1)[0].split()[:2] != [
                    b"CONNECT",
                    f"{host}:443".encode(),
                ]:
                    return
                if not any(
                    line.partition(b":")[0].lower() == b"proxy-authorization"
                    and secrets.compare_digest(
                        line.partition(b":")[2].strip()[6:], auth[6:]
                    )
                    and line.partition(b":")[2].strip()[:6].lower() == b"basic "
                    for line in header.split(b"\r\n")[1:]
                ):
                    writer.write(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="project"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
                    )
                    await writer.drain()
                    return
                remote, upstream = await asyncio.wait_for(
                    asyncio.open_connection(str(ips[0]), 443), 10
                )
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")

                async def pump(source, destination):
                    size = 0
                    while data := await source.read(65536):
                        size += len(data)
                        if size > 2 * MAX_BYTES:
                            break
                        destination.write(data)
                        await destination.drain()

                tasks = [
                    asyncio.create_task(pump(reader, upstream)),
                    asyncio.create_task(pump(remote, writer)),
                ]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            except (
                OSError,
                TimeoutError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
            ):
                pass
            finally:
                writer.close()
                if upstream:
                    upstream.close()

        listener = await asyncio.start_server(tunnel, "127.0.0.1", 0)
        async with listener:
            return await _execute_transfer(
                listener,
                password,
                kind,
                url,
                dest,
                shallow,
                runtime,
                scope,
                grant,
                parsed,
                cancelled,
            )
    except (ValueError, OSError, HTTPException, TimeoutError) as exc:
        return ToolResult(
            title=parsed.command,
            output="",
            error=str(exc) or "命令超时",
            metadata={"retryable": False},
        )


async def _execute_transfer(
    listener,
    password,
    kind,
    url,
    dest,
    shallow,
    runtime,
    scope,
    grant,
    parsed,
    cancelled,
):
    from ...server import workdirs, project_files

    port = listener.sockets[0].getsockname()[1]
    proxy = f"http://project:{password}@127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(dir=runtime, prefix=".net-exec-") as tmp:
        root = Path(tmp).resolve()
        env = dict(
            PATH="/usr/bin:/bin",
            HOME=str(root),
            TMPDIR=str(root),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_TERMINAL_PROMPT="0",
            GIT_ALLOW_PROTOCOL="https",
            GIT_TEMPLATE_DIR="",
            GIT_LFS_SKIP_SMUDGE="1",
            LANG="C",
        )
        # Only system binaries; no inherited credentials, shell, hooks, redirects or arbitrary flags.
        git = next(
            (
                p
                for p in (
                    "/Library/Developer/CommandLineTools/usr/bin/git",
                    "/Applications/Xcode.app/Contents/Developer/usr/bin/git",
                )
                if Path(p).is_file()
            ),
            "/usr/bin/git",
        )
        env["GIT_EXEC_PATH"] = str(Path(git).parent.parent / "libexec/git-core")
        argv = (
            [
                "/usr/bin/curl",
                "-q",
                "--fail",
                "--silent",
                "--show-error",
                "--proto",
                "=https",
                "--proxy",
                proxy,
                "--max-filesize",
                str(MAX_BYTES),
                "--output",
                "payload",
                "--write-out",
                "%{http_code}",
                url,
            ]
            if kind == "curl"
            else [
                git,
                "-c",
                "credential.helper=",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "http.followRedirects=false",
                "-c",
                f"http.proxy={proxy}",
                "clone",
                *(["--depth", "1"] if shallow else []),
                "--",
                url,
                "payload",
            ]
        )
        import json

        literal = json.dumps(str(root))
        profile = f"""(version 1)(deny default)(allow process-fork)(allow process-exec)(allow sysctl-read)(allow file-read-metadata)
                (allow file-read* (literal "/") (subpath "/usr") (subpath "/bin") (subpath "/System") (subpath "/Library/Developer")
                 (subpath "/Applications/Xcode.app") (subpath "/private/etc") (subpath "/dev") (subpath {literal}))
                (allow file-write* (subpath {literal}) (literal "/dev/null"))
                (allow mach-lookup (global-name "com.apple.trustd.agent") (global-name "com.apple.trustd") (global-name "com.apple.system.opendirectoryd.libinfo"))
                (allow network-outbound (remote ip "localhost:{port}"))"""
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-p",
            profile,
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=root,
            env=env,
            start_new_session=True,
        )
        task = asyncio.create_task(proc.communicate())
        try:
            async with asyncio.timeout(min(120, max(1, parsed.timeout or 90))):
                while not task.done():
                    cancelled()
                    await asyncio.wait([task], timeout=0.2)
                    if (
                        sum(
                            p.lstat().st_size
                            for p in root.rglob("*")
                            if not p.is_symlink() and p.exists()
                        )
                        > MAX_BYTES
                    ):
                        raise ValueError("超过 64 MiB 限制")
                out, err = task.result()
        finally:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await task
        if proc.returncode or (kind == "curl" and out != b"200"):
            raise ValueError(
                f"下载未完成（退出码 {proc.returncode}）：{err.decode(errors='replace')[-400:] or out.decode(errors='replace')}"
            )
        cancelled()
        payload = root / "payload"
        if kind == "curl":
            project_files.publish_bound(
                runtime, scope, grant, dest, payload.read_bytes()
            )
        else:
            # Check the actual checkout before the atomic directory publication.
            import configparser
            config = configparser.RawConfigParser(strict=False)
            config.read(payload / ".git/config", encoding="utf-8")
            checkout_verified = (
                config.get('remote "origin"', "url", fallback="") == url
                and bool((payload / ".git/HEAD").read_bytes().strip())
            )
            if any(
                p.is_symlink() and not p.resolve().is_relative_to(payload)
                for p in payload.rglob("*")
            ):
                raise ValueError("仓库包含指向项目外部的符号链接，未导入")
            with workdirs._WORKSPACE_MUTATION_LOCK:
                workdirs.assert_workspace_active(runtime, scope)
                project_files.check_writable(runtime, scope, grant)
                with project_files.opened(grant, "", directory=True) as fd:
                    parent = os.dup(fd)
                    try:
                        for part in dest.split("/")[:-1]:
                            try:
                                os.mkdir(part, 0o700, dir_fd=parent)
                            except FileExistsError:
                                pass
                            child = os.open(
                                part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                            )
                            os.close(parent)
                            parent = child
                        # Darwin RENAME_EXCL publishes the complete checkout without replacing an existing directory.
                        _publish_checkout(payload, parent, dest.split("/")[-1], cancelled)
                    finally:
                        os.close(parent)
    ref = f"@roots/{grant['root_id']}/{dest}"
    published = Path(grant["path"]) / dest
    return ToolResult(
        title=parsed.command,
        output=(f"已克隆到项目：{published}；已核对 .git/HEAD 和来源 {url}。普通文件列表隐藏 .git。"
                if kind == "git" and checkout_verified else f"已保存到项目：{published}"),
        metadata={"exitCode": 0, "sandbox": True, "project_path": ref, **(
            {"published_checkout": {"origin": url, "path": ref}}
            if kind == "git" and checkout_verified else {}
        )},
    )


def _publish_checkout(payload, parent, name, cancelled):
    from contextlib import ExitStack
    directory = os.fstat(parent)
    with ExitStack() as stack:
        if payload.stat().st_dev != directory.st_dev:
            # Darwin's inode path pins the destination directory, not its spelling.
            tmp = stack.enter_context(tempfile.TemporaryDirectory(
                dir=f"/.vol/{directory.st_dev}/{directory.st_ino}", prefix=".tcode-clone-"))
            payload = Path(shutil.copytree(payload, Path(tmp) / "payload", symlinks=True))
        cancelled()
        if ctypes.CDLL(None, use_errno=True).renameatx_np(
                -2, os.fsencode(payload), parent, os.fsencode(name), 4):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
