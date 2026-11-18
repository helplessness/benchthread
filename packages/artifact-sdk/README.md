# BenchThread Artifact Python SDK

同仓库维护、可独立安装的文件客户端，可选安装 Artifact 服务端。发行包名 `tcode-artifact-sdk`，导入名 `tcode_artifacts`。当前版本 0.1.0，仅提供源码／wheel 安装，**尚未发布到 PyPI**。

[第一次让 MCP 收发文件：`echo_file` 快速开始](#echo_file-快速开始) · [只在聊天中使用文件服务，不接 MCP](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)

基础客户端只依赖 `httpx`；安装基础包或导入 `ArtifactIO` 不会启动服务，也不导入 `tcode_agent` 或 `tmcp`。客户端和可选服务端沿用 Artifact HTTP v1 接口和 `ArtifactRef`／`BundleRef`，不实现计算、队列、审核或轮询。Python 3.10+。客户端在 Linux／macOS 保留安全目录描述符实现；**Windows 10+ 本地 NTFS 客户端为实验性支持（原生验收尚未完成）**，使用系统句柄，不提供关闭路径保护的降级方式。

## Windows：手动安装与验证，不依赖 CI

在仓库根目录的 PowerShell 中执行，使用独立环境，不要求修改执行策略：

```powershell
py -3.12 -m venv .venv-artifact-sdk
.\.venv-artifact-sdk\Scripts\python.exe -m pip install -e "./packages/artifact-sdk[test,example]"
.\.venv-artifact-sdk\Scripts\python.exe -m pytest -c packages/artifact-sdk/pyproject.toml packages/artifact-sdk/tests packages/artifact-sdk/examples/test_mcp_server.py -q
```

Windows 原生测试不能用 macOS/Linux 的通过结果代替。原有符号链接测试需要创建符号链接的权限（例如已启用开发者模式）；不自动修改系统权限。新增目录联接测试不依赖符号链接特权。

- 传入绝对的本地盘符路径，例如 `C:/TCodeTasks/task-123/input.dat`；SDK 不解释远端主机路径，不接受 UNC、设备命名空间或网络映射盘。
- 当前仅准入固定磁盘上的 NTFS。拒绝重解析点（包含 junction／符号链接）、硬链接源文件、ADS、保留设备名、尾随点/空格、短名别名和含 `~` 的名称；这些限制是显式拒绝，不会偷偷重命名。
- Bundle 在创建目录前检查大小写别名和文件/目录前缀冲突。跨平台传输仍使用 `/` 分隔的相对清单路径。
- 新建文件/目录具有仅当前进程用户与 SYSTEM 访问的保护 DACL；既有目录权限不修改。仍应使用应用创建的私有任务目录，不与不可信进程共享写权限。
- 子项使用相对于父目录句柄的 `NtCreateFile`，拒绝重解析；祖先句柄保留且不共享删除。失败下载通过打开的文件句柄标记删除，不按可能被替换的名字删除文件。

配置真实服务后可手动启动文件回显示例：

```powershell
$env:TMCP_ARTIFACT_SERVICE_URL = 'https://artifact.example.org'
# 按部署方式注入 TMCP_ARTIFACT_API_TOKEN，不把真实令牌提交到仓库
.\.venv-artifact-sdk\Scripts\python.exe packages/artifact-sdk/examples/mcp_server.py
```

这里只支持远端 MCP 使用 SDK 进行文件传输，不代表 BenchThread Web 全功能、Job Protocol 服务或 Windows 安全沙箱已经完成适配。

## 安装与打包

在 BenchThread 仓库根目录执行，建议使用新的虚拟环境，不修改正在运行的宿主环境：

```sh
python3 -m venv .venv-artifact-sdk
source .venv-artifact-sdk/bin/activate
python -m pip install './packages/artifact-sdk[test,example]'
```

不需要示例／测试依赖时：`python -m pip install ./packages/artifact-sdk`。开发安装可加 `-e`。不要直接执行 `pip install tcode-artifact-sdk` 并假定 PyPI 有此包。

交付到第三方服务器：

```sh
python -m pip install build
python -m build packages/artifact-sdk
# 将生成的 wheel 交付到远端，在远端自己的虚拟环境安装
python -m pip install /path/to/tcode_artifact_sdk-0.1.0-py3-none-any.whl
```

SDK 的最小依赖只有 httpx；运行 MCP 示例还需安装 `fastmcp>=3.2,<4`。无需安装整个 BenchThread。复制示例到远端时一并交付 wheel；SDK 不要求远端访问 BenchThread 的源码路径。

## 启动 Artifact 服务端

先决定文件存在哪里，再选一种方式。下面的命令从仓库根目录运行；用 wheel 部署时，给 wheel 安装路径加上相同的 extras。

| 部署方式 | 在哪台机器启动 | BenchThread 里填什么 |
| --- | --- | --- |
| [和 MCP 同端口](#和-mcp-同端口启动) | 在 **MCP 主机**启动一个进程，同时提供 MCP 和 Artifact。BenchThread 不用另起文件服务。 | 「MCP 服务」填可达的 `/mcp` 地址；「文件服务」填同一主机、同一端口的 `/artifact` 地址，服务 ID 用启动时的值（示例 `echo-files`），绑定到刚添加的 MCP。 |
| [独立启动 Artifact](#仅启动-artifact) | 在 **BenchThread 后端和 MCP 都能访问的主机**运行 `tcode-artifact serve`。可以是任一侧主机或第三台。MCP 单独启动，并配置这个 Artifact 的 URL、服务 ID 和令牌。 | 「MCP 服务」填实际 MCP 地址；「文件服务」填独立 Artifact 的基础地址和服务 ID（示例 `shared-files`）。要给 MCP 传文件就绑定它；只在聊天里使用文件服务则不添加、不绑定 MCP。 |
| [复用已有 Artifact](#复用已有-artifact-服务) | **不启动新的 Artifact。** 在 MCP 主机按原方式启动 MCP，把 SDK 指向已有文件服务。 | 「MCP 服务」填实际 MCP 地址；「文件服务」填已有 Artifact 的实际基础地址和服务 ID，绑定到对应 MCP。若该文件服务已登记，直接复用现有配置，不另建同 ID 的条目。 |

### 和 MCP 同端口启动

```sh
python -m pip install './packages/artifact-sdk[server,example]'
# 先为进程注入 TMCP_ARTIFACT_API_TOKEN（至少 24 个可打印非空格 ASCII 字符的随机密钥）
python packages/artifact-sdk/examples/mcp_server.py --with-artifact --port 18080 \
  --data-dir ./artifact-data --service-id echo-files
```

这条命令仅监听环回：本机测试地址为 `http://127.0.0.1:18080/mcp` 和 `http://127.0.0.1:18080/artifact`。**即使 Web 与 MCP 同机，也不要把这个 MCP 环回地址填进普通 Web 的 MCP 表单。** 接入 Web 时，按 [快速开始](#echo_file-快速开始)让示例监听可达的私网地址，或通过带认证的 HTTPS 反向代理提供 `/mcp` 和 `/artifact`。两者同端口不代表共用凭据：文件请求使用 `X-Artifact-Token`，MCP 的认证独立设置。开发自己的 FastMCP 服务时，可以保留工具函数，只替换启动入口：

```python
from tcode_artifacts.server import run_with_artifact

run_with_artifact(mcp, port=18080, data_dir="./artifact-data", service_id="echo-files")
```

入口默认只监听环回地址。若已设置非空 `TMCP_ARTIFACT_SERVICE_URL`，或配置了 `TMCP_ARTIFACT_SERVICES` 和默认服务 ID，只启动 MCP，沿用 SDK 的外部配置；不会创建本地存储或在连接失败后切换。`run_with_artifact(..., token=...)` 只接受内置服务的令牌；复用外部服务时传这个参数会明确报错。外部凭据通过 `TMCP_ARTIFACT_API_TOKEN` 或服务映射的 `token_env` 设置。

### 仅启动 Artifact

不需要安装 FastMCP，也不需要写 Python 启动脚本：

```sh
python -m pip install './packages/artifact-sdk[server]'
# 同样先注入 TMCP_ARTIFACT_API_TOKEN
tcode-artifact serve --port 18081 --data-dir ./artifact-data --service-id shared-files
```

默认基础地址为 `http://127.0.0.1:18081/artifact`，仅同主机、同网络环境的客户端能访问。跨主机时给 `tcode-artifact serve` 加 `--host <文件主机的私网IP>`（或让服务保持环回监听，由同机 HTTPS 反向代理转发），并使用调用方真正可达的地址。MCP 进程按[服务配置](#服务配置)设置 Artifact 地址、相同的服务 ID 和文件服务令牌，再启动 MCP。也可在自己的 Python 入口调用 `run_artifact(data_dir=..., service_id=..., port=...)`。两种方式复用同一份服务端和存储格式，但**不要用两个进程同时对外提供同一个数据目录**。

### 配置 BenchThread 和验证

1. 打开 **设置 → 文件服务**（部分内置 Web 版本显示为 **设置 → 远端服务**），点「添加服务」。在「服务地址」填 Artifact 的基础地址（SDK 服务端以 `/artifact` 结尾，不带 `/health`），填入实际服务 ID。「显示名称」是给用户看的，不代替服务 ID。普通 Web 的 **MCP 地址**不接受 `127.0.0.1` 或 `localhost`；**Artifact 地址**只有在 Web 后端和它共用同一网络环境时才可填环回地址。浏览器能打开的地址不一定对 Web 后端可达。跨主机部署请用各端都可达的地址，公开部署用 HTTPS 和独立的 MCP 认证，不要把未认证的本地回显示例直接暴露到公网。
2. 「凭据环境变量名」填 **BenchThread 后端进程**里的变量名，例如 `ECHO_ARTIFACT_TOKEN`，不填令牌原文。把真实的文件服务令牌注入后端的该变量；不要用 MCP 连接令牌。仅在 MCP 进程中设置，Web 后端不会自动拿到它。若用环境变量代替 Web 页面配置，BenchThread 后端和 MCP 都按[服务配置](#服务配置)提供 URL、服务 ID 和所需凭据，服务 ID 都应与服务端一致。
3. **绑定**指定某个 MCP 使用哪一个文件服务，填写「MCP 服务」中登记的服务名称，例如 `sdk-echo`。**默认文件服务**供没单独绑定的 MCP 使用。Web 表单需要保留一个默认服务；给新 MCP 做绑定时，不用把它的文件服务改成全局默认。若只在聊天里浏览、上传和下载，绑定留空，并按 [不绑定 MCP 使用 Artifact](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)配置后端授权。
4. 点 **保存 Artifact 配置**，用同一个运行目录**重启 Web**，再回来点该服务的 **测试连接**。不用重启 MCP；重启前测试会提示配置尚未生效。如果只显示「可达」，说明尚未验证凭据。连接测试不代表 MCP 已经能完成文件往返。

首次启动可请求 `/artifact/health`（也需要文件令牌）确认服务 ID。两种启动方式都提供单文件和目录包上传；单文件可直接用 `ArtifactIO().upload_file("./sample.txt")`，也可向 Artifact 基础地址下的 `POST /api/v1/artifacts` 提交带令牌的 multipart `file` 字段，得到 `ArtifactRef`。完整验证要把一个无敏感内容的小文件交给 `echo_file`，取回 `echo.dat` 并核对内容。重启时保留同一数据目录、服务 ID 和访问配置，再用原引用下载，无需重跑工具。

内置服务面向**同一信任域**：共享令牌不是多租户隔离。使用服务账号专用的私有数据目录（POSIX 权限 `0700`），备份整个目录。默认单文件 64 MiB，存储内容总量 1 GiB，可用 `--max-file-bytes`／`--max-store-bytes` 调整独立服务；SDK 客户端和部署代理仍有各自的限额。首版不开放宿主路径注册和压缩包自动解包，也不自动删除旧产物；草稿包也占空间，需按使用量规划存储。

### 可信文件包写入

BenchThread Web 代已登录用户上传目录时，现有接口会发送实际工作区和所有者。给 Artifact 服务端设置独立的 `TMCP_ARTIFACT_PROXY_TOKEN`，值必须与普通 `TMCP_ARTIFACT_API_TOKEN` 不同。只在受信任的 BenchThread 后端注入这份代理令牌，并在 Artifact 配置中把该服务的「凭据环境变量名」指向它；若 Web 使用单地址环境变量配置，则把代理令牌注入 **Web 进程自己的** `TMCP_ARTIFACT_API_TOKEN`，不要改 Artifact 服务端或 MCP 的普通令牌。

仅可信代理可以声明所有者；创建时绑定的工作区、所有者必须在追加成员或完成文件包时完全一致。未配置代理令牌时，带所有者的请求会被拒绝，而不是降级成无归属写入。MCP 的普通 SDK 上传和已有引用下载不变。旧文件包不会被自动认领；若旧的 Web 目录上传还没完成，请在配置代理后重新发起那次**文件上传**。此机制只保护文件包写入，不提供文件读取、目录浏览或全套多租户隔离。若 MCP 代码不在同一信任域，应独立部署 Artifact，不把代理令牌放进 MCP 进程。

## 服务配置

```sh
export TMCP_ARTIFACT_SERVICE_URL='https://artifact.example.org/artifact'
export TMCP_ARTIFACT_SERVICE_ID='echo-files'  # 改成服务端 --service-id 或 /health 返回的 service_id
# 只在实际部署时设置真实访问令牌，勿放进参数或文件引用
export TMCP_ARTIFACT_API_TOKEN='your-artifact-access-token'
```

双方必须能访问同一个 Artifact 服务。远端的 `127.0.0.1` 不是用户电脑；客户端不会自动启动服务、转发端口或把客户端路径当作远端路径。公网部署需要 HTTPS、鉴权和服务端配额。Artifact 凭据与 MCP 连接 Token 是两种不同凭据。

多服务配置与现有 TMCP 命名一致：

```sh
export TMCP_ARTIFACT_SERVICE_ID='primary'
export BACKUP_ARTIFACT_TOKEN='your-second-service-token'
export TMCP_ARTIFACT_SERVICES='{"backup":{"url":"https://backup.example.org","token_env":"BACKUP_ARTIFACT_TOKEN"}}'
```

带 `service_id=backup` 的输入只使用本地 allowlist 中 backup 的地址和令牌，未知 ID 立即失败，不回退到默认服务；引用内的 URL 不参与路由。没有 service_id 的旧引用沿用默认服务。默认输出归属由构造函数／环境配置确定，不会因读取另一个服务的输入而悄悄改变。SDK 不读取宿主的 target-server 路由设置，因为它不负责调度目标。

## 文件与目录 API

```python
from tcode_artifacts import ArtifactIO

io = ArtifactIO()  # 也可显式提供 base_url、token、service_id、services
input_path = io.materialize(
    {"kind": "artifact", "artifact_id": "art_example"},
    "/private/task-123/input.dat",
)
# 你的业务程序使用 input_path，生成 /private/task-123/output 下的文件
output_ref = io.upload_directory("/private/task-123/output")
result = {"status": "completed", "output_bundle": output_ref}
```

引用中的 ID 必须来自真实上传结果，上面的 `art_example` 只是结构说明。

- `materialize(ref, destination)`：接收 `kind=artifact` 或 `kind=artifact_bundle`，可带 `service_id`；返回本地 `Path`。
- `download_file(artifact_id, destination)`：默认服务上的单文件读取，兼容原示例入口。
- `upload_file(source, workspace_id="third-party")`：返回 ArtifactRef。
- `upload_directory(source, workspace_id="third-party")`：上传并完成目录包，返回 BundleRef；只有服务确认 ready 才返回成功。
- 默认总字节上限 500 MiB、文件数 10,000，可使用 `max_bytes`／`max_files` 构造参数调整。原示例的 `EXAMPLE_MAX_BYTES`／`EXAMPLE_MAX_FILES` 环境变量仍兼容；服务端及 BenchThread 宿主另有独立限制，不能只改 SDK 就视为全链路放宽。

输入文件不能覆盖现有文件，目录接收使用不存在的新目录；下载核对实际字节数及 SHA-256，并核对目录清单与文件元数据。拒绝目录穿越、符号链接和非普通输出文件。为每个任务分配私有任务目录，输出完成后再上传；不要在传输期间并发修改源文件或将任务目录交给不可信进程写入。同步 API 可用于同步 MCP 工具；不要直接放进异步事件循环的长阻塞代码中。

HTTP／校验错误会抛异常，不返回虚假成功、不跟随重定向、不自动重试 POST／PUT。失败的目录下载可能保留已校验的部分文件，失败的上传可能保留远端草稿包；应记录失败并在新的私有目录重试文件操作，不能把残留目录当作完整结果。不要因此重新提交状态不明的科学计算。

## echo_file 快速开始

这个示例把一份小文件原样回传为 `echo.dat`。准备一个**带登录、已配置对话模型的 Web**，并启用 [Job Protocol](../../docs/RUNNING.md#远程异步计算)。自定义文件字段通过异步入口登记；`tcode-server --local` 不会启动 Job Protocol，不能用于这次 Web 验证。无需 Docker 沙箱或 TMCP。

### 1. 启动示例

从仓库根目录运行下面的命令。只在隔离的测试网络上使用合成文件；把 `192.168.1.20` 改成 **运行 MCP 的机器实际持有、且 Web 后端可以访问的私网 IP**。这是监听地址，不是 Web 的地址。测试令牌由安装者生成，不要提交到仓库或发送给 Agent。

```bash
source .venv/bin/activate
python -m pip install './packages/artifact-sdk[server,example]'
export ECHO_ARTIFACT_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export ARTIFACT_DEMO_HOST='192.168.1.20'

env -u TMCP_ARTIFACT_SERVICE_URL -u TMCP_ARTIFACT_SERVICES -u TMCP_ARTIFACT_SERVICE_ID \
  TMCP_ARTIFACT_API_TOKEN="$ECHO_ARTIFACT_TOKEN" python - <<'PY' &
import os
import runpy
from tcode_artifacts.server import run_with_artifact

mcp = runpy.run_path("packages/artifact-sdk/examples/mcp_server.py")["mcp"]
run_with_artifact(mcp, host=os.environ["ARTIFACT_DEMO_HOST"], port=18080,
                  data_dir="./artifact-data", service_id="echo-files")
PY
ECHO_MCP_PID=$!
```

Web 的 MCP 表单不接受 `127.0.0.1` 或 `localhost`，即使 MCP 和 Web 在同一台机器也一样。已有 HTTPS 反向代理时，也可以继续用[环回启动命令](#和-mcp-同端口启动)，由代理对 Web 提供可达的 HTTPS 地址，并保留 `/mcp`、`/artifact` 两条路径。不要把这个未启用 MCP 认证的测试示例直接暴露到公网。

如果 Web 还没开 Job Protocol，按[运行指南](../../docs/RUNNING.md#远程异步计算)准备依赖。以下示例假设你在同一台机器启动本次试验的 Web：在同一个终端启动，使 Web 进程继承刚才的 `ECHO_ARTIFACT_TOKEN`。如果复用正在运行的 Web，在它的部署环境注入令牌后重启，不要再起一个使用同一运行目录的实例。两端不在同一台机器时，分别注入同一个测试令牌。

```bash
TCODE_MANAGED_JOB_PROTOCOL=1 PYTHON_BIN="$PWD/.venv/bin/python" \
  ./scripts/start_web.sh --host 127.0.0.1 --port 8080 --workdir "$PWD/workdirs/local"
```

### 2. 在 Web 登记 MCP 和文件服务

1. 登录 Web，先选择或新建工作区，例如 `artifact-demo`。文件传输声明按工作区保存；后续在同一工作区测试。
2. 打开 **设置 → MCP 服务 → 添加常规 MCP**。服务名称填 `sdk-echo`，MCP 地址填 `http://192.168.1.20:18080/mcp`（替换 IP），传输方式选 **Streamable HTTP**，调用方式选 **异步执行（通过 Job Protocol）**。
3. 勾选 **为自定义异步 MCP 配置文件字段**，远端接收方式选 **已支持产物引用**。点击 **发现工具与参数（不提交计算）**，展开 `echo_file`，勾选 **启用此工具的异步接入**，把 `input_file` 设为 **文件 / 文件列表**，点 **保存并连接**。保存后应看到 **已注册文件传输**。
4. 打开 **设置 → 文件服务**（也可能显示为 **设置 → 远端服务**），点「添加服务」。服务 ID 填 `echo-files`，显示名称可填「Echo 文件服务」，服务地址填 `http://192.168.1.20:18080/artifact`（替换 IP），凭据环境变量名填 `ECHO_ARTIFACT_TOKEN`（**不是令牌本身**），绑定的 MCP 名称填 `sdk-echo`。如果还没有默认服务，就选它为默认；已有默认服务可以保留。点 **保存 Artifact 配置**，用同一个运行目录**重启 Web**（无需重启 MCP），再回到该页面点 **测试连接**，应显示「连接正常」。只有下一步取回文件才算跑通。

### 3. 上传附件并取回 echo.dat

1. 在本地创建一个无敏感内容的 `artifact-echo-test.txt`，例如只写一行 `BenchThread Artifact echo tutorial sample.`。
2. 回到 **同一工作区**，新建任务，用聊天输入框的 **上传附件** 选中该文件。发送：「请通过 Job Protocol 异步提交 `sdk-echo` 的 `echo_file`，原样回传附件 `artifact-echo-test.txt`。」异步目标由 Job Protocol 调用，不会作为直连 MCP 工具出现在 Agent 的工具列表。如果界面询问文件读取或任务执行权限，确认来源和参数后仅批准这一次。
3. 受理编号不代表完成。等原会话的异步结果回传，再到当前工作区的文件列表，在 `artifacts/jobs/` 下打开或下载 `echo.dat`。内容应与输入完全相同。尚未回传时先查看该任务状态，不要重复提交。

本次上传的是单文件附件，不是 Web 目录上传，因此只用普通文件服务令牌，不需要配置[可信文件包写入](#可信文件包写入)。测试后可先停止这次启动的 Web，再在原终端运行 `kill "$ECHO_MCP_PID"` 停止示例；保留数据目录和服务 ID 可继续读取原来的结果。

### 复用已有 Artifact 服务

```sh
python packages/artifact-sdk/examples/mcp_server.py
```

默认仅监听 `127.0.0.1:10240/mcp`，暴露 `echo_file(input_file: dict)`；仅复制合成输入并上传 `echo.dat`，不运行蛋白或 GPU 计算。公开部署之前自行配置 MCP 认证／TLS，不直接把未认证示例暴露到公网。

在 **MCP 主机**启动前，先按[服务配置](#服务配置)注入已有文件服务的地址、ID 和令牌。上面的命令只供环回测试；接入 Web 时，使用可达的 MCP 地址（例如由同机 HTTPS 反向代理转发 `/mcp`，并配置 MCP 认证）。参考[快速开始的 Web 登记步骤](#2-在-web-登记-mcp-和文件服务)，把示例地址和 ID 替换成实际值，再绑定已有 Artifact；不要照抄 `echo-files`。SDK 不会启动第二套文件服务，连接失败也不会自动切换。

这个最小工具是 **blocking** 接入：桌面把它作为已有异步任务执行，但 SDK 不会让远端同步函数自动获得幂等／断线续跑。需要提交幂等性、status/result/cancel 和远端恢复时，复用仓库 `examples/third_party_mcp/remote_server.py` 与 `async-plugin/async-targets.json` 的 remote-job v1 示例。该示例的 `artifact_io.py` 已转为本 SDK 的兼容导入，不另维护第二份传输实现；任务状态机仍留在原示例／宿主。

从远端进程部署到 BenchThread 目标登记的完整步骤见 [第三方异步 MCP 接入与运行](../../docs/服务隔离/第三方MCP插件接入与运行.md)。

## 契约测试

```sh
python -m pytest -c packages/artifact-sdk/pyproject.toml packages/artifact-sdk/tests -q
# 安装 example 依赖后：真实 FastMCP in-process 工具发现及回显
python -m pytest -c packages/artifact-sdk/pyproject.toml packages/artifact-sdk/examples/test_mcp_server.py -q
# 安装 [server,example,test] 后：真实 HTTP、双启动模式、文件往返及重启恢复
python -m pytest -c packages/artifact-sdk/pyproject.toml packages/artifact-sdk/examples/test_artifact_service.py -q
```

测试使用合成数据、MockTransport 和临时回环 HTTP 服务，不连接正式服务，不提交专业计算。包内测试不依赖 BenchThread／TMCP，验证现有 API 路径、multipart、文件／目录往返、服务凭据隔离、无自动重试／重定向及文件安全边界。宿主既有文件传输、审批和异步恢复测试仍单独运行；契约测试不等价于任意真实服务器的鉴权和业务验收。
