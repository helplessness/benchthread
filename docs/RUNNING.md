# 运行指南

## Windows Web

按 [PowerShell Web 快速启动](Windows-Web快速启动.md)运行，不使用下方 Bash 脚本。仅提供 Web 源码启动路径，Windows 实机验收尚未完成。

## Linux Web 和 CLI

按你的用途选择：[只聊天](#只聊天本机-web) · [沙箱浏览器](#linux-web-启用-agent-浏览器) · [远程异步计算](#远程异步计算)。Web 自身装进容器是另一条路线，见 [Docker 本机体验](#docker-本机体验)。

### Linux Web 安装与构建

准备 Git（或下载源码）、Python 3.12（含 venv/pip）和 Node.js 22.12+（含 npm）。如需实际聊天，再准备一个可访问的文本模型服务。基础聊天不需要 Docker、Redis、GPU 或 MCP。

从仓库根目录运行：

```bash
./scripts/install_tcode.sh
source .venv/bin/activate
```

首次安装需要联网。脚本用锁定的 npm 依赖构建界面，从 `pyproject.toml` 安装 Python 包，并补齐 `.tcode-local/` 的公开配置模板，不覆盖已有文件。它不安装系统 Python、Node.js 或 npm；若提示缺少 `venv` / `ensurepip`，先安装发行版中与 Python 版本匹配的 venv 包。

默认依赖见 `requirements.lock`，`--full` 使用 `requirements-full.lock`；跨平台依赖解析见 `uv.lock`。可选依赖和服务见[配置指南](CONFIGURATION.md#需要哪些服务)。

### 只聊天：本机 Web

- **安装与首条消息**：按 [README 快速开始](../README.md#linux-web发出第一条消息)安装、配置模型并发送消息。内置工具仍可操作本机文件，请使用专用工作区。
- **启动**：使用快速开始中的 `tcode-server --local`，只启动本机 Web。它不加载 `.tcode-local/local.env`，也不启动 Job Protocol、后台执行与结果回收进程、Sandbox Runner 或 egress。**它仍会连接生效的 `mcp.json` 中启用的服务**；新初始化生成的是空清单，已有清单不会被覆盖。不需要已有 MCP 时，可通过 `TCODE_MCP_CONFIG` 指向另一份内容为 `{}` 的 JSON 文件，不用删除旧配置。
- **验证**：打开 [Web](http://127.0.0.1:8080)，选实际模型，新建工作区和会话，发一条问候并确认收到回复。网页添加的模型也可以先做「文本测试」。[`/api/health`](http://127.0.0.1:8080/api/health) 只确认 Web 进程就绪。

### 按本机配置启动 Web

沙箱和异步路径会复用这条启动方式，**不要加 `--local`**。已有部署继续用原启动命令；首次部署先按下面步骤准备账号。先在原终端停止占用 8080 的试聊服务，不要让两个 Web 进程同时使用同一运行目录。

#### 首次启用 Web 登录

Web 默认要求登录。认证库还没有用户时，启动前必须提供 `TCODE_BOOTSTRAP_PASSWORD`（至少 12 个字符，不用常见占位密码）。`TCODE_BOOTSTRAP_EMAIL` 可选，默认是 `admin@example.com`。这是 Web 管理员账号，不是模型 API Key。已有管理员时无需再次初始化，修改这些变量也不会重置账号。

#### 使用部署脚本

先确认私有的 `.tcode-local/local.env` 存在（缺失时运行 `./scripts/init_local_config.sh`）。首次启用登录，在其中设置 `TCODE_BOOTSTRAP_PASSWORD='你的强密码'`，替换成自己的密码；需要自定义邮箱时同处设置 `TCODE_BOOTSTRAP_EMAIL`。新模板默认不加载高级部署用的 `symphony_route.env`；已有配置继续按原方式加载，不必迁移或重复填写密码。不要将真实密码写入公开模板或提交该文件。

从仓库根目录运行：

```bash
mkdir -p workdirs/local
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/start_web.sh --workdir "$PWD/workdirs/local"
```

部署脚本会加载本机环境配置，认证、沙箱和后台服务以实际配置为准；若启用了强制 Runner，先准备隔离服务。基础聊天能用不代表所有工具都可用，不要绕过需要的隔离。

#### 只启动带登录的 Web

如果只想在本机验证带登录的 Web，也可以在 Bash/Zsh 用下方命令直接启动。密码输入不显示，不写入命令历史；已有管理员可省略读取和导出初始化密码的四行。

```bash
mkdir -p workdirs/local
(
  printf '请输入 Web 管理员密码（至少 12 个字符）：' >&2
  IFS= read -r -s TCODE_BOOTSTRAP_PASSWORD || exit 1
  printf '\n' >&2
  export TCODE_BOOTSTRAP_PASSWORD
  TCODE_PROJECT_ROOT="$PWD" \
  TCODE_CONFIG_ROOT="$PWD/.tcode-local" \
  exec ./.venv/bin/tcode-server --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
)
```

这里没有加 `--local`，所以仍显式指定 `TCODE_CONFIG_ROOT`。它不自动加载 `.tcode-local/local.env`；文件模型引用的环境变量需在启动终端设置，或改用部署脚本。看到 `Application startup complete` 后，打开 [Web 页面](http://127.0.0.1:8080) 登录，再[配置模型](CONFIGURATION.md#在-web-设置模型)。`/api/health` 只验证进程启动，不验证模型、MCP 或远端计算。

只使用 CLI 时，可在已安装的环境中运行 `TCODE_CONFIG_ROOT="$PWD/.tcode-local" tcode --workdir "$PWD/workdirs/local"`。做前端开发时保持选定的后端运行，在另一个终端执行 `npm --prefix web run dev`，访问 Vite 显示的地址；默认代理目标是后端 8080 端口。

### Linux Web 可选容器部署

从源码启动 Web 时，按要测试的功能选择。Dockerfile 用来构建镜像，不代表有一个需要常驻的服务。

| 测试场景 | 需要构建的镜像 | 需要启动的服务 |
| --- | --- | --- |
| 基础聊天、本机模拟 Boltz | 无 | Web；模拟 Boltz 另启动本机模拟服务，不需要 Docker 或 Sandbox Runner |
| Agent 内置浏览器 | `tcode-browser:playwright`（`deploy/sandbox-base/browser`） | 出口代理容器和宿主机上的 Sandbox Runner；浏览器容器由 Runner 按需创建 |
| 通用容器隔离执行 | [`tcode-executor:py312`](#通用执行器和-blast-镜像) | 使用 Runner 部署时启动 Sandbox Runner；不手动启动执行容器 |
| 本地沙箱 BLAST | [先构建 executor，再构建 `tcode-tools-blast:latest`](#通用执行器和-blast-镜像) | 沿用执行沙箱，不单独启动 BLAST 服务 |
| 容器隔离的 Python 插件 | `tcode-plugin-runtime:py312`（`deploy/plugin-runtime`） | 启动 Sandbox Runner，由它创建插件 Worker；见[插件容器部署](plugin-sdk/07-容器隔离与受控能力调用.md#8-生产部署) |

如果只想把 **Web 本身**跑进容器，使用仓库根目录的 Compose，见 [Docker 本机体验](#docker-本机体验)。它与工具沙箱是两套独立的启动方式。

### Linux Web 启用 Agent 浏览器

以下是 **受信任的 Linux 单机开发**流程。先完成 [Linux Web 安装](#linux-web-安装与构建)与[模型配置](CONFIGURATION.md#在-web-设置模型)，再在运行 Sandbox Runner 的机器准备 Docker Engine、Docker Compose 插件和 `curl`。不在宿主机另装 Chrome/ChromeDriver，也不需要 Cua。共享或生产部署应另配独立的 Runner 账号、gVisor 和生产隔离策略，不要直接对外开放这个开发配置。

这一场景要运行的是 **Web、宿主机上的 Sandbox Runner、egress 代理容器**。Playwright 浏览器镜像由 Runner 按需启动，不用手动运行浏览器容器；不需要 Job Protocol。在这条路径里，egress 代理沙箱浏览器的公网 HTTP/HTTPS 流量，不代理 Web 的模型请求或普通 MCP 连接。

1. 用实际运行 Runner 的 Linux 账号执行 `docker info`，确认能访问 Docker 守护进程，再从仓库根目录构建 Playwright/Chromium 镜像并启动受控出口代理。若只有 `sudo docker info` 能成功，说明当前账号还不能直接访问 Docker；请先由管理员为 Runner 账号配置访问权限，再以该账号重新验证。不要放宽 Docker socket 的权限或给 Web 账号额外授权。

   ```bash
   docker info
   docker build -t tcode-browser:playwright deploy/sandbox-base/browser
   docker compose -f deploy/sandbox-egress/compose.yaml up -d
   ```

2. 检查 `.tcode-local/local.env`。下列值放在文件末尾或核对已有设置；Web 与 Runner 应从同一份配置启动。工作区目录必须在 Runner 启动前存在。

   ```bash
   TCODE_SANDBOX_ENABLED=true
   TCODE_SANDBOX_BACKEND=docker
   TCODE_SANDBOX_DOCKER_RUNTIME=
   TCODE_SANDBOX_RUNNER_REQUIRED_RUNTIME=
   TCODE_BROWSER_REQUIRE_RUNNER=true
   TCODE_USER_WORKDIRS_ROOT="$TCODE_PROJECT_ROOT/workdirs"
   TCODE_SANDBOX_WORKSPACE_ROOT="$TCODE_USER_WORKDIRS_ROOT"
   TCODE_SANDBOX_RUNNER_WORKSPACE_ROOT="$TCODE_USER_WORKDIRS_ROOT"
   ```

   新安装生成的配置已包含以下值，核对即可，不用重复写入。Web、Runner 和冒烟脚本都应使用同一份 `TCODE_ENV_FILE`，以及它加载的同一份配置模板。

   | 核对项 | 单机模板值 |
   | --- | --- |
   | Runner 地址 | `TCODE_SANDBOX_RUNNER_HOST=127.0.0.1`、`TCODE_SANDBOX_RUNNER_PORT=19092`、`TCODE_SANDBOX_RUNNER_URL=http://127.0.0.1:19092`；若改端口，URL 也要改 |
   | Runner 认证 | `TCODE_SANDBOX_RUNNER_REQUIRE_TOKEN=true`、`TCODE_SANDBOX_AUTO_CREATE_TOKEN=true`；首次启动会在模板指定的 `tmp/sandbox-runner/runner.token` 创建令牌，无需手填 |
   | 浏览器镜像与出网 | `TCODE_BROWSER_SANDBOX_IMAGE=tcode-browser:playwright`、`TCODE_SANDBOX_EGRESS_PROXY=http://egress-proxy:3128`、`TCODE_SANDBOX_EGRESS_PROXY_CONTAINER=tcode-egress-proxy`；代理地址是容器网络内的地址，不是宿主机的 localhost |
   | 工具策略 | `TCODE_SANDBOX_RUNNER_TOOL_POLICY_FILE` 指向初始化生成的 `.tcode-local/sandbox_runner_tool_policy.json`；Runner 允许工具包含 `browser`，允许网络包含 `egress` |

   如果工作区权限不是 UID/GID `1000:1000`，同步调整 `TCODE_SANDBOX_USER` 与 `TCODE_SANDBOX_RUNNER_ALLOWED_USERS`，并确保配置中的浏览器档案没有覆盖为其他用户。

3. 启动 Runner、运行一次无需模型的浏览器冒烟，再启动 Web：

   如果之前按 README **方式 A** 用 `tcode-server --local` 试聊，先在原终端用 `Ctrl+C` 停止该次试聊，释放默认的 8080 端口。下面用 **方式 B** 启动，**不要加 `--local`**，否则不会加载这份部署配置。若之前只通过网页保存了模型，继续使用相同的运行数据目录；若通过终端变量配置模型，则在启动 Web 的终端重新设置所需变量。

   ```bash
   mkdir -p workdirs/browser-smoke/local/browser
   export TCODE_ENV_FILE="$PWD/.tcode-local/local.env"
   export PYTHON_BIN="$PWD/.venv/bin/python"
   ./scripts/start_sandbox_runner.sh start -d
   ./scripts/check_browser_sandbox.sh "$PWD/workdirs/browser-smoke/local/browser" https://example.com
   ./scripts/start_web.sh --workdir "$PWD/workdirs/local"
   ```

   冒烟命令应退出成功，并输出 `Browser sandbox smoke test passed.` 和 `network: egress`；如果失败，先排查再启动 Web。冒烟会检查真实容器、页面观察和点击，但不验证 Web 聊天链路。

   Web 启动后，打开 `http://127.0.0.1:8080`，按页面提示登录（如启用了认证），新建工作区和会话，选择支持工具调用的模型，发送「用内置浏览器打开 https://example.com，读出页面主标题」。确认聊天中出现实际的浏览器工具调用，且浏览器面板显示页面画面。只收到网页标题或只看到 `/api/health` 成功，都不算 Web 浏览器验收。

   停止时先用 `Ctrl+C` 退出 Web，再运行 `./scripts/start_sandbox_runner.sh stop`。确认没有其他工作区或服务依赖此代理后，才用 `docker compose -f deploy/sandbox-egress/compose.yaml down` 停止本次代理。

### 通用执行器和 BLAST 镜像

准备 Docker Engine，并在实际执行任务的 Runner 主机上构建。下面在仓库根目录运行；只需要通用执行器时，可跳过 BLAST 的构建和检查。

```bash
docker build -t tcode-executor:py312 deploy/sandbox-base/executor
# 按需：BLAST 镜像以刚构建的执行器镜像为基础，顺序不能颠倒。
docker build -t tcode-tools-blast:latest deploy/sandbox-tools/blast
```

检查镜像能启动、工具版本能读出：

```bash
docker run --rm --network none tcode-executor:py312 python --version
docker run --rm --network none --user 1000:1000 tcode-tools-blast:latest blastn -version
```

这些命令只验证镜像，不代表 Web 已能分配沙箱任务。实际使用时，在部署配置启用沙箱（`TCODE_SANDBOX_ENABLED=true`）；若通过 Runner 执行，设置 `TCODE_SANDBOX_REQUIRE_RUNNER=true`，并按上节核对 Runner 地址、认证、工作区根和工具策略。让 Web 与 Runner 读取同一份环境文件，从仓库根目录启动 Runner 并检查健康状态（端口有改动时替换 `19092`）：

```bash
TCODE_ENV_FILE="$PWD/.tcode-local/local.env" PYTHON_BIN="$PWD/.venv/bin/python" \
  ./scripts/start_sandbox_runner.sh start -d
curl -fsS http://127.0.0.1:19092/health
```

再[按本机配置方式](#按本机配置启动-web)启动 Web。执行容器由后端按任务创建，BLAST 不单独起服务。只有任务需要受控出网时才另启 egress；本地 BLAST 搜索如需数据库，还要单独准备并授权读取，构建镜像不会下载数据库。

### 远程异步计算

远程计算需要 **Web、Job Protocol 网关、实际执行任务的 MCP 服务**，并启用后台执行和结果跟踪。Job Protocol 只接受、转发和追踪任务，不替远端计算。在 Web 主机上，基本的纯 JSON 异步验证不需要 Sandbox Runner、egress、Docker 或 GPU；计算主机按各自工具准备依赖。涉及文件引用时，再配置双方可达的 Artifact 服务和认证。

1. **安装与启动执行端**：先用[纯 JSON 异步求和示例](../examples/third_party_mcp/json-async/README.md#1-启动第三方服务)验证。它用独立 Python 环境安装 FastMCP，在回环地址启动一个可恢复的示例服务，不需要私有 TMCP 或文件 SDK。
2. **选择网关，再启动 Web**：
   - **已有外部网关**：保留原网关、任务库和配置，先确保网关及它对应的后台执行与结果跟踪进程已运行。按[异步 MCP 接入指南](服务隔离/第三方MCP插件接入与运行.md#1-先确认-benchthread-侧的服务就绪)核对地址、认证和后台配置，再按本机配置方式启动 Web。即使网关配置的是 `127.0.0.1`，只要已指定地址，Web 也会复用它，不会自动拉起一个替代服务。
   - **新建单机内置网关**：在 Web 的 Python 环境补装 `.[desktop]` 可选依赖（提供 FastMCP，不安装 Electron）。只用于回环地址、不开 reload 的单进程 Web；若任一生效配置已指定外部网关，仍会优先用外部。完成[首次启用 Web 登录](#首次启用-web-登录)和本机配置后，从仓库根目录运行：

     ```bash
     ./.venv/bin/python -m pip install -e '.[desktop]'
     mkdir -p workdirs/local
     TCODE_MANAGED_JOB_PROTOCOL=1 PYTHON_BIN="$PWD/.venv/bin/python" \
       ./scripts/start_web.sh --workdir "$PWD/workdirs/local"
     ```

     内置模式由 Web 拉起网关和后台执行进程，无需手动再起一份，也不用给新网关在 `mcp.json` 预填地址。
3. **验证完整链路**：按[示例接入步骤](../examples/third_party_mcp/json-async/README.md#2-接入-benchthread)登记并启用目标；发起一次 `[1,2,3]` 求和，按界面要求审核。收到 `LOCAL-*` 只说明已受理，等完成通知后在原会话看到 `{"sum":6}` 才算跑通。要验证恢复，重开 Web 后用同一个运行目录和原任务去查结果，不重新提交。仅 Web 健康检查通过或 MCP 显示已连接，都不代表异步链路已验证。

### 文件服务（Artifact）

远程工具收发文件以及从文件服务直接导入工作区时，需要配置 Artifact 服务。普通文本工具和纯 JSON 异步任务可以跳过这一步。

1. **准备服务**：已有兼容 Artifact HTTP 服务就直接复用；没有的话，安装 [SDK 的可选服务端](../packages/artifact-sdk/README.md#启动-artifact-服务端)，在第三方 MCP 主机上同端口启动，或在两端都能访问的主机上独立运行 `tcode-artifact serve`。BenchThread 和基础 Compose 不会自动启动它，普通 SDK 客户端也不会占用端口。已有独立 TMCP 部署仍可沿用 [TMCP 兼容启动脚本](../scripts/start_artifact_server.sh)，该脚本需要 `tmcp.artifact_server`。
2. **配置两端**：在 Web「设置 → 远端服务」添加 Artifact 基础地址、服务 ID 和凭据环境变量名，设为默认或绑定对应 MCP，保存后重启 Web。若改用 `.tcode-local/local.env` 配置单个服务，同时设置 `TMCP_ARTIFACT_SERVICE_URL`、`TMCP_ARTIFACT_SERVICE_ID` 和服务要求的 `TMCP_ARTIFACT_API_TOKEN`。对 SDK 服务端，ID 必须与启动时的 `--service-id`（或 `/health` 返回值）一致，否则带 ID 的结果引用可能无法取回。第三方 MCP 也要使用相同 ID，见 [SDK 连接配置](../packages/artifact-sdk/README.md#服务配置)。已有桌面配置不变；文件服务令牌与 MCP 连接令牌分开设置。Web 向 SDK 服务端上传目录时，配置[可信文件包写入](../packages/artifact-sdk/README.md#可信文件包写入)。
3. **验收文件往返**：按[第三方异步 MCP 文件示例](服务隔离/第三方MCP插件接入与运行.md#2-在计算主机安装-sdk-并启动示例)启动示例并[登记和验证](服务隔离/第三方MCP插件接入与运行.md#3-在-benchthread-中登记并验证)。用一份无敏感内容的小文件做输入，核对回传产物；仅能连接服务不等于上传、下载和结果回收都正常。

#### 不绑定 MCP 使用 Artifact

在与 BenchThread Web 同机的终端启动独立服务。先通过部署环境为服务进程注入 `TMCP_ARTIFACT_API_TOKEN`，使用至少 24 位的随机密钥。

```sh
python -m pip install './packages/artifact-sdk[server]'
tcode-artifact serve --port 18081 --data-dir ./artifact-data --service-id lab-store
```

1. 在 Web「设置 → 远端服务 → Artifact 文件服务」新增服务。显示名称填「实验室文件服务」，服务 ID 填 `lab-store`，地址填 `http://127.0.0.1:18081/artifact`，凭据环境变量名填 `LAB_ARTIFACT_TOKEN`。MCP 绑定留空。若想省略聊天里的服务名，把它设为默认服务。
2. 在 **BenchThread 后端进程**注入 `LAB_ARTIFACT_TOKEN`，值与文件服务的令牌一致。按实际的租户和用户 ID 配置权限，保存设置后重启 Web。下面的示例分别授予 Alice 读取和上传权限。只浏览或下载时，仅授予读取权限即可。

   ```sh
   export TCODE_ARTIFACT_IMPORT_ACCESS='{"lab-store":{"my-tenant":["alice"]}}'
   export TCODE_ARTIFACT_UPLOAD_ACCESS='{"lab-store":{"my-tenant":["alice"]}}'
   ```

3. 打开当前工作区，放入一份测试文件 `sample.csv`。依次在聊天中发送以下指令，最后在工作区的 `imports/` 预览下载文件并核对内容。

   - 「把当前工作区的 `sample.csv` 上传到实验室文件服务的 `results/sample.csv`。」
   - 「列出实验室文件服务的 `results/` 目录。」目录有多页时，可以继续说「下一页」。
   - 「下载刚才列表里的 `results/sample.csv` 到当前工作区。」

读取授权覆盖整套服务上可浏览和下载的文件。共享目录与含私有任务产物的存储应使用不同的 Artifact 实例、数据目录和令牌。文件包的所有者限制保护写入，持有服务令牌的调用方仍可读取包内容。若 Web 与文件服务分开部署，使用 Web 后端可访问的 HTTPS 地址，并保留末尾的 `/artifact`。

已有任务继续使用它原来的服务 ID 和存储。添加新服务时使用新的 ID，并保留旧服务的配置与地址。改变默认服务前，先确认是否仍有只靠默认服务定位的旧引用。独立服务重启时继续使用原数据目录和服务 ID。

#### 从 Artifact 直接导入工作区

在聊天中说「列出实验室文件服务的 `results/` 目录」，可以分批查看直属文件、大小和子目录。选中文件后，Agent 按该条目下载到当前工作区。省略目录时，从根目录开始。目录浏览使用与导入相同的服务读取授权，兼容服务需提供 `GET /api/v1/files`。

在聊天中说「从实验室文件服务下载 `results/report.csv` 到当前工作区」。服务名称来自「设置 → 远端服务」，省略时使用默认服务。服务名称匹配到多项时，界面会请你选择。服务可独立使用。单文件使用上传时的文件名，已完成文件包中的内容使用包内相对路径。同一个服务出现同路径文件时，可用 `bundle-…/results/report.csv` 指定原文件包。

在 BenchThread 后端环境设置 `TCODE_ARTIFACT_IMPORT_ACCESS`，按服务 ID、租户 ID、用户 ID 明确授予该服务的文件读取范围。例如 `{"lab-store":{"my-tenant":["alice"]}}`。首版适用于整套文件服务授权给这些用户的情况。不同用户的私有文件应使用独立的服务或各自隔离的存储实例。服务还需配置访问令牌，并支持 `GET /api/v1/files/by-path?path=…`，仓库的 SDK 可选服务端已提供该接口。

导入支持 Linux 和 macOS 后端。通过读取和工作区写入授权后，文件保存到当前工作区的 `imports/`，单文件最大 64 MiB，并保留已有同名文件。

#### 上传到 Artifact 指定路径

在聊天中说「把当前工作区的 `reports/report.csv` 上传到实验室文件服务的 `2026/results/report.csv`」。服务选择沿用导入规则，省略名称时使用默认服务，有多个候选时由用户选择。独立文件服务和绑定 MCP 的文件服务都可以用。

在 BenchThread 后端环境为获准上传的用户设置 `TCODE_ARTIFACT_UPLOAD_ACCESS`，格式与导入授权一致。上传时分别检查工作区文件读取权限与远端路径写入权限。Linux 和 macOS 后端支持上传工作区内的普通文件，单文件最大 64 MiB。工作区之外的文件可以先通过现有的文件导入流程加入工作区。

服务端需支持 `PUT /api/v1/files/by-path?path=…`。仓库的 SDK 可选服务端已提供此接口，目标完整路径已存在时返回冲突并保留原文件。上传后可用原路径下载，也可沿用 Artifact 引用交给 `echo_file` 等支持该引用的工具。

## macOS 桌面开发版

Apple Silicon 用户按[桌面首次启动](../desktop/README.md#打开方式)准备依赖、初始化管理员、配置模型并发送第一条消息。桌面使用自己的本机后端与运行数据目录，不用先启动 Web。打包见 [macOS 打包](gui/12-macos-dmg-packaging.md)。

## Docker 本机体验

这套 Compose 只启动 BenchThread Web，默认只发布到本机 `127.0.0.1:8080`。宿主机只需 Docker Engine 和 Docker Compose 插件（或 Docker Desktop），不用另外安装 Python、Node.js、TMCP、Redis 或沙箱。第一回构建需要联网下载基础镜像和依赖；使用 Docker Desktop 时建议给构建环境预留约 8 GB 内存。

**首次配置**（在 BenchThread 仓库根目录执行；已有配置时直接编辑，不要重复复制）：

```bash
(umask 077; mkdir -p .tcode-local/docker-chat/config &&
  cp -n config/examples/local-demo/*.json .tcode-local/docker-chat/config/ &&
  cp -n config/examples/docker-chat.env.example .tcode-local/docker-chat/settings.env &&
  chmod 755 .tcode-local/docker-chat/config &&
  chmod 644 .tcode-local/docker-chat/config/*.json)
```

1. 在 `.tcode-local/docker-chat/settings.env` 设置至少 12 字符的独立管理员密码，以及实际模型的 `TCODE_DEMO_BASE_URL` 和 `TCODE_DEMO_API_KEY`。默认管理员邮箱为 `admin@example.com`，可在首次启动前修改。模型地址从**容器**访问；例如 Docker Desktop 下连接宿主机模型可用 `host.docker.internal`，不要填容器里的 `localhost`。不把密码或密钥写入 JSON 或提交到 Git。
2. 在 `.tcode-local/docker-chat/config/tcode.json` 把两处 `your-model-id` 都替换为服务商实际提供的模型 ID。容器的本机 HTTP 页面可以聊天，但网页模型管理需要 HTTPS；本机体验使用这份文件配置，不降低网页安全限制。

**配置好后，一条命令启动**：

```bash
docker compose up -d
```

打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)，用管理员邮箱和密码登录。点「新建任务」，按提示新增或选择一个工作目录，创建会话并发送第一条文本消息。`docker compose logs -f web` 可查看启动情况；`/api/health` 只说明 Web 已启动，不验证模型。

- 宿主机的配置目录只读挂载；账号、工作区和会话存入 Compose 的 `workspace` 命名卷。`docker compose down` 后再次执行启动命令仍会保留数据；不要用 `down -v` 停机，它会删除数据卷。改动环境文件后重新执行 `up -d`；改动账号引导密码不会重置已创建的账号。更新源码后用 `docker compose up -d --build`。
- 若本机端口已被占用，可用 `TCODE_DOCKER_PORT=18080 docker compose up -d`。Windows PowerShell 的环境变量语法不同；首次配置也可用文件管理器复制模板。
- 这是本机聊天配置，不包含飞书 CLI、桌面应用、Agent 浏览器沙箱、Job Protocol、远端 MCP 或文件服务，不要直接对外开放。

## 不连接远端服务的验证

暂时没有可用模型或 MCP 服务，也可在已安装依赖的仓库根目录运行真实入口验收：

```bash
python scripts/dsh.py smoke \
  --scenario-timeout-seconds 45 --total-timeout-seconds 120 \
  --report build/test-reports/real-entry-smoke.json
```

该命令用临时目录和本机 LLM/MCP 协议桩检查 WebSocket 对话、插件、CLI 与 Worker 入口。它验证集成链路，不代表真实模型效果或远端计算可用。

## 启动排查

| 现象 | 检查 |
| --- | --- |
| 启动时报 `TCODE_BOOTSTRAP_PASSWORD` | 首次开启登录时，确认启动进程拿到了自定义的强密码；见[首次启用 Web 登录](#首次启用-web-登录)。 |
| 页面提示未构建或显示旧界面 | 从源码构建时重新运行 `npm --prefix web run build` 并刷新；用安装包启动时更新包内前端后再启动。 |
| 8080 已被占用 | 更改 `--port`；Docker Web 改 `TCODE_DOCKER_PORT`，Vite 开发模式同时调整代理目标。 |
| 找不到 MCP 或插件工具 | 新生成的 MCP 清单为空。按[普通 MCP 配置](../tcode_agent/MCP_QUICKSTART.md#配置一个服务)、[异步计算](#远程异步计算)或[插件 SDK](plugin-sdk/README.md)启用所需工具。 |
| 沙箱执行器不可用 | 基础聊天可不配置执行器；需要隔离的工具按[工具沙箱部署](#linux-web-可选容器部署)配置，不能退回宿主绕过隔离。 |
| 模型列表为空或发送失败 | 按[模型配置排查](CONFIGURATION.md#模型不显示或无法发送)核对。 |
