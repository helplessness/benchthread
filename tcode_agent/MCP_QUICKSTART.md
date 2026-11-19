# MCP 配置与验证

普通源码启动默认读取 Git 忽略的 `.tcode-local/mcp.json`。首次初始化生成的是 `{}`；不连接 MCP 时不用配置 TMCP。若文件缺失，运行 `./scripts/init_local_config.sh` 补齐。

## 部署自己的 FastMCP 服务

在 **MCP 所在主机**运行你自己的工具代码；不需要安装 BenchThread 或 TMCP。以下示例只回显文本，用来验证连接和认证，不需要 Artifact SDK：

```bash
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install 'fastmcp>=3.2,<4'
```

将以下内容保存为 `server.py`。已有 FastMCP 项目只需在创建 `FastMCP(...)` 时传入 `auth`，原有工具注册保持不变。

```python
import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
if not token:
    raise SystemExit("Set MCP_BEARER_TOKEN before starting this service")

auth = StaticTokenVerifier(tokens={token: {"client_id": "demo-client", "scopes": []}})
mcp = FastMCP("My tools", auth=auth)


@mcp.tool
def echo_text(text: str) -> str:
    return text


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=10240, path="/mcp")
```

令牌由部署者自己生成。把 `MCP_BEARER_TOKEN=你的随机令牌` **只保存一次**到仓库外的私有 `~/.config/my-mcp/service.env`（权限 `0600`）。不要把真实令牌写进 Python、URL 或 Git。手动启动时，可以在子 Shell 加载自己维护的配置：

```bash
(
  set -a
  . "$HOME/.config/my-mcp/service.env"
  set +a
  exec .venv-mcp/bin/python server.py
)
```

Linux 上若用 `systemd`，可让服务的 `EnvironmentFile=` 读取这个私有文件，`WorkingDirectory=` 指向项目目录，`ExecStart=` 使用虚拟环境 Python 和 `server.py` 的绝对路径。生产环境可改由密钥管理器注入。服务读取的是进程环境，不会自动读取 `.env`，也无需每次在终端输入。

上面的监听地址只供本机访问。接入 BenchThread Web 时，用带 HTTPS 的反向代理将 `/mcp` 转发到该进程，并使用 **BenchThread 后端可达**的地址。将同一个令牌注入 **BenchThread 后端**的 `TCODE_MCP_TOKEN`，再按下节配置 `Authorization: Bearer ${TCODE_MCP_TOKEN}`。确认能列出工具后，调用一次 `echo_text` 核对回显。仅需本机匿名开发时，可不配置 `auth`，但不要把未鉴权的示例暴露到公网。

需要收发文件时，另按 [Artifact SDK 部署与多服务配置](../packages/artifact-sdk/README.md#服务配置)配置文件服务；需要服务端持久化任务和重启恢复时，参考[第三方异步 MCP 部署](../docs/服务隔离/第三方MCP插件接入与运行.md)。

## 配置一个服务

这一节只配置已经启动的普通 MCP 服务。先按该服务的文档或[上面的 FastMCP 示例](#部署自己的-fastmcp-服务)启动工具端；BenchThread 不替第三方工具部署或启动服务。普通同步工具不需要 Job Protocol、Artifact 或 Docker，可沿用[本机 Web 启动方式](../README.md#linux-web发出第一条消息)。

```json
{
  "my-mcp": {
    "transport": "streamable-http",
    "url": "https://mcp.example.com/mcp",
    "connect_timeout": 10000,
    "timeout": 300000,
    "tool_timeout_seconds": {
      "long_running_tool": 14400
    },
    "headers": {
      "Authorization": "Bearer ${TCODE_MCP_TOKEN}"
    },
    "enabled": true
  }
}
```

字段解释见 [配置字段中文说明](../docs/配置字段说明.md)。只添加需要的服务，替换 URL 并设置所需环境变量。可选的普通、异步和蛋白部署配置见 [MCP 接入示例](../config/examples/README.md)。

请求头支持 `${ENV_NAME}` 占位符，并在建立连接时读取环境变量。变量不存在时连接会失败，错误只显示变量名，不显示其他请求头内容。不要把真实令牌写入公开模板或提交到 Git。

保存后重启 Web，或在 **设置 → MCP 服务** 重新加载连接。先确认已连接且能列出工具，再在会话中调用一次无副作用的工具验证。只看到连接状态不能证明工具调用成功。

## 选择配置文件

启动脚本会从 `.tcode-local/local.env` 读取：

```bash
TCODE_CONFIG_ROOT="$PROJECT_ROOT/.tcode-local"
TCODE_MCP_CONFIG="$TCODE_CONFIG_ROOT/mcp.json"
```

仅在主动设置 `TCODE_MCP_PROFILE=job-protocol-only` 时，Web / Server 启动脚本才改读 `mcp_job_protocol_only.json`。后台计算目标与前台服务分开配置。

从仓库根目录用 `--local` 启动时，默认读取已初始化的本机配置；非 `--local` 的直接部署命令可显式指定：

```bash
export TCODE_CONFIG_ROOT=/absolute/path/to/config
export TCODE_MCP_CONFIG="$TCODE_CONFIG_ROOT/mcp.json"
tcode-server --workdir /absolute/path/to/workspace
```

生产部署应把配置放在仓库之外，并通过进程环境或密钥系统注入令牌。

## 超时语义

- `connect_timeout`：初始化、会话建立和工具列表请求的超时，单位为毫秒；
- `timeout`：服务的默认调用超时，单位为毫秒；
- `tool_timeout_seconds`：按工具名覆盖调用超时，单位为秒，适合异步计算或长任务。

超时只控制等待边界，不保证远端任务可恢复。长任务还需要 Job Protocol、状态持久化和结果回收链路。

## 本地验证与远端验证

无需远端服务器即可运行本机协议桩 Smoke：

```bash
python scripts/dsh.py smoke \
  --scenario-timeout-seconds 45 \
  --total-timeout-seconds 120 \
  --report build/test-reports/real-entry-smoke.json
```

真实远端 MCP 需要另外验证：连接与认证、工具发现、长任务提交、进度恢复、取消、重连和最终 Artifact 完整性。本机 Smoke 通过不能替代这些结果。

Agent 可见的服务范围由 `.tcode-local/mcp_agent_config.json` 控制，模板和字段说明见 [配置字段中文说明](../docs/配置字段说明.md)。
