# infoseek WorkBuddy SKILL 包（v1.0.0）

本目录为 WorkBuddy 生态构建产物的**差异层**（2026-08-19 精简）。

## 使用方式

完整 SKILL 包以主目录为准（`../..` 即 infoseek-v1.0.0 根目录），本目录仅保留 WorkBuddy 特有配置：

| 文件 | 用途 |
|------|------|
| `.mcp.json` | WorkBuddy 生态 MCP server 配置（双服务器） |
| `README.md` | 本说明 |

## 安装步骤

1. 将主目录（含 `core/` `scripts/` `domains/` `ecosystem/` 等）整体作为 SKILL 目录
2. 按 WorkBuddy 技能安装流程打包（或直接使用主目录）
3. `.mcp.json` 由主目录 `.mcp.json` 生成，保持一致

> 注：v1.0.0 起 WorkBuddy 与主目录共享同一套运行代码，不再维护独立副本（避免 65 文件重复漂移）。
