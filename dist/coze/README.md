# Coze 插件包（infoseek v1.0.0）

对照 docs.coze.cn 官方规范（2026-08 核验）生成：PluginManifest + **全量 OpenAPI**。

**已补齐（v1.0.0）：**
- `openapi.json` 含 **13 个操作定义**（13 个规范工具，POST /tools/<name>，
  参数 schema 取自 MCP TOOLS inputSchema），对应 server 新增的 REST 桥
  （Bearer token 鉴权）。
- 托管地址 / 图标 URL 由构建时 `INFOSEEK_PUBLIC_URL` 注入（当前：http://127.0.0.1:8765）；
  发布时设置该环境变量后重新构建。

**发布前待补项（如实声明）：**
- 认证 payload 的 `service_token` 按扣子平台注入方式配置；
- 扣子导入后按平台校验结果微调（OpenAPI 3.0 兼容性）。
