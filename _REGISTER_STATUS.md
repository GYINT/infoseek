# Infoseek 多生态注册状态跟踪（v1.0.0）

> 目标生态：WorkBuddy / ima.copilot / Claude Desktop / Codex / Dify / Coze / 通用 MCP。
> 本表为单一真源；每次生态接入/重新提交后更新。v1.0.0：工具面已收敛 **25 → 13**（v1.0.1 扩展至 **16**：+manage_keys/key_usage/qcm_query）
> （12 个废弃工具并存期保留，调用附 deprecated 标记）；搜索链 v1.1.0 起含 AI 引擎（Jina 免 key 主选 + Exa/Tavily/TinyFish 键控冗余，`INFOSEEK_SEARCH_ENGINE=ai` 启用）；server 新增 REST 桥
> `POST /tools/<name>`，供 OpenAPI 生态（Coze/Dify）按独立端点调用。

## 状态总览

| 生态 | 状态 | 入口/产物 | 注册说明 | 最后更新 |
|---|---|---|---|---|
| **WorkBuddy** | ✅ 本地就绪 | SKILL 目录（活跃）| 直接可用，无需注册；`dist/workbuddy/` 已精简为差异层（2026-08-19，避免 65 文件重复漂移） | 2026-08-19 |
| **ima.copilot** | ✅ 已注册(v1.3.0) | `dist/ima/ima_manifest.json` | **v1.3.0 重新提交**（2026-08-25，SKILL.md frontmatter 同步 1.2.0→1.3.0）。历史：v1.0.0 最终版 2026-08-19 提交成功（code=0）；旧版本线 v3.1.0 注册 id `7486824209471306` 为历史遗留，旧 id 如需清理请在 ima 平台控制台手动下架 | 2026-08-25 |
| **Claude Desktop** | 🔵 待注册 | `dist/claude/mcp.json` | 拷贝 mcp.json 到客户端配置即可；SSE 远程需先 `infoseek_host.py start` | 2026-08-19 |
| **Codex** | 🔵 待注册 | `dist/generic-mcp/mcp.json` | HTTP/SSE 直连；推荐启用 Bearer token | 2026-08-19 |
| **Dify** | 🟡 已补全待平台核验 | `dist/dify/manifest.yaml` + `provider/` + `tools/` | **v1.0.1 G8 补全**：15 工具全量实现（`tools/_base.py` REST 桥代理 + 15 个 `tools/*.py` + `*.yaml`，`provider/infoseek.yaml` 全量注册）；`call_remote_tool` 纯标准库本地测通；平台部署需 dify_plugin SDK（Dify 提供） | 2026-08-20 |
| **Coze** | 🟡 已补全待平台核验 | `dist/coze/plugin_manifest.json` + `openapi.json` | **v1.0.1 G8 补全**：`openapi.json` 重新生成 **15 个操作定义**（与 MCP TOOLS 逐项对齐验证通过）；`plugin_manifest.json` / `tools_list.json` 同步 | 2026-08-20 |
| **通用 MCP** | ✅ 可直连 | `dist/generic-mcp/mcp.json` | 任意 MCP 客户端按 mcpServers 接入；stdio/SSE/HTTP 三传输 | 2026-08-19 |

## 状态图例

- ✅ 就绪/可直连：产物可直接使用
- 🔵 待注册：产物已生成，尚未在对应平台注册/安装
- 🟡 已注册但待更新：早期注册，v3.2.0 变更后需重新提交
- 🟠 模板待核验：结构对齐但字段需对照平台最新规范核验

## 更新流程

1. 生态接入/变更后，手动更新 `dist/` 对应产物（注：v1.0.0 未提供 `infoseek_build.py`，产物为手工维护）。
2. 更新上表对应行（状态/说明/最后更新）。
3. 运行 `python tests/run_tests.py` 全绿后方可提交发布（注：`quality_gate.py` 尚未随包提供，以 run_tests.py + dist/quality_baseline.json 为准）。
