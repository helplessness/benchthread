# BenchThread

BenchThread（原 TCode）是一个可自托管的 AI 研究工作台。把文献、工具调用、远程计算和分析结果留在同一个工作区，方便复查、继续或交给同事。蛋白质研究是其中一个应用场景，不是使用前提。

目前处于开源 Alpha，尚未验收生产多租户部署。界面与截图可能仍显示 TCode；`tcode`、`tcode-server` 和 Python 包名 `tcode-agent` 不变。

## 快速开始

**选择入口：** [Linux Web](#linux-web发出第一条消息) · [macOS 桌面（Apple Silicon）](desktop/README.md#打开方式) · [Windows Web（实验性）](docs/Windows-Web快速启动.md#1-首次准备) · [Docker Web](docs/RUNNING.md#docker-本机体验)

已有基础聊天？跳到[配置和启动其他服务](#安装与部署)。

![BenchThread 研究工作区：对话、结果表格与后续验证建议](docs/screenshots/research-chat.png)

*真实界面，使用虚构数据和本地演示模型，不代表实际科研结果。*

### Linux Web：发出第一条消息

**首次安装必需：Git、Python 3.12（含 venv/pip）、Node.js 22.12+（含 npm）和网络访问。** 先安装这些系统依赖，并确保 Linux 主机能访问 GitHub、PyPI 和 npm 仓库（或对应镜像）。安装脚本负责下载项目依赖、构建前端和生成配置模板，不会安装系统依赖。脚本默认查找 `python3.12`；若兼容的解释器不在该命令名下，用 `PYTHON_BIN=/path/to/python ./scripts/install_tcode.sh` 指定。若缺少 `venv` 或 `ensurepip`，见[运行指南](docs/RUNNING.md#linux-web-安装与构建)。

**发送消息还需要**一个从 Linux 主机可访问的文本模型服务，以及该服务要求的凭证。基础聊天不需要 Docker、Redis、GPU 或 MCP。

Linux Web 的两种入口启动的是同一个服务。首次安装步骤相同，按场景选一条启动命令：

| 场景 | 启动入口 | 是否加载 `.tcode-local/local.env` |
| --- | --- | --- |
| [A. 本机试聊（无需登录）](#方式-a本机试聊无需登录) | `tcode-server --local` | 不加载 |
| [B. 带登录部署](#方式-b带登录启动) | `./scripts/start_web.sh`（不加 `--local`） | 默认在文件存在时加载 |

#### 方式 A：本机试聊（无需登录）

**启动命令必须带 `--local`**。这个模式只在本机环回地址监听，不要求登录，也不用设置 `TCODE_BOOTSTRAP_PASSWORD`。不要通过代理或隧道对外开放。

**首次聊天前要配置真实模型。** 安装脚本生成的 `.tcode-local/tcode.json` 只是模板，`demo/your-model-id` 不是可用模型。新用户建议直接在网页配置；如果想用文件，先按[文件配置步骤](docs/CONFIGURATION.md#从文件配置模型)填写真实模型 ID、地址和所需密钥。

```bash
git clone https://github.com/helplessness/benchthread.git
cd benchthread
./scripts/install_tcode.sh
source .venv/bin/activate
mkdir -p workdirs/local
TCODE_PROJECT_ROOT="$PWD" \
tcode-server --local --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
```

1. 打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。已用文件配好模型的可以直接到第 3 步，不用在网页重复添加。否则打开 **设置 → 模型与执行 → Web 模型服务 → 添加聊天服务**，填协议、接口地址、模型 ID 和所需 API Key，点 **保存服务**。OpenAI 兼容接口填基础地址（通常以 `/v1` 结尾），不要附加 `/chat/completions`。
2. 对刚添加的模型点 **文本测试**，通过后选为 **实例默认对话模型**。
3. 回到聊天页，点 **新建任务**。在「创建会话」中选工作目录；没有目录时先输入 `demo-workspace` 并点 **新增目录**。点 **创建会话**，确认输入框选中了实际模型，发送「你好，请用一句话介绍你能做什么」。收到回复即跑通。

直接运行 `tcode-server` 时，不设置 `TCODE_CONFIG_ROOT` 会优先使用已有的项目根目录 `.tcode-local/`；如果从已安装的命令启动，也可在项目根目录运行，让它找到当前目录下的 `.tcode-local/`。找不到该目录时沿用原来的默认路径。**找到配置目录不等于加载其中的 `local.env`**：`.tcode-local/tcode.json` 是应用配置，`local.env` 是部署脚本加载的环境文件。显式设置 `TCODE_CONFIG_ROOT` 可指定配置目录，`--workdir` 仍用于指定运行目录。

用同一个 `--workdir` 重启可保留设置和会话。找不到模型时看[模型配置排查](docs/CONFIGURATION.md#模型不显示或无法发送)。

#### 方式 B：带登录启动

首次安装沿用方式 A 的安装步骤（到 `./scripts/install_tcode.sh` 为止），**不要运行 A 的启动命令**。如果已经启动了试聊，先用 `Ctrl+C` 停掉它，避免两个进程占用同一端口或运行目录。

新安装的基础 Web 只需在 `local.env` 配置首次登录，管理员密码只填一处。新模板默认不加载高级部署用的 `symphony_route.env`。已有部署沿用原来的环境文件和加载方式，不必迁移或重复填写密码。

1. 编辑仓库根目录下生成的 **`.tcode-local/local.env`**。其中的 `# TCODE_BOOTSTRAP_PASSWORD=` 只是注释，不是默认密码。首次创建管理员时，去掉行首 `#`，填入自己设置的强密码（至少 12 个字符，不用示例值）。登录邮箱默认是 `admin@example.com`。这个密码不是模型 API Key，修改它也不会重置已有账号的密码。

   ```bash
   TCODE_BOOTSTRAP_PASSWORD='替换为你自己生成的强密码'
   ```

   等号两边不要加空格。单引号可保留密码里的空格和 `$` 等字符；如果密码本身含单引号，需按 Shell 语法转义，或重新生成不含单引号的强密码。不要把真实密码写入公开模板或提交到 Git。

2. 从仓库根目录用部署脚本启动，**不加 `--local`**。脚本默认读取 `.tcode-local/local.env`；如果已设置 `TCODE_ENV_FILE`，则以显式指定的环境文件为准。直接运行 `tcode-server` 即使去掉 `--local`，也不会默认读取 `local.env`。

   ```bash
   mkdir -p workdirs/local
   PYTHON_BIN="$PWD/.venv/bin/python" \
   ./scripts/start_web.sh --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
   ```

3. 如果浏览器和服务在同一台机器上，打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)；如果服务跑在远程 Linux 上，按下文转发后访问。用管理员邮箱和密码登录，再按方式 A 的网页步骤配置模型并发送消息。

**从自己的电脑访问远程 Linux（仅方式 B）：** 保持服务绑定远程主机的 `127.0.0.1:8080`，不用对外开放 Web 端口。在你自己电脑的终端执行下面的命令，把 `user@server` 换成实际的 SSH 用户和服务器地址：

```bash
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 user@server
```

保持 SSH 连接打开，然后在自己电脑的浏览器访问 [http://127.0.0.1:18080](http://127.0.0.1:18080) 并登录。远程主机需可通过 SSH 登录且允许端口转发。**不要用这种方式转发免登录试聊。**

更多说明见[带登录的 Web 部署](docs/RUNNING.md#按本机配置启动-web)。

## 安装与部署

按你要做的事选择入口。**Docker Web 和工具沙箱是两套独立的部署，不用一起启动。**

| 用途 | 配置、启动与验证 | 需要什么 |
| --- | --- | --- |
| **用 Docker 跑 Web** | [Docker Compose：首次配置与启动](docs/RUNNING.md#docker-本机体验) | 配管理员和模型，再启动基础聊天；不包含工具沙箱或远程任务服务。 |
| **启用工具沙箱** | [组件分工与启动顺序](#linux-web沙箱组件与启动顺序) · [隔离执行 Bash 或 Python](#linux-web隔离执行-bash-或-python) · [Agent 浏览器](docs/RUNNING.md#linux-web-启用-agent-浏览器) · [通用执行器和 BLAST 镜像](docs/RUNNING.md#通用执行器和-blast-镜像) | 按需构建对应镜像、配置 Sandbox Runner；不需要另起 Docker Web。 |
| **连接普通 MCP** | [普通 MCP：配置与验证](tcode_agent/MCP_QUICKSTART.md#配置一个服务) | 先启动实际工具服务，再在 BenchThread 配置地址和认证；不需要 Job Protocol 或 Docker。 |
| **启用远程异步计算** | [Job Protocol 与执行端：启动和验收](docs/RUNNING.md#远程异步计算) | 配网关、后台执行和跟踪；实际执行计算的 MCP 服务需单独准备。 |
| **让远程工具收发文件** | [用 `echo_file` 跑通第一次往返](packages/artifact-sdk/README.md#echo_file-快速开始) · [Artifact 部署指南](docs/RUNNING.md#文件服务artifact) | 只在传文件时需要。安装文件 SDK 不等于启动文件服务。 |

其他入口：[运行指南](docs/RUNNING.md) · [配置指南](docs/CONFIGURATION.md) · [网页搜索配置](docs/CONFIGURATION.md#网页搜索可选) · [启动排查](docs/RUNNING.md#启动排查)。

### Linux Web：沙箱组件与启动顺序

**只聊天不用启用这套沙箱。** 如果你希望 Agent 既能隔离执行 Bash/Python，又能用内置浏览器访问公网，就需要下面两种任务镜像、一个出网代理容器，以及一个宿主机上的 Runner 服务。

| 组件 | 负责什么 | 怎么启动 |
| --- | --- | --- |
| 通用执行器 `tcode-executor:py312` | 提供运行 Bash 命令和 Python 脚本的隔离环境。 | 先构建镜像；Runner 收到任务后按需创建容器，不用手动 `docker run`。 |
| 浏览器 `tcode-browser:playwright` | 在沙箱里运行 Chromium，供 Agent 打开和操作网页。 | 先构建镜像；Runner 按需启动浏览器容器。 |
| 出网代理 `tcode-egress-proxy` | 代理沙箱的公网 HTTP/HTTPS 请求，按规则拦截内网等目标；不代理 Web 自己的模型请求。 | 需要受控出网时，用 `docker compose -f deploy/sandbox-egress/compose.yaml up -d` 后台启动。 |
| Sandbox Runner | 接收 Web 的执行请求，通过 Docker 创建和管理任务容器。 | 用 `./scripts/start_sandbox_runner.sh start -d` 启动宿主机上的 Python 服务；Runner 自己不是容器。 |

**启动顺序：** 用有 Docker 权限的 Linux 账号构建镜像、启动所需的出网代理和 Runner，再换到**没有 Docker 权限**的账号按[方式 B](#方式-b带登录启动)启动 Web。两个账号都要能读取同一份私有配置，并能按需访问同一工作区。

只跑离线 Bash/Python 不需要出网代理或浏览器镜像。要用内置浏览器访问公网，启动代理后还要在共用的 `.tcode-local/local.env` 里填下面两项。`egress-proxy` 是 Docker 网络内的服务名，不要换成 `localhost`。[浏览器配置与启动步骤](docs/RUNNING.md#linux-web-启用-agent-浏览器)

```bash
TCODE_SANDBOX_EGRESS_PROXY=http://egress-proxy:3128
TCODE_SANDBOX_EGRESS_PROXY_CONTAINER=tcode-egress-proxy
```

**在 Web 验证：** 新建工作区，选支持工具调用的模型。先发「用 Bash 执行 `pwd`」，确认看到实际工具输出；如果启用了浏览器，再发「用内置浏览器打开 `https://example.com`」，确认看到浏览器工具调用和页面画面。

### Linux Web：隔离执行 Bash 或 Python

**只聊天可以跳过这一节。** 如果要让 Web 里的 Agent 在容器里跑 Bash 命令或 Python 脚本，请配置 Sandbox Runner。通用执行器是镜像，不是常驻服务；Runner 会按需创建容器，不用手动 `docker run`。单纯离线执行不需要浏览器镜像或出口代理。

1. 在运行 Runner 的 Linux 主机上准备 Docker Engine。用有 Docker 权限的 Runner 账号，从仓库根目录构建镜像；不要给 Web 账号 Docker 权限。

   ```bash
   docker build -t tcode-executor:py312 deploy/sandbox-base/executor
   ```

2. 在 Web 与 Runner 共同使用的 `.tcode-local/local.env` 里启用沙箱。下面的地址使用同机模板的 `127.0.0.1:19092`；如果改过 Runner 的监听地址或端口，URL 也要对应修改。按详细指南核对 Runner 令牌、工作区根目录和工具策略，确保 `bash`、`exec` 按策略走沙箱。

   ```bash
   TCODE_SANDBOX_ENABLED=true
   TCODE_SANDBOX_REQUIRE_RUNNER=true
   TCODE_SANDBOX_IMAGE=tcode-executor:py312
   TCODE_SANDBOX_RUNNER_URL=http://127.0.0.1:19092
   ```

3. 用 **Runner 账号**执行以下命令。两账号的共享令牌和目录权限见[运行指南](docs/RUNNING.md#沙箱账号与共享配置)。

   ```bash
   mkdir -p workdirs
   TCODE_ENV_FILE="$PWD/.tcode-local/local.env" PYTHON_BIN="$PWD/.venv/bin/python" \
     ./scripts/start_sandbox_runner.sh start -d
   curl -fsS http://127.0.0.1:19092/health
   ```

4. 换到 **Web 账号**，按[方式 B](#方式-b带登录启动)启动或重启 Web，读取同一份配置，不加 `--local`。`/health` 只确认 Runner 就绪，能否执行请按上面的 Web 步骤验证。

## 功能进度

- **研究过程可以回查**：在工作区整理文献、假说、实验和证据；查看交互图表与分子结构。
- **任务由你把关**：设置目标和预算、复用工作流，必要时审核工具参数。结果验收会标明未通过或缺证据的部分，不等于科学结论已证实。
- **计算可以在远端继续**：接入支持可恢复任务的远端服务后，可用原任务 ID 查进度、取结果，不必重新提交。

[完整功能、研究场景与 Roadmap](docs/FEATURES.md) · [Alpha 范围与边界](docs/PUBLIC-ALPHA.md)

## 文档与开发

- **接入示例**：[纯 JSON 异步 MCP](examples/third_party_mcp/json-async/README.md)（不需要文件 SDK） · [文件传输 SDK](packages/artifact-sdk/README.md)（远端收发文件） · [插件 SDK](docs/plugin-sdk/README.md)（BenchThread 侧扩展）。
- **项目维护**：[文档目录](docs/README.md) · [项目架构](docs/MAINTENANCE.md) · [贡献指南](CONTRIBUTING.md) · [安全与漏洞报告](SECURITY.md)。

## 许可证与来源

项目代码按 [MIT License](LICENSE) 提供。第三方组件和外部资料沿用各自的许可证及版权声明。
