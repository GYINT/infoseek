# Dify 插件包（infoseek v1.0.1 G8 补全）

对照 docs.dify.ai Manifest 规范生成；插件以 **REST 桥代理**模式运行：所有工具经
infoseek 远程 MCP 的 `POST /tools/<tool_name>` 调用（Bearer 鉴权），实现仅做参数
透传 + 结果渲染（`tools/_base.py::call_remote_tool` 纯标准库，可脱离 SDK 测试）。

**v1.0.1 G8 补齐（15 工具全量）：**
- `tools/_base.py`：通用远程工具基座（HTTP 代理 + 结果提取，本地可单测）
- 15 个工具实现 + 声明：`research_v3` / `research_stream` / `manage_keys` /
  `key_usage` + 11 个 `*_async` 调研工具（`tools/*.py` + `tools/*.yaml`）
- `provider/infoseek.yaml` 全量注册 15 工具；`tools_list.json` 同步

**前置条件：**
1. 启动 infoseek MCP server（SSE/HTTP 模式，含 REST 桥）：
   ```bash
   python scripts/infoseek_mcp_server.py --transport sse --port 8765 [--require-token --token <secret>]
   ```
2. 插件凭证配置 `api_endpoint`（默认 `http://127.0.0.1:8765`）与 `api_token`。

**发布前待补项（如实声明）：**
1. 插件本地调试需安装 `dify_plugin` SDK（`pip install dify_plugin`，平台部署时由 Dify 提供）；
2. 若托管地址非默认，设置 `INFOSEEK_ENDPOINT` 或凭证 `api_endpoint`；
3. 按 Dify 平台导入校验结果微调。
