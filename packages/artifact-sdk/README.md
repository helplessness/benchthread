# BenchThread Artifact Python SDK

同仓库维护、可独立安装的文件客户端，可选安装 Artifact 服务端。发行包名 `tcode-artifact-sdk`，导入名 `tcode_artifacts`。当前版本 0.1.0，仅提供源码／wheel 安装，**尚未发布到 PyPI**。

[最小 FastMCP 示例代码](#最小-fastmcp-示例) · [第一次让 MCP 收发文件：`echo_file` 快速开始](#echo_file-快速开始) · [只在聊天中使用文件服务，不接 MCP](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)

基础客户端只依赖 `httpx`；安装基础包或导入 `ArtifactIO` 不会启动服务，也不导入 `tcode_agent` 或 `tmcp`。客户端和可选服务端沿用 Artifact HTTP v1 接口和 `ArtifactRef`／`BundleRef`，不实现计算、队列、审核或轮询。Python 3.10+。客户端在 Linux／macOS 保留安全目录描述符实现；**Windows 10+ 本地 NTFS 客户端为实验性支持（原生验收尚未完成）**，使用系统句柄，不提供关闭路径保护的降级方式。

## 一份服务供多人使用：每人一个 Token

无需公共登录服务器。下面从仓库根目录、在自己的 Python 虚拟环境中执行。服务进程与管理命令必须使用**同一私有持久数据目录**；不要使用共享盘上的公共可写目录。

```sh
python -m pip install -e './packages/artifact-sdk[server,example]'
mkdir -m 700 ./personal-service-data
tcode-artifact users issue alice --data-dir ./personal-service-data
tcode-artifact users issue bob --data-dir ./personal-service-data
```

签发命令只显示一次新 Token，请通过私密渠道交给对应用户，不要贴进聊天、日志或 Git。任选一种启动方式：

| 服务形态 | 启动命令（示例默认仅监听本机） | 客户端入口 |
| --- | --- | --- |
| 纯文件服务 | `tcode-artifact serve --auth personal-tokens --service-id lab --data-dir ./personal-service-data` | `http://127.0.0.1:18081/artifact` |
| 仅 MCP | `python packages/artifact-sdk/examples/mcp_server.py --auth personal-tokens --data-dir ./personal-service-data` | `http://127.0.0.1:10240/mcp`，使用文字工具 echo_text，无文件路由 |
| MCP 与文件服务一体 | `python packages/artifact-sdk/examples/mcp_server.py --auth personal-tokens --with-artifact --service-id lab --data-dir ./personal-service-data` | 同一主机端口上的 `/mcp` 和 `/artifact` |

运行过程中可在另一个终端添加用户、列出凭据状态、换发或撤销，无需重启：

```sh
tcode-artifact users list --data-dir ./personal-service-data
tcode-artifact users rotate alice --data-dir ./personal-service-data
tcode-artifact users revoke bob --data-dir ./personal-service-data
```

轮换保留用户编号、文件和历史任务；撤销阻止后续认证，不回收已下载内容，也不删除文件。不设置个人令牌模式时，原来的共享凭据模式不变。旧共享数据不会自动展示给新用户。

### 只读共享已有目录（不需要 MCP 或重新上传）

例如管理员要把服务器上已有的 `/srv/lab/published` 给 Alice 和 Bob 看：在 Linux/macOS 文件主机上运行下面的命令。可复用上面已签发个人令牌的凭据目录；名单填写**文件服务的账号名**，不是显示名称。共享区使用独立端口和服务 ID，原个人服务继续照常运行。

```sh
tcode-artifact serve --auth personal-tokens --service-id lab-library --port 18082 \
  --data-dir ./personal-service-data --shared-dir /srv/lab/published \
  --reader alice --reader bob
```

**多人使用时**，把最后一行换成 `--readers-file ./lab-readers.txt`。文件用 UTF-8，每行一个准确的账号名，可留空行，不写注释或令牌，最大 1 MiB。名单放在共享目录以外，由管理员维护；用同目录临时文件编辑后再重命名替换，避免读到保存一半的名单。增删用户从**下一个请求**生效，无需重启，不会撤销个人令牌、已完成的下载或正在传输的响应。

原来的 `--reader` 仍可单用或与文件合用。合用时取并集：文件删掉的人如果仍在固定参数中，仍有权限。**名单丢失、不可读或格式损坏时，所有共享区请求都被拒绝，包括固定读者**；恢复有效名单后自动恢复。空名单是有效的，仅保留显式固定读者。只想用文件统一撤权，就不要额外传 `--reader`。

**批量签发新用户令牌（可选）**：已有账号直接加入读者名单即可。首次创建一批新账号时，可在不对外共享的管理员目录中执行下面的 Bash 示例。`new-users.txt` 只放新账号；如果所有人都是新用户，可以直接使用同一份 `lab-readers.txt`。示例为每人保存一个独立文件，为避免把用户名解释成路径，仅接受字母、数字、下划线和连字符开头或组成的简单账号（中间也可有点）。

```bash
(
  set -eu
  umask 077
  out=$(mktemp -d ./issued-tokens.XXXXXXXX)
  printf '私密交付目录：%s\n' "$out"
  while IFS= read -r name || [[ -n "$name" ]]; do
    name=${name%$'\r'}
    [[ -n "$name" ]] || continue
    [[ "$name" =~ ^[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,127}$ ]] || { echo '账号格式不适用，停止签发' >&2; exit 1; }
    [[ ! -e "$out/$name.token" ]] || { echo '交付文件重名，停止签发' >&2; exit 1; }
    token=$(tcode-artifact users issue --data-dir ./personal-service-data -- "$name")
    printf '%s\n' "$token" > "$out/$name.token"
  done < new-users.txt
)
```

通过私密渠道分别交付令牌，不要开启 shell 跟踪（`set -x`）、提交到 Git 或把交付目录放进共享区。如果遇到已存在的账号，签发会停止，不会轮换其令牌；之前成功生成的文件仍在交付目录。核对剩余新用户名后再继续。

默认只监听本机。跨主机建议通过 HTTPS 反向代理开放 `/artifact`。只指定专门用于发布的目录，不要指向家目录、配置目录或任务存储；只有可信管理员可以在本机写入它。不展示隐藏文件、符号链接、硬链接、特殊文件和超出单文件限额的文件。文件由管理员在原目录更新；旧引用不会下载到新版本，重新浏览即可。撤销个人令牌立即阻止后续请求；只想取消共享区资格而保留个人文件权限时，从启动参数去掉对应的 `--reader` 后重启共享服务。

BenchThread 中把这个端点单独添加为「实验室共享资料」，**不绑定 MCP，不用作任务输出或默认上传服务**。单用户使用自己的读者令牌；多人 Web 后端可为共享区单独签发一个服务账号，将它加入 `--reader`，仅后端保存令牌，再按[文件服务授权步骤](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)用 `TCODE_ARTIFACT_IMPORT_ACCESS` 限定哪些租户和用户可以读取；不要授予上传权限。例如在聊天中说「从实验室共享资料列出 results 目录，再下载 report.csv 到当前工作区」。共享服务本身也拒绝写入，即使是名单内的读者也不能覆盖服务器上的文件。

**接入其他 Harness：** 远程 MCP 使用 `Authorization: Bearer <个人令牌>`；文件 HTTP 接口支持相同的 Bearer，也兼容 SDK 的 `X-Artifact-Token`。无文件工具不用安装客户端 SDK。有文件工具先上传取得引用、调用 MCP、再按引用下载。列根目录请求 `GET /artifact/api/v1/files`；按列表返回的 artifact_id 下载 `GET /artifact/api/v1/artifacts/{artifact_id}/content`。客户端不要把令牌转发给引用中出现的其他主机。跨主机部署使用 HTTPS。

**第三方作者：** 用 `run_personal_mcp(mcp, data_dir=..., files=True, service_id="lab")` 启动；同步工具内部继续使用 `ArtifactIO()`，自动选择经过认证的当前请求，不必自己传 owner_id。持久任务必须另外记录归属、校验查询和取消；SDK 不会自动保护自定义的 HTTP 接口或业务数据库。可参考[异步示例](../../examples/third_party_mcp/remote_server.py)：设置 `EXAMPLE_AUTH_MODE=personal-tokens`，用 `EXAMPLE_DATA_ROOT` 指向签发凭据的目录；个人模式下，示例用 ArtifactIO.delegate_job 为后台 worker 授权指定输入和一个输出包。用户断开后 worker 仍会发布文件；重启会继续处理尚未开始的任务，不会盲目重传已中断的发布。领取结果只读已持久化的引用，不启动计算。取消排队任务或发布结束后回收任务凭据。

### 本机自测个人 MCP

先按上表签发个人令牌，启动「MCP 与文件服务一体」示例（服务 ID 为 `lab`）。另开终端，激活安装了 SDK 的虚拟环境，运行以下代码。它会隐藏令牌输入，上传临时文件、通过 MCP 调用 `echo_file`，再用 Artifact 下载并逐字节核对。**无需启动 BenchThread，也不需要 Job Protocol。**

```sh
python - <<'PY'
import asyncio, getpass, tempfile
from pathlib import Path
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from tcode_artifacts import ArtifactIO

async def main():
    token = getpass.getpass("个人令牌: ")
    base = "http://127.0.0.1:10240"
    files = ArtifactIO(base + "/artifact", token=token, service_id="lab")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        src = root / "sample.txt"
        src.write_bytes(b"BenchThread file echo\n")
        ref = files.upload_file(src)
        async with Client(StreamableHttpTransport(base + "/mcp", headers={"Authorization": "Bearer " + token})) as mcp:
            reply = await mcp.call_tool("echo_file", {"input_file": ref})
        out = files.materialize(reply.structured_content["output_bundle"], root / "received")
        assert (out / "echo.dat").read_bytes() == src.read_bytes()
        print("echo_file 文件往返通过")

asyncio.run(main())
PY
```

### 桌面 HTTPS 接入

默认桌面不接受上面的环回 MCP 地址。让提供者在文件服务主机配置 HTTPS 反向代理：例如将 `https://lab.example.org/mcp` 和 `https://lab.example.org/artifact/…` 原路径转发至同机 `127.0.0.1:10240`，保留认证头并支持 MCP 流式响应。把示例域名换成桌面后端可达、证书受信任的真实域名。

在 **设置 → MCP 服务 → 添加常规 MCP** 填服务名称和上述 HTTPS **/mcp** 地址，勾选 **使用此 MCP 自带的个人文件服务**，填写提供者签发的个人令牌（只填令牌值），点 **保存并连接**。桌面会用同一凭据验证同源的 `/artifact/health`；不用再手工添加 Artifact，也不会覆盖已登记的同 ID 服务。可在聊天里按服务名称列目录，并选一个自己的文件下载到工作区。

桌面的个人绑定只属于保存时的账号和租户。切换身份后不能连接或沿用这份凭据；切回原身份，在自己的工作区即可继续使用。原用户取消勾选、改为普通 MCP 时，不会自动把旧个人认证头变成共享凭据；如需认证，请重新填写。

### Web 手动配置

**多人 Web：每位登录用户使用自己的文件空间。** 只需管理员登记一次端点，不需要给每个人部署一份服务，也不需要 BenchThread 签发令牌。

1. **文件服务管理员**：用本仓库新版 SDK 按上面的个人模式启动文件服务（独立或与 MCP 同端口皆可），向每人私下发放各自的个人令牌。新版的已认证健康接口会返回稳定的账号标识，旧版需要升级。
2. **Web 管理员**：启用需要登录的 Web（不要用免登录的 `--local`）。在工作区和代码仓库之外放一份持久化的绑定加密密钥；这不是用户令牌。下面是 Linux/macOS 示例（只在首次执行生成步骤，不覆盖已有密钥）：

   ```bash
   install -d -m 700 "$HOME/.config/benchthread-secrets"
   export TCODE_ARTIFACT_BINDING_KEY_FILE="$HOME/.config/benchthread-secrets/artifact-binding.key"
   (umask 077; set -C; openssl rand -base64 32 > "$TCODE_ARTIFACT_BINDING_KEY_FILE")
   ```

   给 Web 服务进程配置该环境变量、挂载同一密钥文件并限制读取权限。Windows 可将同样格式的密钥文件放在仅 Web 服务账号可读的位置。重启时**不要重新生成**；与认证数据库分开妥善备份，丢失密钥需重新绑定。浏览器到 Web、Web 到远端 Artifact 都应走 HTTPS；仅同机开发使用环回 HTTP。
3. **Web 管理员**：在 **设置 → Artifact 文件服务 → 添加服务** 填服务实际 ID 和地址（例如 `lab`、`https://lab.example.org/artifact`），勾选 **按 Web 登录账号绑定个人令牌**。共享凭据环境变量和 MCP 绑定均留空。已有共享服务时用独立的服务 ID，可保留原默认服务。保存全站配置并重启 Web。
4. **每位用户**：登录自己的 Web 账号，在同一设置页找到该服务，在 **我的文件服务 → 个人令牌** 填发行方提供的令牌，点 **验证并绑定**。绑定即时生效；不需要管理员权限，也不必再重启。可在聊天中指定服务名称浏览、导入或上传文件。浏览器目录附件使用所选服务（未指定时使用部署默认服务）。不同账号即使上传同名文件，也只看到自己的内容。

个人文件服务可以用于同步上传。普通上传的默认服务可以留空。通过 Job Protocol 传文件时，在共享服务的「绑定的 MCP 名称」里填写具体的目标 MCP 名称。管理员移除或更换服务 ID 后，可以在「已失效的个人绑定」解除旧绑定，再绑定新服务。

令牌轮换后使用 **验证并更新令牌**；**解除我的绑定** 只取消本账号在 Web 的访问，不删除远端文件。服务拒绝凭据时不会改用其他人的令牌或共享令牌。本批只支持同步文件操作，**不用于后台任务或异步结果取回**。共享令牌模式、桌面个人 MCP 和[多人只读共享目录](#只读共享已有目录不需要-mcp-或重新上传)保留原接入方式。

**旧方式：仅适用于单用户私有 Web。** 以下配置使用整个 Web 进程的环境变量，**不要把个人令牌这样配给多人共用的 Web 后端**。

1. 在 BenchThread **后端进程**的环境中设置 `LAB_PERSONAL_TOKEN` 为提供者签发的令牌。不要把令牌写进聊天或提交到 Git。
2. 在 **设置 → MCP 服务** 添加 `lab-echo`，URL 填 `https://lab.example.org/mcp`。高级设置添加固定认证头：名称 `Authorization`，值填写字面量 `Bearer ${LAB_PERSONAL_TOKEN}`。只有文件服务、没有 MCP 时，跳过此步。
3. 在 **设置 → 文件服务** 添加端点：服务 ID 填提供者实际配置的 ID（如 `lab`），显示名称自定，URL 填 `https://lab.example.org/artifact`，凭据环境变量名只填 `LAB_PERSONAL_TOKEN`。如需绑定 MCP，选择或填写上一步登记的名称；只浏览和传文件则不绑定，另按[工作区读写授权](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)配置访问范围。
4. 保存 Artifact 配置，用**同一运行目录**重启 Web，再点 **测试连接**。连接通过只表示凭据和服务可用；实际文件往返以下载核对为准。若要从聊天附件发起异步计算，再按[Web 异步文件任务步骤](#echo_file-快速开始)启用 Job Protocol 并登记工具文件字段。

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

这条命令仅监听环回：本机测试地址为 `http://127.0.0.1:18080/mcp` 和 `http://127.0.0.1:18080/artifact`。**即使 Web 与 MCP 同机，也不要把这个 MCP 环回地址填进普通 Web 的 MCP 表单。** 接入 Web 时，按 [快速开始](#echo_file-快速开始)让示例监听可达的私网地址，或通过带认证的 HTTPS 反向代理提供 `/mcp` 和 `/artifact`。两者同端口不代表共用凭据：文件请求使用 `X-Artifact-Token`，MCP 的认证独立设置。开发自己的 FastMCP 服务时，参考[最小示例代码](#最小-fastmcp-示例)，把已有的 `mcp = FastMCP(...)` 对象交给 `run_with_artifact` 即可。

入口默认只监听环回地址。若已设置非空 `TMCP_ARTIFACT_SERVICE_URL`，或配置了 `TMCP_ARTIFACT_SERVICES` 和默认服务 ID，只启动 MCP，沿用 SDK 的外部配置；不会创建本地存储或在连接失败后切换。`run_with_artifact(..., token=...)` 只接受内置服务的令牌；复用外部服务时传这个参数会明确报错。外部凭据通过 `TMCP_ARTIFACT_API_TOKEN` 或服务映射的 `token_env` 设置。

### 仅启动 Artifact

不需要安装 FastMCP，也不需要写 Python 启动脚本：

```sh
python -m pip install './packages/artifact-sdk[server]'
# 同样先注入 TMCP_ARTIFACT_API_TOKEN
tcode-artifact serve --port 18081 --data-dir ./artifact-data --service-id shared-files
```

`--service-id` 可以省略：首次启动会生成 ID，此后同一数据目录会沿用该 ID。成功监听后，终端会打印本机连接地址、实际服务 ID 和 BenchThread 填写提示，不会打印令牌。新建服务时可直接用这里显示的 ID；跨主机要把地址换成 Web 后端可达的地址。

默认基础地址为 `http://127.0.0.1:18081/artifact`，仅同主机、同网络环境的客户端能访问。跨主机时给 `tcode-artifact serve` 加 `--host <文件主机的私网IP>`（或让服务保持环回监听，由同机 HTTPS 反向代理转发），并使用调用方真正可达的地址。MCP 进程按[服务配置](#服务配置)设置 Artifact 地址、相同的服务 ID 和文件服务令牌，再启动 MCP。也可在自己的 Python 入口调用 `run_artifact(data_dir=..., service_id=..., port=...)`。两种方式复用同一份服务端和存储格式，但**不要用两个进程同时对外提供同一个数据目录**。

### 配置 BenchThread 和验证

1. 打开 **设置 → 文件服务**（部分内置 Web 版本显示为 **设置 → 远端服务**），点「添加服务」。在「服务地址」填 Artifact 的基础地址（SDK 服务端以 `/artifact` 结尾，不带 `/health`），填入实际服务 ID。「显示名称」是给用户看的，不代替服务 ID。普通 Web 的 **MCP 地址**不接受 `127.0.0.1` 或 `localhost`；**Artifact 地址**只有在 Web 后端和它共用同一网络环境时才可填环回地址。浏览器能打开的地址不一定对 Web 后端可达。跨主机部署请用各端都可达的地址，公开部署用 HTTPS 和独立的 MCP 认证，不要把未认证的本地回显示例直接暴露到公网。
2. 「凭据环境变量名」填 **BenchThread 后端进程**里的变量名，例如 `ECHO_ARTIFACT_TOKEN`，不填令牌原文。把真实的文件服务令牌注入后端的该变量；不要用 MCP 连接令牌。仅在 MCP 进程中设置，Web 后端不会自动拿到它。若用环境变量代替 Web 页面配置，BenchThread 后端和 MCP 都按[服务配置](#服务配置)提供 URL、服务 ID 和所需凭据，服务 ID 都应与服务端一致。
3. **绑定**指定某个 MCP 使用哪一个文件服务，填写「MCP 服务」中登记的服务名称，例如 `sdk-echo`。**普通上传的默认服务**可选。MCP 文件传输只使用该 MCP 的显式绑定。若只在聊天里浏览、上传和下载，绑定留空，并按 [不绑定 MCP 使用 Artifact](../../docs/RUNNING.md#不绑定-mcp-使用-artifact)配置后端授权。
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

部署自己的 MCP 时，可以把这些设置和 MCP 令牌集中放在 **MCP 主机的一份私有配置文件**，由启动脚本或服务管理器注入进程，不用每次在终端手输。FastMCP 的 `auth=`、配置加载和 BenchThread 连接步骤见[部署自己的 FastMCP 服务](../../tcode_agent/MCP_QUICKSTART.md#部署自己的-fastmcp-服务)。一个 MCP 入口不需要因为文件服务变多就增加 MCP 令牌；每个 Artifact 服务仍按自己的认证要求配置。

例如，复用两个已经部署的 Artifact 服务时，可在同一份私有环境文件里写下以下设置。值均为占位符，不要提交真实令牌：

```dotenv
TMCP_ARTIFACT_SERVICE_ID=primary
PRIMARY_ARTIFACT_TOKEN=replace-with-primary-artifact-token
ARCHIVE_ARTIFACT_TOKEN=replace-with-archive-artifact-token
TMCP_ARTIFACT_SERVICES='{"primary":{"url":"https://files.example.org/artifact","token_env":"PRIMARY_ARTIFACT_TOKEN"},"archive":{"url":"https://archive.example.org/artifact","token_env":"ARCHIVE_ARTIFACT_TOKEN"}}'
```

`token_env` 填**变量名**。这个写法已在映射中指定默认服务，不需要另设 `TMCP_ARTIFACT_SERVICE_URL` 或 `TMCP_ARTIFACT_API_TOKEN`；MCP 只启动自己的工具服务，不另起 Artifact。不要套用内置 Artifact 演示中清除外部服务变量的命令。若 BenchThread 也需要上传输入或取回输出，在它自己的[文件服务设置](#配置-benchthread-和验证)中登记所需服务，保持服务 ID 一致；两台机器不一定使用相同的环境变量名。

带 `service_id=archive` 的输入只使用本地允许列表中 `archive` 的地址和凭据，未知 ID 立即失败，不回退；引用里的 URL 不参与路由。没有服务 ID 的旧引用使用默认服务。默认输出归属由构造函数或环境配置决定：在上例中始终写到 `primary`，不会因为输入来自 `archive` 而改变。SDK 不负责调度目标，也不读取宿主的目标路由设置。

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
- `upload_named_file(source, "incoming/input.txt")`：按路径创建文件，不覆盖，返回同样的固定引用。`file_ref_by_path("incoming/input.txt")` 可以取得已有文件的引用，无需再次上传。
- `upload_directory(source, workspace_id="third-party")`：上传并完成目录包，返回 BundleRef；只有服务确认 ready 才返回成功。
- 默认总字节上限 500 MiB、文件数 10,000，可使用 `max_bytes`／`max_files` 构造参数调整。原示例的 `EXAMPLE_MAX_BYTES`／`EXAMPLE_MAX_FILES` 环境变量仍兼容；服务端及 BenchThread 宿主另有独立限制，不能只改 SDK 就视为全链路放宽。

输入文件不能覆盖现有文件，目录接收使用不存在的新目录；下载核对实际字节数及 SHA-256，并核对目录清单与文件元数据。拒绝目录穿越、符号链接和非普通输出文件。为每个任务分配私有任务目录，输出完成后再上传；不要在传输期间并发修改源文件或将任务目录交给不可信进程写入。同步 API 可用于同步 MCP 工具；不要直接放进异步事件循环的长阻塞代码中。

使用此 SDK 的 Linux/macOS 内置服务时，新命名上传还会自动在服务端 `<data-dir>/files/incoming/input.txt` 生成一份服务器运行账号可读的副本。可读目录不用导出，也不能用来反向编辑服务上的原件；任务引用仍读取独立的原件，旧上传不会批量补写。每份可读副本会多占用一份文件大小的磁盘空间，请为命名上传预留空间，并保持存储目录仅供服务账号维护。Windows 服务端保持原有 API 路径浏览行为，不生成磁盘副本。

HTTP／校验错误会抛异常，不返回虚假成功、不跟随重定向、不自动重试 POST／PUT。失败的目录下载可能保留已校验的部分文件，失败的上传可能保留远端草稿包；应记录失败并在新的私有目录重试文件操作，不能把残留目录当作完整结果。不要因此重新提交状态不明的科学计算。

## 最小 FastMCP 示例

下面是一份可独立运行的 `server.py`。和仓库里的 [完整示例](examples/mcp_server.py) 一样，它用 `@mcp.tool` 注册文件回显工具。调用方传的是上传后得到的 `ArtifactRef`，不是调用方机器的文件路径。工具用 `materialize()` 下载到私有临时目录，再用 `upload_directory()` 返回包含 `echo.dat` 的文件包引用。

```python
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from fastmcp import FastMCP
from tcode_artifacts import ArtifactIO
from tcode_artifacts.server import run_with_artifact

mcp = FastMCP("Artifact file echo")


@mcp.tool
def echo_file(input_file: dict) -> dict:
    """原样回传一个文件。输入为 ArtifactRef，输出包含 echo.dat。"""
    if input_file.get("kind") != "artifact":
        raise ValueError("echo_file requires an ArtifactRef")
    files = ArtifactIO()
    with TemporaryDirectory(prefix="artifact-echo-") as directory:
        root = Path(directory).resolve()
        source = files.materialize(input_file, root / "input.dat")
        output = root / "output"
        output.mkdir()
        shutil.copyfile(source, output / "echo.dat")
        return {"status": "completed", "output_bundle": files.upload_directory(output)}


if __name__ == "__main__":
    run_with_artifact(
        mcp,
        host=os.environ.get("ARTIFACT_DEMO_HOST", "127.0.0.1"),
        port=18080,
        data_dir="./artifact-data",
        service_id="echo-files",
    )
```

在 MCP 主机安装 `./packages/artifact-sdk[server,example]` 并注入 `TMCP_ARTIFACT_API_TOKEN` 后，用 `python server.py` 启动。默认监听本机，提供 `/mcp` 和 `/artifact`；远端测试和 BenchThread 接入见下节。已有自己的 FastMCP 工具时，不需要重写工具注册：保留原来的 `mcp` 对象和 `@mcp.tool`，在需要文件的工具中使用 `ArtifactIO`。已有外部 Artifact 时也可以保留原来的 `mcp.run(...)`，只给 SDK 注入[外部连接配置](#服务配置)。

## echo_file 快速开始

这个示例把一份小文件原样回传为 `echo.dat`。准备一个**带登录、已配置对话模型的 Web**，并启用 [Job Protocol](../../docs/RUNNING.md#远程异步计算)。自定义文件字段通过异步入口登记；`tcode-server --local` 不会启动 Job Protocol，不能用于这次 Web 验证。无需 Docker 沙箱或 TMCP。

### 1. 启动示例

将[上面的最小示例](#最小-fastmcp-示例)保存为仓库根目录下的 `server.py`，然后从仓库根目录运行下面的命令。只在隔离的测试网络上使用合成文件；把 `192.168.1.20` 改成 **运行 MCP 的机器实际持有、且 Web 后端可以访问的私网 IP**。这是监听地址，不是 Web 的地址。测试令牌由安装者生成，不要提交到仓库或发送给 Agent。

```bash
source .venv/bin/activate
python -m pip install './packages/artifact-sdk[server,example]'
export ECHO_ARTIFACT_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export ARTIFACT_DEMO_HOST='192.168.1.20'

env -u TMCP_ARTIFACT_SERVICE_URL -u TMCP_ARTIFACT_SERVICES -u TMCP_ARTIFACT_SERVICE_ID \
  TMCP_ARTIFACT_API_TOKEN="$ECHO_ARTIFACT_TOKEN" python server.py &
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
4. 打开 **设置 → 文件服务**（也可能显示为 **设置 → 远端服务**），点「添加服务」。服务 ID 填 `echo-files`，显示名称可填「Echo 文件服务」，服务地址填 `http://192.168.1.20:18080/artifact`（替换 IP），凭据环境变量名填 `ECHO_ARTIFACT_TOKEN`（**不是令牌本身**），绑定的 MCP 名称填 `sdk-echo`。普通上传的默认服务可按需勾选。点 **保存 Artifact 配置**，用同一个运行目录**重启 Web**（无需重启 MCP），再回到该页面点 **测试连接**，应显示「连接正常」。只有下一步取回文件才算跑通。

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
