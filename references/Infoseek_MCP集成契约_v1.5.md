# Infoseek MCP 集成契约

> 版本：v1.0.1 ｜ 状态：✅ 已提供 ｜ 对齐全：`scripts/infoseek_mcp_server.py`

## 1. 传输方式

| 传输 | 命令 | 适用 |
|------|------|------|
| stdio | `python scripts/infoseek_mcp_server.py` | 本地客户端（Claude Desktop / Codex） |
| SSE+token | `python scripts/infoseek_mcp_server.py --transport sse --port 8765 [--token XXX]` | 远程多客户端共享（经 `infoseek_host.py start` 托管） |

端点：`GET /sse`（SSE 流）｜`GET /health`（健康检查）｜`POST /messages`（JSON-RPC）｜`POST /rpc`（短请求-响应）

> ⚠️ 无 `--transport http` 选项（`--transport` 仅接受 `stdio` / `sse`）；HTTP 形态经 SSE 端点的 `/rpc` 提供。

## 2. 工具面（13 个规范工具）

### v3.0 核心（2）

| 工具 | 参数 | 说明 |
|------|------|------|
| `research_v3` | subject*, sources, domain, output_format, lite | 一次性完整调研结果 |
| `research_stream` | subject*, sources, domain, output_format, lite | 流式调研（7 步 yield） |

### v3.0 async（11）

| 工具 | 参数 | 说明 |
|------|------|------|
| `search_anchors_async` | subject*, depth, sources | 锚点发现（真实多引擎搜索） |
| `fetch_content_async` | url*, format, max_retries | L1 静态正文抓取 |
| `save_archive_async` | subject*, url*, title, content, metadata | 归档落盘 |
| `check_dedup_async` | url, title | URL 去重检查 |
| `dedup_stats_async` | — | 去重统计 |
| `fuse_analysis_async` | subject*, sources | 跨源融合分析 |
| `cross_subject_analysis_async` | subject_a, subject_b | 跨主题相关性 |
| `summarize_content_async` | text*, max_len, prefer | 内容摘要 |
| `conflict_detection_async` | sources*, subject | 跨源矛盾检测 |
| `score_source_async` | query*, source* | 单源评分 |
| `score_contradiction_async` | claim_a*, claim_b* | 两句矛盾评分 |

### 废弃并存期（12）

11 个 v1.x sync 工具 + `research`：仍响应，结果附 `deprecated: true` + `migrate_to`，计划 v4.0.0 冻结。

## 3. REST 桥（Coze/Dify 生态）

```
POST /tools/<tool_name>   # Bearer token 鉴权（若启用）
Body: JSON-RPC 参数对象
```

## 4. 客户端配置示例

```json
{
  "mcpServers": {
    "infoseek": {
      "command": "python3",
      "args": ["scripts/infoseek_mcp_server.py"],
      "env": {
        "INFOSEEK_ROOT": "<skill根目录>",
        "DEEPSEEK_API_KEY": "${env:DEEPSEEK_API_KEY}",
        "KIMI_API_KEY": "${env:KIMI_API_KEY}"
      }
    }
  }
}
```

## 5. 鉴权与安全

- SSE 远程模式支持 `--token` + Bearer 校验（`--require-token`）
- 归档/落盘路径由 `state_dir` 解析，运行时数据不写技能目录
