# 完整研究体验：从首聊到工具与产物

这是一条 **Linux 单机 Web** 的推荐部署路线，从根 README 进入即可按顺序完成。它不是“所有功能一键打开”：Mac 电脑控制只在 macOS 桌面提供，真实蛋白计算需要自备计算服务，目标模式仍属实验性。Windows Web 按[PowerShell 指南](Windows-Web快速启动.md)部署，不能照抄本页 Bash 命令。

全程从仓库根目录操作。配置使用 `.tcode-local/`，Web 运行数据统一使用 `workdirs/local`。已有用户不要覆盖配置或更换运行目录；本指南不迁移旧数据。

## 1. 安装与首聊准备

需要 Git、Python 3.12（含 venv/pip）、Node.js 22.12+/npm，以及可访问的模型接口。后续工具体验需要模型支持工具调用；截图理解另需支持图片的模型。先不需要 GPU、Redis、Neo4j 或 Docker。

```bash
git clone https://github.com/helplessness/benchthread.git
cd benchthread
./scripts/install_tcode.sh
```

安装会构建界面并生成配置，不会覆盖已有配置。`--full` 仅增加部分 Python 依赖，不启动服务，也不包含所有 MCP 依赖。

打开 `.tcode-local/local.env`，取消管理员配置行的注释，填写自己生成的强密码（至少 12 字符）。默认邮箱为 `admin@example.com`，可在首次创建账号前修改。密码不是模型 API Key，不能用示例密码，也不要发到聊天中。

```dotenv
# 去掉行首 # 后填写自己的值；已有管理员时不会重置密码。
# TCODE_BOOTSTRAP_EMAIL=admin@example.com
# TCODE_BOOTSTRAP_PASSWORD=
```

这条路线不使用 `--local`，因为需要加载环境配置和后续异步能力。若已运行试聊，先用 Ctrl+C 停止它；保留原来的 `workdirs/local`。

## 2. 启用研究能力

在 **同一份 `.tcode-local/local.env` 末尾**加入下列内容。只改私有配置，不改公开模板。不必取消文件顶部 `source symphony_route.env` 的注释：那份高级配置还会引入其他路由与服务假设。

```bash
# research-experience-start
# 研究记录、候选归属与正式课题
TCODE_RESEARCH_ENABLED=true
TCODE_RESEARCH_JOURNAL_ENABLED=true
TCODE_RESEARCH_ATTRIBUTION_ENABLED=true
TCODE_RESEARCH_ATTRIBUTION_SHADOW_MODE=true
TCODE_RESEARCH_TOPICS_ENABLED=true
# 研究片段、检查点与关联资料
TCODE_RESEARCH_EPISODES_ENABLED=true
TCODE_RESEARCH_CHECKPOINTS_ENABLED=true
TCODE_RESEARCH_KNOWLEDGE_LINKS_ENABLED=true
TCODE_RESEARCH_RESUME_CONTEXT_ENABLED=true
# 研究包导出与导入
TCODE_RESEARCH_PACK_ENABLED=true
TCODE_RESEARCH_PACK_IMPORT_ENABLED=true
# research-experience-end
```

这些开关有依赖关系，不能只开 `TCODE_RESEARCH_PACK_ENABLED`。候选与资料归属仍要核对，不会把所有聊天自动收进研究包；这里也没有开启目标自动审核或放宽工具权限。

从仓库根目录启动，后续重启始终使用同一命令：

```bash
PYTHON_BIN="$PWD/.venv/bin/python" \
  ./scripts/start_web.sh --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
```

