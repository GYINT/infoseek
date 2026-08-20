# Coze 插件包（infoseek v1.0.1 G8 补全）

对照 docs.coze.cn 官方规范生成：PluginManifest + **全量 OpenAPI**。

**v1.0.1 G8 补齐（15 工具全量）：**
- `openapi.json` 含 **15 个操作定义**（15 个规范工具：research_v3 / research_stream /
  manage_keys / key_usage + 11 个 `*_async`），参数 schema 取自 MCP TOOLS
  inputSchema 自动生成（脚本：`python - <<EOF` 引用 `infoseek_mcp_server.TOOLS`）
- 与当前 MCP 工具面**逐项对齐验证通过**（15/15，含鉴权 scheme、4 类错误响应）
- `plugin_manifest.json` 描述同步 15 工具；`tools_list.json` 同步

**使用：**
1. 启动 infoseek MCP server（SSE/HTTP 模式，含 REST 桥）：
   ```bash
   python scripts/infoseek_mcp_server.py --transport sse --port 8765 [--require-token --token <secret>]
   ```
2. 扣子平台导入 `openapi.json`（或按插件包打包上传），配置 `service_token`。

**发布前待补项（如实声明）：**
- 托管地址 / 图标 URL 由构建时 `INFOSEEK_PUBLIC_URL` 注入（当前：http://127.0.0.1:8765）；
  发布时设置该环境变量后重新构建；
- 认证 payload 的 `service_token` 按扣子平台注入方式配置；
- 扣子导入后按平台校验结果微调（OpenAPI 3.0 兼容性）。
