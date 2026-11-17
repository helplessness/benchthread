# BenchThread

BenchThread（原 TCode）是一个可自托管的 AI 研究工作台。把文献、工具调用、远程计算和分析结果留在同一个工作区，方便复查、继续或交给同事。蛋白质研究是其中一个应用场景，不是使用前提。

目前处于开源 Alpha，尚未验收生产多租户部署。界面与截图可能仍显示 TCode；`tcode`、`tcode-server` 和 Python 包名 `tcode-agent` 不变。

## 快速开始

**选择入口：** [Linux Web](#linux-web发出第一条消息) · [macOS 桌面（Apple Silicon）](desktop/README.md#打开方式) · [Windows Web（实验性）](docs/Windows-Web快速启动.md#1-首次准备) · [Docker Web](docs/RUNNING.md#docker-本机体验)

已有基础聊天？跳到[配置和启动其他服务](#安装与部署)。

![BenchThread 研究工作区：对话、结果表格与后续验证建议](docs/screenshots/research-chat.png)

*真实界面，使用虚构数据和本地演示模型，不代表实际科研结果。*

### Linux Web：发出第一条消息

准备 **Git、Python 3.12（含 venv/pip）、Node.js 22.12+（含 npm）**，以及一个可访问的文本模型服务。基础聊天不需要 Docker、Redis、GPU 或 MCP。

**首次聊天前要配置真实模型。** 安装脚本生成的 `.tcode-local/tcode.json` 只是模板，`demo/your-model-id` 不是可用模型。新用户建议直接在网页配置；如果想用文件，先按[文件配置步骤](docs/CONFIGURATION.md#从文件配置模型)填写真实模型 ID、地址和所需密钥。

```bash
git clone https://github.com/helplessness/benchthread.git
cd benchthread
./scripts/install_tcode.sh
source .venv/bin/activate
mkdir -p workdirs/local
TCODE_PROJECT_ROOT="$PWD" \
TCODE_CONFIG_ROOT="$PWD/.tcode-local" \
TCODE_MCP_CONFIG="$PWD/.tcode-local/mcp.json" \
tcode-server --local --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
```

1. 打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。已用文件配好模型的可以直接到第 3 步，不用在网页重复添加。否则打开 **设置 → 模型与执行 → Web 模型服务 → 添加聊天服务**，填协议、接口地址、模型 ID 和所需 API Key，点 **保存服务**。OpenAI 兼容接口填基础地址（通常以 `/v1` 结尾），不要附加 `/chat/completions`。
2. 对刚添加的模型点 **文本测试**，通过后选为 **实例默认对话模型**。
3. 回到聊天页，点 **新建任务**。在「创建会话」中选工作目录；没有目录时先输入 `demo-workspace` 并点 **新增目录**。点 **创建会话**，确认输入框选中了实际模型，发送「你好，请用一句话介绍你能做什么」。收到回复即跑通。

`--local` 仅用于本机个人试用，不要求登录，不要通过代理或隧道对外开放。用同一个 `--workdir` 重启可保留设置和会话。需要登录时走[带登录的 Web 部署](docs/RUNNING.md#按本机配置启动-web)；找不到模型时看[模型配置排查](docs/CONFIGURATION.md#模型不显示或无法发送)。

## 安装与部署

按你要做的事选择入口。**Docker Web 和工具沙箱是两套独立的部署，不用一起启动。**

| 用途 | 配置、启动与验证 | 需要什么 |
| --- | --- | --- |
| **用 Docker 跑 Web** | [Docker Compose：首次配置与启动](docs/RUNNING.md#docker-本机体验) | 配管理员和模型，再启动基础聊天；不包含工具沙箱或远程任务服务。 |
| **启用工具沙箱** | [Linux 工具沙箱部署](docs/RUNNING.md#linux-web-可选容器部署) · [Agent 浏览器](docs/RUNNING.md#linux-web-启用-agent-浏览器) · [通用执行器和 BLAST 镜像](docs/RUNNING.md#通用执行器和-blast-镜像) | 按需构建对应镜像、配置 Sandbox Runner；不需要另起 Docker Web。 |
| **连接普通 MCP** | [普通 MCP：配置与验证](tcode_agent/MCP_QUICKSTART.md#配置一个服务) | 先启动实际工具服务，再在 BenchThread 配置地址和认证；不需要 Job Protocol 或 Docker。 |
| **启用远程异步计算** | [Job Protocol 与执行端：启动和验收](docs/RUNNING.md#远程异步计算) | 配网关、后台执行和跟踪；实际执行计算的 MCP 服务需单独准备。 |
| **让远程工具收发文件** | [Artifact 文件服务：准备、接入与验收](docs/RUNNING.md#文件服务artifact) | 只在传文件时需要。安装文件 SDK 不等于启动文件服务。 |

其他入口：[运行指南](docs/RUNNING.md) · [配置指南](docs/CONFIGURATION.md) · [网页搜索配置](docs/CONFIGURATION.md#网页搜索可选) · [启动排查](docs/RUNNING.md#启动排查)。

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
