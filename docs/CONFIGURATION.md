# 配置指南

在仓库根目录运行 `./scripts/init_local_config.sh`，一次将全部 13 份[公开模板](../config/README.md)补齐到 `.tcode-local/`，已有文件不会覆盖。源码安装脚本已包含这一步。`.tcode-local/` 仅保存在本机，不提交 Git。

| 场景 | 必填或需确认 | 不使用时 |
| --- | --- | --- |
| 基础聊天 | 在 Web / 桌面设置中添加聊天服务，或在 `tcode.json` 中配置模型和服务地址 | 可以不配置视觉模型、Embedding、插件和 MCP |
| macOS 桌面 | 在桌面模型设置或 `tcode.json` 配置模型；有登录要求时配置本机账号 | 不需要改 `desktop.env` 就保持默认；`desktop-mcp.json` 由桌面管理 |
| MCP | 只添加并启用需要的服务，填写 URL 与相应认证，确认服务和工具的准入范围 | 服务清单保持 `{}`，不需要部署 TMCP |
| 多人部署 | 配置认证、服务凭据、隔离执行器、持久化路径和网络边界 | 不直接使用 README 的单机体验参数 |

`model` 使用 `provider-id/model-id` 格式。`baseURLEnv` 和 `apiKeyEnv` 引用环境变量名称，不是地址和密钥本身。JSON 文件保持标准格式；注释见[配置字段说明](配置字段说明.md)。不要把真实凭据写进公开模板。

### 在 Web 设置模型

打开 **设置 → 模型与执行 → Web 模型服务**，添加服务，填写协议、接口地址、模型 ID 和 API Key，再保存。可分别点击每个模型的「文本测试」和「图片测试」；只有正确识别内置测试图才算图片测试通过。测试不发送工作区内容，不验证工具调用。网页添加的模型测试结果会保留，修改接口地址或密钥后需要重测。

可分别选择实例默认对话模型和图片理解模型，也可选同一个支持图片的模型。已有会话和工作区的对话模型选择不会被强制替换。手工文件配置仍可用；管理员可以测试已有文件模型，或清除网页默认值以沿用文件配置。

共享部署须由实例管理员通过 HTTPS 操作。无登录配置仅允许后端明确绑定回环地址、浏览器直接从本机访问时使用，不要通过反向代理或隧道公开本机模式。网页新建的远程模型服务仅支持公网 HTTPS 地址；源码本机模式可连接回环模型服务（如本机 Ollama），其他内网模型由运维通过配置文件管理。

网页模型设置和密钥保存在后端的 `<运行数据目录>/.tcode/web-models.json`。按根 README 启动时就是 `workdirs/local/.tcode/web-models.json`；重启沿用同一 `--workdir` 即可保留。密钥不回传浏览器、不写进 `tcode.json`；不要提交或共享该文件。

### 从文件配置模型

如果选择文件配置，不用在网页重复添加。打开 `.tcode-local/tcode.json`，按以下步骤填写 OpenAI 兼容服务：

1. 确认有 `provider.demo`（见[公开模板](../config/templates/tcode.example.json)）。它使用 `@ai-sdk/openai-compatible`，通过 `options.baseURLEnv` 和 `options.apiKeyEnv` 引用地址与密钥变量。已有配置没有 `demo` 时，只设置环境变量不会新增模型；可以添加对应服务，不要覆盖已有配置。
2. 把 `provider.demo.models` 下的 `your-model-id` 键换成服务商提供的实际模型 ID。
3. 把顶层 `model` 改成 `demo/实际模型ID`，与上一步一致。`demo` 是服务名，`your-model-id` 是占位符，不是真实模型。只聊天可以不配图片模型，只有需要识图时才把 `vision_model` 指向支持图片输入的真实模型。

用你自己的值替换下方示例，并在**启动 Web 的同一终端**执行。OpenAI 兼容接口填写服务商的基础 URL（通常以 `/v1` 结尾），不要附加 `/chat/completions`；不鉴权的本地服务可用 `local` 作密钥占位。