打开 `http://127.0.0.1:8080` 登录。在 **设置 → 模型与执行** 添加真实模型服务，做文本测试并选为默认对话模型；需识图时再配置图片模型。完整表单步骤见[模型设置](CONFIGURATION.md#在-web-设置模型)。创建工作区和任务，发出第一条消息。接口应使用基础地址，不附加 `/chat/completions`。

如果 Linux 在远端，通过[SSH 转发](../README.md#方式-b带登录启动)访问带登录的 Web，不把免登录模式公开出去。网页新增模型地址有安全限制：内网模型按[文件配置](CONFIGURATION.md#从文件配置模型)接入，不通过关闭安全检查解决。

### 研究体验验收

使用合成资料，不需要先跑蛋白计算：

1. 在同一工作区讨论一个小课题，例如“比较两种样品保存条件，先整理假说与验证方案，不执行计算”。
2. 打开研究空间，核对候选并确认正式课题；若没有候选，检查研究功能是否开启和记录归属，不把普通聊天回复当作已建档。
3. 在课题中审核假说、创建实验记录，查看“实验与分支”。创建记录不等于已执行实验；不要虚构测量结果或把计划标为完成。
4. 选取已有记录导出研究包；在另一个测试研究空间导入，检查来源记录与内容。导入不是复现计算环境。
5. 用 Ctrl+C 停止 Web，按同一命令重启，确认原会话、课题和记录仍在。模型若“消失”，先检查是否换了 `--workdir`。

文献库、Paper Wiki 和领域知识依赖各自的数据与服务，不因开启研究开关就自动获得文献或私有知识库。网页搜索可按[搜索配置](CONFIGURATION.md#网页搜索可选)选 SearXNG 或 Brave；结构查看使用自己的合法 PDB/mmCIF 文件，授权后再要求聚焦或测距。

### 常用研究操作会改变什么？

| 操作 | 是否启动计算 | 影响与下一步 |
| --- | --- | --- |
| 创建实验记录 | 不会 | 先记录假说、输入和目的；以后可关联已有运行。 |
| 启动计算 | 会尝试提交 | 核对输入、资源与审核要求，在运行详情看状态；已提交不等于已有结果。 |
| 完成并冻结 | 不会 | 保留本次判断和快照；继续尝试请新建后续路线。 |
| 导入研究包 | 不会 | 先检查再接收来源资料，不执行包里的脚本，也不重建计算环境。 |
| 启动复现 | 可能会 | 确认目标课题与来源实验后创建独立复现实验；兼容性检查通过才启动，原记录保留。 |
| 归档课题 | 不会 | 随课题归档关联聊天，不删除文件；可在「研究中心 → 当前课题与历史」恢复。已有活动课题时，先处理当前课题。 |

在未冻结的实验里展开已完成的计算轮次，点「对话中换文件继续」。聊天草稿会单独标出关联的运行；可移除引用，不必抄编号。请在空闲的普通原生会话中发送，先核对新主输入文件，再按原有审核流程决定是否提交。

例如，只想让研究最多再推进两轮：在课题的「持续研究设置」中将运行次数上限设为 2，按需设置时间或用量预算。启动后在同一处看已用次数和上限；显示 1/2 表示还有一次额度，不表示第一次运行成功。需要暂缓时点暂停，检查记录后再决定是否恢复。不要把暂停当成撤销已提交的远程任务。

### 第一次核对一条结论（不需要远程计算）

准备一份自己的测试资料，正文写两句：“展馆有三处路标。AmberCompass 指南针位于第 28 站的木桌上。”这只是练习材料，不是研究发现。

1. 在研究库的资料库中导入这份材料。确认材料解析成功后，到同一工作区的「文献 Wiki → 变更审核」检查生成的建议。只有确认内容与原文相符才批准；导入文件本身不等于已有正式 Wiki 页面。
2. 在「知识页面」搜索 `AmberCompass`。若结果显示正文片段，点击即可定位；只命中标题时不会假装定位到原文。没有匹配时先清除筛选，再检查所在工作区以及建议是否仍待审核。
3. 打开页面，点「查看证据」，再点一条结论的来源。核对显示的材料标题、原文位置和摘录；需要带到实验记录里时点「复制结论与出处」。

**不要混淆三种状态：**「没有关联证据」表示这条结论未附来源；「来源暂不可读」表示已有引用但还没取到原文；「证据存在冲突」表示材料间有分歧，需分别核对，不能只摘录支持的一方。模型生成的页面和措辞可能不同；若没有生成对应结论，不要为了走完示例而批准无依据的建议。

## 3. 启用隔离命令与浏览器

此步骤才需要 Docker。不要把 Docker socket 挂进 Web，也不要用取消隔离的方式解决启动错误。

按[沙箱账号与共享配置](RUNNING.md#沙箱账号与共享配置)准备两个账号：Runner 账号有 Docker 权限，Web 账号没有；两者可读同一私有配置、共享所需工作区权限和 Runner 令牌。这个系统账号准备需要部署管理员完成。

用 Runner 账号构建镜像：

```bash
docker build -t tcode-executor:py312 deploy/sandbox-base/executor
docker build -t tcode-browser:playwright deploy/sandbox-base/browser
docker compose -f deploy/sandbox-egress/compose.yaml up -d
```

接着按[浏览器部署步骤 2–4](RUNNING.md#linux-web-启用-agent-浏览器)设置工作区根、容器 UID/GID、出网代理与 Runner 令牌，启动 Runner 并通过浏览器 smoke。还要在共用 `local.env` 中设置 `TCODE_SANDBOX_REQUIRE_RUNNER=true`，让 Bash/Python 按[通用执行器步骤](RUNNING.md#通用执行器和-blast-镜像)走 Runner。

换回 Web 账号，用第 2 步的同一运行目录启动 Web。分别要求“用 Bash 执行 pwd”和“用内置浏览器打开 https://example.com，读取主标题”。看到实际工具输出和页面才算通过；健康接口成功或模型口头描述不算。

## 4. 启用异步计算

先用合成数据测试，不需要真实 GPU 工具。停止 Web，在 Web 的 Python 环境补装现有可选依赖：

```bash
./.venv/bin/python -m pip install -e '.[desktop]'
```

这里的 `desktop` extra 提供 FastMCP 等 Python 依赖，不会安装 Electron。首次单机部署在 `local.env` 末尾加入：

```bash
TCODE_MANAGED_JOB_PROTOCOL=1
```

按第 2 步命令启动 Web。无外部网关配置时，它会启动内置网关及后台执行；已有外部网关则优先复用，不自动切换。不要仅为测试删除旧服务配置或任务库。

然后按[纯 JSON 异步示例](../examples/third_party_mcp/json-async/README.md)启动独立执行端、登记插件目标，只提交一次求和。验收标准是原会话收到 `sum=6`，不只是受理编号。示例服务的数据库、Web 的运行目录均需保留；重启后查原任务，不重投。

## 5. 跑通文件往返

复用第 4 步的 Web 与网关，按[`echo_file` 完整教程](../packages/artifact-sdk/README.md#echo_file-快速开始)启动临时 MCP + Artifact 服务，配置服务 ID、凭据、显式 MCP 绑定和文件字段。

- 不要再启动第二个占用相同端口、相同运行目录的 Web；给现有 Web 注入教程里的令牌后重启。
- MCP 地址必须从 Web 后端可达；教程中的私网 IP 要换成服务机器真实地址。
- 普通 Web MCP 表单不接受环回地址；不要把仅监听 `127.0.0.1` 的示例当成跨机器服务。
- 新 SDK 服务地址以 `/artifact` 结尾。模板中的历史默认 `127.0.0.1:10120` 不代表已有服务，更不能直接当作新 SDK 地址。
- 普通共享服务用于任务文件时必须显式绑定对应 MCP；Web 每用户个人令牌绑定目前仅支持同步文件操作，不用它测试后台异步回收。

收到输出后，在原工作区打开 `echo.dat`，比较输入输出 SHA-256。再重启 Web，确认任务记录和文件仍在。计算已完成但文件待回收时，修复原文件服务的连接或权限，不重提计算。

不需要计算、只想管理文件时，走[独立文件服务](RUNNING.md#不绑定-mcp-使用-artifact)；不必部署 Job Protocol。该节的用户与租户 ID 必须替换为实际身份，不照抄 `alice`。

## 6. 扩展与部署完成清单

- [ ] 模型测试和第一条消息通过，选择的模型支持所需工具调用。
- [ ] 能确认课题、建立实验记录，并导出/导入研究包。
- [ ] Bash 有真实工具输出，浏览器有实际页面（启用沙箱时）。
- [ ] 异步求和得到结果，任务不是一直停在受理状态。
- [ ] 文件往返内容一致，结果实际落到原工作区。
- [ ] 同目录重启后原记录仍在，未因验证而重复提交。

自定义工作流从已有模板运行开始，再导入或编辑自己的定义；模板引用的工具需要实际接入。对话生成流程与目标模式仍属实验阶段，不用自动审核代替首次验收。真实 Boltz 等计算需单独部署其 MCP、权重和计算资源，公共示例不包含它们。

macOS 的应用控制和浏览器人工接管请走[桌面路线](../desktop/README.md#网页操作与电脑控制)，不要期待 Linux Web 控制浏览器所在电脑的 Mac 应用。基础 Docker Compose 明确关闭研究、异步和沙箱，本页不是那份 Compose 的直接配置覆写。

## 出问题时提供什么

先看[启动排查](RUNNING.md#启动排查)。反馈时附：系统、源码标签/提交、所走步骤、启动命令（删去密钥）、失败现象和相关错误日志。区分“打不开页面”“模型失败”“工具不可用”“任务完成但文件未回收”。不要上传 `.tcode-local`、令牌或完整私有会话；部署失败也可以提交 Issue，不必等完全跑通才反馈。

## 本指南的验证边界

本轮已检查研究配置块的实际生效、重复初始化不覆盖配置、启动与网关回归，以及无私有配置的源码副本中的 Web 协议冒烟、HTTP 文件往返和服务重启恢复。源码副本复用了已有 Python 依赖，并非全新操作系统安装。协议冒烟使用测试模型，不代表真实模型效果。Linux 双账号 Docker 沙箱、真实模型聊天及研究包的完整界面操作仍需按上述清单实机验收；本指南不把这些未测步骤标为通过。
