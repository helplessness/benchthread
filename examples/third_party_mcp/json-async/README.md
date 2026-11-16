# 最小异步 MCP：整数求和（只收发 JSON）

[server.py](server.py) 只依赖 FastMCP：提交先存到 SQLite 并返回任务 ID，后台循环再计算总和。BenchThread 侧的 [插件声明](plugin/async-targets.json) 负责把三个 MCP 工具映射到异步流程。不需要文件 SDK、Artifact 服务或私有 TMCP。

## 1. 启动第三方服务

在仓库根目录执行（Linux/macOS）：

```bash
python3 -m venv .venv-json-mcp
.venv-json-mcp/bin/python -m pip install 'fastmcp>=3.2,<4'
JSON_ASYNC_DB="$PWD/.tcode/json-async-jobs.sqlite" .venv-json-mcp/bin/python examples/third_party_mcp/json-async/server.py
```

默认地址为 `http://127.0.0.1:10231/mcp`。本例无鉴权，只监听回环地址，用于同机验证；不要直接开放到公网。

- `submit_sum(request_id, tool, arguments)`：接收业务工具名 `sum_numbers` 和 `{"numbers":[1,2,3]}`，立即返回 `job_id`。重复的请求 ID 和参数只返回原任务 ID。
- `get_sum_status(job_id)`：返回 `queued` 或 `completed`。
- `get_sum_result(job_id)`：完成后读取已保存的结果 `{"sum":6}`，不会再次计算。

重启时沿用数据库路径即可按原 ID 查询。SQLite 只是演示存储，实际可以复用已有任务系统。本例不实现取消和按请求 ID 找回丢失的受理回执；真实长计算应交给独立执行器，不要放在 SQLite 事务里。

## 2. 接入 BenchThread

1. 按[接入指南](../../../docs/服务隔离/第三方MCP插件接入与运行.md)启用内置 Job Protocol 和后台执行。可在 BenchThread 启动环境设置 `TCODE_MANAGED_JOB_PROTOCOL=1`；Web 不要使用纯聊天的 `--local`。本例需使用支持 JSON 异步声明的当前源码。
2. 在 BenchThread 的 Python 环境中运行 `python scripts/plugin.py validate examples/third_party_mcp/json-async/plugin`。将以下条目合并进实际生效的 `plugins.json` 的 `plugins` 数组，替换为实际绝对路径；按现有插件信任和隔离策略加载，不覆盖其他条目。

   ```json
   {
     "name": "json-async-sum",
     "path": "/实际路径/examples/third_party_mcp/json-async/plugin",
     "enabled": true,
     "config": {}
   }
   ```

3. 在目标工作区打开 **设置 → MCP 服务 → 插件异步 MCP**。选择 `json-async-sum` 的 `sum`，填 `http://127.0.0.1:10231/mcp`，令牌留空。点「检查连接」，再点「启用／更新异步目标」。

## 3. 提交并核对结果

在会话里发送：「用 json-async-sum 插件异步计算 [1,2,3] 的总和。用 Job Protocol 发现目标，只提交一次；收到受理号后等完成通知。」

如果手动构造工具调用，先用 `mcp_job-protocol-mcp_describe_target_tool`（`input_config: {}`）找到 `plugin=json-async-sum` 对应的 `server`，再向 `mcp_job-protocol-mcp_submit_job` 传：

```json
{
  "input_config": {
    "target_server": "<发现接口返回的 server>",
    "target_tool": "sum_numbers",
    "arguments": {"numbers": [1, 2, 3]}
  }
}
```

按界面要求审核。收到 `LOCAL-*` 只是 BenchThread 受理；等后台回收结果后，在原会话核对 `{"sum":6}`。需要 Agent 接着分析时，再点「继续此任务」。