```bash
export TCODE_DEMO_BASE_URL='https://your-provider.example/v1'
export TCODE_DEMO_API_KEY='replace-with-your-key'
```

从项目根目录用 `--local` 启动时，会自动读取已有的 `.tcode-local/tcode.json`。显式设置 `TCODE_CONFIG_ROOT` 时以指定目录为准；该变量不改变 `--workdir` 指定的会话数据目录。`--local` 不会加载 `.tcode-local/local.env`；使用[部署脚本](RUNNING.md#使用部署脚本)时，可以把这两个变量写进私有的 `local.env`。

配置完成后启动 Web（已运行则重启），在会话输入框确认选中了实际模型。地址和密钥变量不会替旧会话切换模型；需要时手工重新选择。

### 模型不显示或无法发送

- **没有可用的对话模型**：公开模板的 `demo/your-model-id` 和图片占位模型不进入可用列表。先在网页添加，或[填写文件配置](#从文件配置模型)。旧版本可能显示占位项并在发送时报 404，不需要为此删除会话。
- **确认后端读到了哪组模型**：访问当前 Web 地址的 [`/api/models`](http://127.0.0.1:8080/api/models)（端口以实际部署为准）。`models` 为空时，核对启动目录、`TCODE_CONFIG_ROOT` 和对应 `tcode.json` 的条目；有模型时，在聊天下拉框确认选择，或在设置里展开「测试文件中配置的模型」。这些模型不会出现在网页添加的服务主列表中。
- **`Provider not found: ollama`**：请求引用了未配置的 `ollama/…`。若本来要用 OpenAI 兼容接口，选择已配置的真实模型即可，不需要安装 Ollama。
- **有模型但测试或聊天失败**：核对接口路径、模型 ID、权限、密钥和网络。文本测试不验证模型的工具调用能力；`/api/health` 也不测试模型连接。

### 按需开启扩展

- `mcp.json` 是前台服务清单；`mcp_agent_config.json` 控制 Agent 可见范围。后台任务的服务配置与前台分开，见 [MCP 接入](../tcode_agent/MCP_QUICKSTART.md)。
- `plugins.json` 只登记已审阅的插件；安装包、权限申请和运行授权是不同环节。见[插件 SDK](plugin-sdk/README.md)。
- `human_review.json` 管理工具审核规则；`sandbox_runner_tool_policy.json` 是 Runner 侧工具执行的权限上限。不要为解决连接问题而放宽审核或隔离策略。
- `local.env` 继承同目录的 `symphony_route.env`。`desktop.env` 与 `mcp_host.env` 分别用于桌面与独立服务主机；非对应场景无需填写。

### 需要哪些服务？

基础聊天只需 BenchThread 和一个可访问的模型接口。SQLite 随项目使用，无需另起数据库；保留同一运行数据目录即可继续使用会话与运行状态。证据图和本体使用本地 SQLite 与图文件等存储，不需要 Neo4j；特定研究插件可能需要自备有权使用的数据和额外依赖。

| 按需能力 | 什么时候配置 |
| --- | --- |
| Redis | 仅为配置了 Redis 后端的部分通知和 Swarm 消息启用；不替代 SQLite，也不等于高可用。 |
| FAISS 与 Embedding | 需要证据语义召回时安装并配置；未启用语义索引时仍可使用关键词检索。 |
| 普通 MCP | 工具直接返回结果时，按[普通 MCP 配置](../tcode_agent/MCP_QUICKSTART.md#配置一个服务)接入；不需要 Job Protocol、TMCP 或 Docker。 |
| Job Protocol 和计算执行端 | 有远程异步任务才[启用网关和执行端](RUNNING.md#远程异步计算)。GPU、模型权重和领域工具由实际计算主机准备，不是 Web 启动依赖。 |
| Artifact 文件服务 | 只有工具要收发文件引用时才[配置及验证](RUNNING.md#文件服务artifact)；纯文本和纯 JSON 任务不需要。 |
| 工具隔离、Agent 浏览器 | 源码 Web 按需[部署工具沙箱](RUNNING.md#linux-web-可选容器部署)；[Docker Web](RUNNING.md#docker-本机体验)是另一条路线。桌面使用原生隔离，电脑控制按系统提示授权。 |

`./scripts/install_tcode.sh --full` 安装 swarm、Redis 客户端、FAISS 等可选 Python 依赖，不部署 Redis、不下载 Embedding 模型。共享部署前请先读[安全说明](../SECURITY.md)。

### 网页搜索（可选）

基础聊天不需要网页搜索。默认的 SearXNG 需能从 BenchThread 后端访问且允许 JSON 搜索，可用 `TCODE_SEARXNG_BASE_URL` 指定地址（默认 `http://127.0.0.1:8888`）。

也可在桌面或由 Web 实例管理员打开 **设置 → 网页搜索**，选择 SearXNG 并填写地址，或选 Brave 并填写 API Key。保存后用于后续搜索；没有保存过的字段仍取启动配置。点「测试连接」只发送一次固定查询，Brave 可能消耗额度；修改配置后需重新测试。密钥保存在对应后端的私有运行目录，不会回显；Web 和桌面分开配置。

若用启动环境配置 Brave，先从 [Brave Search API](https://api-dashboard.search.brave.com/documentation/guides/authentication)取得密钥，再设置并重启后端：

```bash
export TCODE_WEBSEARCH_PROVIDER=brave
export TCODE_BRAVE_SEARCH_API_KEY='replace-with-your-key'
```

Brave 后端的 `websearch` 仅用于普通网页搜索，每次最多请求一页；图片、视频和新闻等分类仍用 SearXNG。切回 SearXNG 时改回 `searxng` 或取消变量。失败不会自动切换；用 Brave 时，查询关键词会发送给 Brave。

### 产品技能与沙箱挂载

- 源码仓库自带的产品技能位于 [`.tcode/skills/`](../.tcode/skills/)，不需要安装 Claude；技能是用法指导，不等于已连接对应模型或 MCP。BenchThread 仍识别 `.claude/skills/`；已有自定义技能不用搬，开发辅助技能也保留原位。直接从非 editable 安装启动源码仓库的 Web 时，按 README 设置 `TCODE_PROJECT_ROOT`，让加载器能找到该仓库。
- Linux Sandbox Runner 模板列出新旧两个技能根。升级后如手工维护挂载白名单，将项目内的 `.tcode/skills` 加到 `TCODE_SANDBOX_RUNNER_ALLOWED_MOUNT_ROOTS`，仅在该目录由部署管理员维护时才纳入 `TCODE_SANDBOX_RUNNER_TRUSTED_MOUNT_ROOTS`。旧配置如果已列出同一 BenchThread 项目的 `.claude/skills`，Runner 会沿用该项配置兼容新路径；不会扩大到整个 `.tcode/` 或其他工作区。技能脚本仍只读挂载。不要把用户可写的工作区或个人技能目录列为可信根。
- `.tcode/` 的运行数据和本机安装的技能仍默认被 Git 忽略。只有确认要随源码交付的具体技能文件才显式加入版本控制。macOS 冻结后端仍只随包附带 `anatomy`、`docx` 和 `pdf`，统一从 `.tcode/skills/` 打包。

### 生效与本机数据

源码启动脚本默认使用 `.tcode-local/local.env`，可用 `TCODE_ENV_FILE` 显式指定环境文件；`TCODE_CONFIG_ROOT` 指定核心配置目录。修改环境文件后重启对应进程，桌面需完全退出再打开。运行记录、缓存和用户产物与配置分开，见[数据边界](MAINTENANCE.md#数据边界)。
