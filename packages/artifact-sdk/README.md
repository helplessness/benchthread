# BenchThread Artifact Python SDK

同仓库维护、可独立安装的文件客户端，可选安装 Artifact 服务端。发行包名 `tcode-artifact-sdk`，导入名 `tcode_artifacts`。当前版本 0.1.0，仅提供源码／wheel 安装，**尚未发布到 PyPI**。

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

已有兼容服务时不用安装或启动新的服务端。没有服务时，在 **MCP 所在的机器**或双方都能访问的独立主机上安装额外依赖。下面的命令从仓库根目录运行；用 wheel 部署时，给 wheel 安装路径加上相同的 extras。

### 和 MCP 同端口启动

```sh
python -m pip install './packages/artifact-sdk[server,example]'
# 先为进程注入 TMCP_ARTIFACT_API_TOKEN（至少 24 个可打印非空格 ASCII 字符的随机密钥）
python packages/artifact-sdk/examples/mcp_server.py --with-artifact --port 18080 \
  --data-dir ./artifact-data --service-id echo-files
```

同机时，MCP 地址是 `http://127.0.0.1:18080/mcp`，Artifact 基础地址是 `http://127.0.0.1:18080/artifact`。两者共用一个进程和端口，文件请求仍需要自己的 `X-Artifact-Token`，MCP 的认证设置保持独立。开发自己的 FastMCP 服务时，可以保留工具函数，只替换启动入口：

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

基础地址为 `http://127.0.0.1:18081/artifact`。MCP 进程设置该地址、相同的 `TMCP_ARTIFACT_SERVICE_ID` 和文件服务令牌，仍按原方式启动。也可在自己的 Python 入口调用 `run_artifact(data_dir=..., service_id=..., port=...)`。两种方式复用同一份服务端和存储格式，但**不要用两个进程同时对外提供同一个数据目录**。

### 配置 BenchThread 和验证

若只在 BenchThread 聊天中浏览、上传和下载，可将「MCP 绑定」留空。完整的独立启动、后端授权和聊天示例见 [不绑定 MCP 使用 Artifact](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)。

在 BenchThread「设置 → 远端服务 → Artifact 文件服务」填基础地址和相同的服务 ID，凭据字段填已注入到 BenchThread 后端的**环境变量名**。可把它设为默认，或绑定到已添加的 MCP。Web 保存后重启生效。用环境变量代替页面配置时，BenchThread 后端和 MCP 都要配 `TMCP_ARTIFACT_SERVICE_ID`，值须与服务端的 ID 一致，不能只填 URL 和令牌。跨主机使用两端都可达的 HTTPS 地址，不用 `127.0.0.1`；通过反向代理部署时保留 `/mcp` 和 `/artifact` 路径，另外配置 MCP 认证，不要直接暴露不带 MCP 认证的本地回显示例。

首次启动可请求 `/artifact/health`（也需要文件令牌）确认服务 ID。两种启动方式都提供单文件和目录包上传；单文件可直接用 `ArtifactIO().upload_file("./sample.txt")`，也可向 Artifact 基础地址下的 `POST /api/v1/artifacts` 提交带令牌的 multipart `file` 字段，得到 `ArtifactRef`。完整验证要把一个无敏感内容的小文件交给 `echo_file`，取回 `echo.dat` 并核对内容。重启时保留同一数据目录、服务 ID 和访问配置，再用原引用下载，无需重跑工具。

内置服务面向**同一信任域**：共享令牌不是多租户隔离。使用服务账号专用的私有数据目录（POSIX 权限 `0700`），备份整个目录。默认单文件 64 MiB，存储内容总量 1 GiB，可用 `--max-file-bytes`／`--max-store-bytes` 调整独立服务；SDK 客户端和部署代理仍有各自的限额。首版不开放宿主路径注册和压缩包自动解包，也不自动删除旧产物；草稿包也占空间，需按使用量规划存储。

### 可信文件包写入

BenchThread Web 代已登录用户上传目录时，现有接口会发送实际工作区和所有者。给 Artifact 服务端设置独立的 `TMCP_ARTIFACT_PROXY_TOKEN`，值必须与普通 `TMCP_ARTIFACT_API_TOKEN` 不同。只在受信任的 BenchThread 后端注入这份代理令牌，并在 Artifact 配置中把该服务的「凭据环境变量名」指向它；若 Web 使用单地址环境变量配置，则把代理令牌注入 **Web 进程自己的** `TMCP_ARTIFACT_API_TOKEN`，不要改 Artifact 服务端或 MCP 的普通令牌。

仅可信代理可以声明所有者；创建时绑定的工作区、所有者必须在追加成员或完成文件包时完全一致。未配置代理令牌时，带所有者的请求会被拒绝，而不是降级成无归属写入。MCP 的普通 SDK 上传和已有引用下载不变。旧文件包不会被自动认领；若旧的 Web 目录上传还没完成，请在配置代理后重新发起那次**文件上传**。此机制只保护文件包写入，不提供文件读取、目录浏览或全套多租户隔离。若 MCP 代码不在同一信任域，应独立部署 Artifact，不把代理令牌放进 MCP 进程。

## 服务配置

```sh
export TMCP_ARTIFACT_SERVICE_URL='https://artifact.example.org'
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

## 最小 MCP 示例

```sh
python packages/artifact-sdk/examples/mcp_server.py
```

默认仅监听 `127.0.0.1:10240/mcp`，暴露 `echo_file(input_file: dict)`；仅复制合成输入并上传 `echo.dat`，不运行蛋白或 GPU 计算。公开部署之前自行配置 MCP 认证／TLS，不直接把未认证示例暴露到公网。

在 BenchThread 中使用已有“设置 → MCP 服务 → 异步执行 → 输入文件传输”：发现 echo_file 并将 input_file 声明为文件字段，远端接收方式选“已支持产物引用”。BenchThread 负责读取授权、上传、队列和结果回传；用户批准后仅提交一次，收到受理编号后等待通知，不由示例自行轮询或重新提交。

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
