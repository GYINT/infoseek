# Infoseek v1.2.0 发布说明

> 发布日期：2026-08-20 ｜ 版本：1.2.0（能力里程碑）｜ 许可证：MIT ｜ 类型：开源
> 前置：v1.0.1（审计 G1–G13 + ABC 增强 + 引擎生命周期 P0–P3）

---

## 一句话总结

Infoseek 是一套**端到端的内容智能采集与调研工作流**——从行业/主题/人名输入开始，自动嗅探信息源、四维评分门控、4 级深度抓取、矛盾检测、实体图谱，最终输出结构化 Markdown 报告。**纯 Python、外部依赖全可选降级、可跨生态部署**。

v1.2.0 是能力里程碑版本：补齐搜索引擎全生命周期管理、搜索召回增强、4 级抓取（L3 凭证 / L4 多媒体）、Key 管理全生命周期，并通过 10k 源性能基准验证线性可扩展。

---

## v1.2.0 核心特性

| 能力 | 说明 |
|---|---|
| 🧭 **搜索引擎全生命周期** | 健康状态机 / 配额追踪（429 自动退出）/ 认证粘滞 / **新鲜度自愈**（配额重置恢复、冷却恢复、API 漂移检测、TTL 对账）+ CLI engine-status/reconcile/probe/reset |
| 🎯 **搜索召回增强** | query 别名扩展（实体别名跨名召回）/ 跨引擎多样性轮询（防单源垄断）/ 自适应相关性门槛 / 动态层权重（opt-in） |
| 🕸️ **4 级抓取** | L1 静态 → L2 playwright 渲染 → L3 凭证辅助（KeyManager 注入，**仅内存不落盘**）→ L4 多媒体统一 chunk（whisper 可选降级） |
| 🔑 **Key 管理** | 归一化 Key 管理（多后端 / 状态机 / 熔断 / 多 key 池 / 配额 / token 成本折算 / 加密落盘 + 系统 keyring）+ CLI 16 子命令 |
| ⚡ **perf 10k 基准** | 10k 源实测近线性扩展（评分 139s / 冲突 89s / research 97s），无指数退化 |
| ✅ **质量基线** | 全量回归 25/25 套件 PASS；质量基线 26/26 all_ok；符号自检 9 模块 ALL OK |

## 多生态适配

发布包内含 **7 个生态的安装/注册产物**（`dist/`）：

| 生态 | 形态 | 状态 |
|---|---|---|
| **WorkBuddy** | 本地 SKILL 包 | ✅ 直接可用 |
| **ima.copilot** | 注册清单（stdio） | ✅ 按平台规范提交注册 |
| **Claude Desktop** | mcp.json（stdio 本地 + SSE 远程） | ✅ 直接可用 |
| **通用 MCP** | 平台无关配置 | ✅ 任意 MCP 客户端可直连 |
| **Dify** | 插件包（Manifest + Python 实现 + 图标/隐私） | ✅ 结构对齐，待平台导入核验 |
| **Coze** | PluginManifest + OpenAPI 操作 | ✅ 结构对齐，待平台导入核验 |
| **Codex / 工作流 / 插件** | 同 MCP 通用配置 | ✅ 直连 |

## MCP 工具面（15 规范 + 12 兼容并存）

| 类别 | 工具 |
|---|---|
| **研究核心（2）** | `research_v3`, `research_stream` |
| **异步工具（11）** | `search_anchors_async`, `fetch_content_async`, `save_archive_async`, `check_dedup_async`, `dedup_stats_async`, `fuse_analysis_async`, `cross_subject_analysis_async`, `summarize_content_async`, `conflict_detection_async`, `score_source_async`, `score_contradiction_async` |
| **Key 管理（2）** | `manage_keys`（list/stat/rotate/revoke，脱敏）, `key_usage`（用量成本报表） |
| **REST 桥** | `POST /tools/<tool_name>`（Bearer 鉴权）—— Coze / Dify 按 OpenAPI 导入 |
| **兼容并存期（12）** | 11 个 sync + `research`（附 `deprecated: true` + `migrate_to`） |

