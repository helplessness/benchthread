# 运行指南

## Windows Web

按 [PowerShell Web 快速启动](Windows-Web快速启动.md)运行，不使用下方 Bash 脚本。仅提供 Web 源码启动路径，Windows 实机验收尚未完成。

## Linux Web 和 CLI

新用户按[快速开始](../README.md#快速开始)安装、填写模型并启动单机 Web；不需要先启动远程服务。需要按本机环境配置运行完整 Web 服务时，从仓库根目录执行：

```bash
./scripts/init_local_config.sh
PYTHON_BIN="$PWD/.venv/bin/python" ./scripts/start_web.sh --workdir "$PWD/workdirs/local"
```

先确保 `.tcode-local/local.env` 及其继承的配置已按用途设置；此启动方式可能拉起配置中启用的后台服务，不等同于 README 的最小启动方式。`/api/health` 验证 HTTP 服务启动，不验证模型、MCP 或远端计算。只使用 CLI 时，可在已安装的环境中运行 `TCODE_CONFIG_ROOT="$PWD/.tcode-local" tcode --workdir "$PWD/workdirs/local"`。

### Linux Web 可选容器部署

从源码启动 Web 时，按要测试的功能选择。Dockerfile 用来构建镜像，不代表有一个需要常驻的服务。

| 测试场景 | 需要构建的镜像 | 需要启动的服务 |
| --- | --- | --- |
| 基础聊天、本机模拟 Boltz | 无 | Web；模拟 Boltz 另启动本机模拟服务，不需要 Docker 或 Sandbox Runner |
| Agent 内置浏览器 | `tcode-browser:playwright`（`deploy/sandbox-base/browser`） | 出口代理容器和宿主机上的 Sandbox Runner；浏览器容器由 Runner 按需创建 |
| 通用容器隔离执行 | `tcode-executor:py312`（`deploy/sandbox-base/executor`） | 使用 Runner 部署时启动 Sandbox Runner；不手动启动执行容器 |
| 本地沙箱 BLAST | 先构建 executor，再构建 `tcode-tools-blast:latest`（`deploy/sandbox-tools/blast`） | 沿用执行沙箱，不单独启动 BLAST 服务 |
| 容器隔离的 Python 插件 | `tcode-plugin-runtime:py312`（`deploy/plugin-runtime`） | 启动 Sandbox Runner，由它创建插件 Worker；见[插件容器部署](plugin-sdk/07-容器隔离与受控能力调用.md#8-生产部署) |

如果只想把 **Web 本身**跑进容器，使用仓库根目录的 Compose，见 [Docker 本机体验](#docker-本机体验)。它与工具沙箱是两套独立的启动方式。

### Linux Web 启用 Agent 浏览器

以下是 **受信任的 Linux 单机开发**流程。先完成 [Linux Web 安装与模型配置](../README.md#linux安装并启动-web)，再在运行 Sandbox Runner 的机器准备 Docker Engine、Docker Compose 插件和 `curl`。不在宿主机另装 Chrome/ChromeDriver，也不需要 Cua。共享或生产部署应另配独立的 Runner 账号、gVisor 和生产隔离策略，不要直接对外开放这个开发配置。

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

## macOS 桌面开发版

```bash
./scripts/init_local_config.sh
./scripts/start_desktop.sh
```

首次启动需要联网、Python 3.12+ 与 Node.js 22.12+。桌面使用自己的本机后端与运行数据目录，启动桌面不要求先启动 Web；首次使用按[桌面说明](../desktop/README.md)完成设置。打包见 [macOS 打包](gui/12-macos-dmg-packaging.md)。

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