## 传输与托管

- **stdio**（本地首选）｜**SSE+token**（远程一级部署）｜**HTTP**（平台兼容）
- 远程托管 CLI：`python scripts/infoseek_host.py start [--port] [--host] [--token]`（脱离父进程组 + 健康检查 + token 鉴权）

## 质量门控

- 全量回归：**25/25 套件 PASS**（`python tests/run_tests.py`，脚本风格勿用 pytest）
- 质量基线：`dist/quality_baseline.json`（v1.2.0，26/26 all_ok）
- 符号自检：`python scripts/mcp_tools_check.py` → 9 模块 ALL OK
- 泄漏扫描：`python scripts/leak_scan.py`（发布前 0 命中）
- perf 基准：`dist/perf_baseline_v101.json`（10k 源单轮实测；多轮 P50/P95 见 ROADMAP 待办）

---

## 历史版本摘要

### v1.0.1（2026-08-20）
- 全维度审计 G1–G13 全闭环（subprocess 硬编码 / 权限 / 路径穿越 / L2 抓取 / LLM 路径 / 测试 / 生态 / env 文档 / 死代码 / 模块拆分 / 工具收敛 / 基线）
- ABC 能力增强：QCM 跨 skill 协同、AST 符号自检、Keyring 后端、token 成本折算、CLI backup/restore、perf 基准、引擎健康探测
- 搜索引擎生命周期 P0–P3：错误分类状态机 / 配额追踪 / 能力路由 + 新鲜度自愈

### v1.0.0（2026-08-19）
- 首个发布版本：工具面收敛（25→13 规范）、搜索引擎降级链重写（DDG HTML / Bing RSS / Wikipedia 真实结果）、4 维评分门控、矛盾检测、实体图谱、结构化报告、本地持久化（`~/.infoseek`）、零依赖核心

---

## 安装与快速开始

```bash
# 依赖（核心 + 文本分析 + 可选 LLM）
pip install -r requirements.txt
# 可选：浏览器抓取（L2/L3）
pip install -r requirements-extra.txt

# 本地 MCP（stdio）
python scripts/infoseek_mcp_server.py
# 远程 SSE 托管
python scripts/infoseek_host.py start --port 8765
```

完整依赖与 API Key 配置见 `references/external-deps.md` / `references/api-keys.md`。

---

## 已知限制（如实声明）

- **运行时托管需 Python 环境**：Coze / Claude SSE / Dify 云端调用需先 `infoseek_host.py start`；stdio 形态完全本地
- **L4 转录默认占位**：whisper 为可选依赖，未安装时仅返回多媒体元信息（`transcript_available=False`）
- **实体库为进程内状态**：实体元数据跨进程不持久化（FreshnessCron 验证结果以统计返回）；持久层已列入 ROADMAP 待办
- **perf 多轮 P50/P95**：10k 单轮数据已入基线；多轮采样留作后续（ROADMAP P1）
- **Dify 插件需 `dify_plugin` SDK** 调试（生产环境无此依赖）

## 路线图

详见 `references/ROADMAP.md`（历史脉络 · 待办 · 前景方向）。近期：perf 多轮基准 / 实体持久层 / L3 真实凭证冒烟；中期（v2.x）：召回深化 / 转录落地 / 多模态起步。

---

## 致谢

- 上游 **infoseek** 项目（expeditionhub/infoseek）奠定的核心架构
- 各依赖库的开发者（duckduckgo / Bing / Wikipedia / MCP 生态）
- 所有早期内测与反馈者

## 反馈与贡献

- Issues / Discussions：按平台选择
- 许可证：MIT（详见 `LICENSE`）

---

**Infoseek v1.2.0 — 让每一次调研都更可靠。**
