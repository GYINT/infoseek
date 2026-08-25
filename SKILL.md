---
name: infoseek
version: 1.3.0
description: 端到端内容智能采集与调研工作流。从行业/主题/人名/公司输入开始，自动嗅探信息源、按可信度+主题一致性+互动深度+LLM可读性四维评分门控、深度抓取（4级降级：静态/渲染/凭证/多媒体）、搜索引擎全生命周期管理（健康/配额/新鲜度自愈）、语义矛盾检测（共享事实槽+否定词典+极性放大）、实体识别（95+实体+多语种+别名归并）、召回增强（别名扩展/多样性合并/自适应门槛）、跨源融合分析，最终输出结构化 Markdown 报告，可选自动归档。适用：行业调研、趋势分析、竞品分析、市场研究、技术研究、内容采集、报告生成、长期知识库建设。不适用：实时新闻监控、学术文献综述、浏览器自动化爬取、即时聊天对话
license: MIT
---

# Infoseek

> 端到端内容智能采集与调研工作流。把"信息发现 + 内容采集 + 智能融合"封装成可复用的调研流水线。

---

## 目录

1. [这是什么](#1-这是什么)
2. [快速上手](#2-快速上手)
3. [工作流](#3-工作流)
4. [核心能力](#4-核心能力)
5. [工作机制要点](#5-工作机制要点)
6. [兼容性](#6-兼容性)
7. [路线图](#7-路线图)
8. [触发词](#8-触发词)
9. [配套文档](#9-配套文档)
10. [测试与质量](#10-测试与质量)

---

## 1. 这是什么

Infoseek 接收一个调研主题（行业/公司/人名/技术问题），自动完成：

1. **多源嗅探** — 关键词展开后从 web/kb/note 多渠道并行检索（搜索引擎全生命周期管理：健康/配额/新鲜度自愈）
2. **多维评分** — 按可信度（40%）+ 主题一致性（30%）+ 互动深度（20%）+ LLM 上下文可读性（10%）四维评分门控
3. **深度抓取** — 4 级降级：静态 HTML（L1）→ 浏览器渲染（L2）→ 凭证辅助（L3）→ 多媒体处理（L4），缺依赖自动降级
4. **智能融合** — 跨源语义矛盾检测 + 实体识别（95+ 实体词典 + 跨语种 + 别名归并）+ 召回增强（别名扩展 / 跨引擎多样性 / 自适应相关性门槛）
5. **结构化报告** — Markdown 报告（核心源摘要 + 多源交叉融合 + 根因分层表）
6. **可选归档** — 调研指令加 `[归档]` 启用，自动落盘到 `infoseek-archives/<subject>/` + 主题 README

**3 种使用方式**：单次直接调用 / 批量调度长期知识库 / 作为 MCP 工具被 Claude/Codex 等 AI Agent 调用。

### 1.1 适用场景

✅ 行业调研 / 趋势分析 / 竞品分析 / 市场研究 / 技术研究 / 内容采集 / 报告生成 / 长期知识库建设

### 1.2 不适用场景

❌ 实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话

---

## 2. 快速上手

### 2.1 Python SDK（单次调研）

```python
from infoseek_core_v2 import research, async_research, streaming_research

# 同步（兼容入口，推荐迁移到 async）
res = research("国产开源大模型", lite=True)

# 异步（推荐）
import asyncio
res = asyncio.run(async_research("AI Agent 框架对比", lite=True))

# 流式（AsyncIterator 7 步 yield）
async for partial in streaming_research("DeepSeek V3", lite=True):
    print(partial["step"], "...")  # score_complete / wikidata_complete / ...
```

### 2.2 MCP 工具调用（15 规范工具 + 12 兼容并存）

| 类别 | 工具数 | 用途 |
|------|--------|------|
| **研究核心** | 2 | `research_v3` / `research_stream` |
| **异步工具** | 11 | `search_anchors_async` / `fetch_content_async` / `save_archive_async` / `check_dedup_async` / `dedup_stats_async` / `fuse_analysis_async` / `cross_subject_analysis_async` / `summarize_content_async` / `conflict_detection_async` / `score_source_async` / `score_contradiction_async` |
| **Key 管理** | 2 | `manage_keys`（list/stat/rotate/revoke，脱敏）/ `key_usage`（用量/成本报表） |
| **兼容并存期** | 12 | 11 个同步工具 + `research`（仍响应，附 `deprecated: true` + `migrate_to`） |
| **REST 桥** | — | `POST /tools/<tool_name>`（Bearer 鉴权，供 Coze/Dify 等 OpenAPI 生态） |

启动 MCP server：

```bash
python scripts/infoseek_mcp_server.py --transport sse --port 8765
# 或 stdio（本地默认）: python scripts/infoseek_mcp_server.py
```

### 2.3 长期知识库

```python
# 调研指令加 [归档]
res = research("AI Agent 行业 2026 Q1 [归档]", lite=True)
# → 自动落盘 infoseek-archives/AI_Agent_行业_2026_Q1/ + 主题 README
```

---

## 3. 工作流

```
输入主题
   ↓
阶段一：锚点发现（search_anchors / research）
   关键词展开 → 多源嗅探（引擎生命周期管理）→ 多维评分门控
   ↓
阶段二：内容采集（fetch_content）
   URL 预检 → 标准化去重 → 4 级降级提取（L1 静态 / L2 渲染 / L3 凭证 / L4 多媒体）
   ↓
阶段三：智能融合（fuse_analysis / research）
   矛盾检测（contradiction_scorer）→ 实体识别（NER）→ 跨源融合
   ↓
阶段四：输出报告（research / summarize_content）
   Markdown 报告（观点 + 数据 + 来源）
   ↓
阶段五（可选）：存档归档（save_archive / [归档]）
   落盘 + 主题 README + 长期沉淀
```

| 阶段 | 关键模块 | 输入 | 输出 |
|------|---------|------|------|
| 锚点发现 | `core/anchor_score_v2.py` / `core/trust_sources.py` | 主题字符串 | 评分排序的候选源列表 |
| 内容采集 | `scripts/mcp_tools_search.py` | URL | Markdown/JSON/TXT 文本或多媒体 chunk |
| 智能融合 | `core/contradiction_scorer.py` / `core/entity_graph.py` | 多个源 | 矛盾列表 + 实体图谱 |
| 报告输出 | `scripts/summarize_adapter.py` | 融合结果 | 结构化报告 |
| 存档归档 | `scripts/infoseek_helper.py` | 报告 + 元数据 | `infoseek-archives/<subject>/` |

---

## 4. 核心能力

> 按 **职能分层**：调研入口 → 智能分析 → 实体管理 → LLM 路由 → 搜索与抓取 → 输出导出 → MCP 工具

### 4.1 调研入口（核心 API）

| 函数 | 模块 | 类型 | 简述 |
|------|------|------|------|
| `streaming_research` | `scripts/infoseek_core_v2.py` | 核心入口 | 流式研究（AsyncIterator 7 步 yield） |
| `async_research` | `scripts/infoseek_core_v2.py` | 核心入口 | 异步研究（多步并发 asyncio.gather） |
| `research` | `scripts/infoseek_core_v2.py` | 兼容入口 | 同步研究（兼容保留） |

### 4.2 智能分析

| 函数 / 类 | 模块 | 简述 |
|-----------|------|------|
| `detect_conflicts_v3` / `detect_conflicts_v3_async` | `core/conflict_v3.py` | 跨源矛盾检测（别名归并 + 严重度评级） |
| `ConflictMonitor.ingest_*_async` | `core/conflict_v3.py` | 实时冲突管道（async 接口） |
| `score_contradiction` / `score_contradiction_async` | `core/contradiction_scorer.py` | 两句话矛盾评分（severity 四档） |
| `EntityGraph` | `core/entity_graph.py` | 实体图谱（加权边 + Graphviz 导出） |
| `extract_entities` | `core/ner.py` | 命名实体识别（95+ 实体词典） |
| `predict_heat` | `core/entity_heat.py` | 实体热度预测（衰减外推） |
| `trace_entity` | `core/entity_trajectory.py` | 实体轨迹追踪（90 天窗口） |

### 4.3 实体管理

| 类 | 模块 | 简述 |
|------|------|------|
| `EntityProfile` | `core/entity_profile.py` | 实体画像（topics/source_domains） |
| `EntityTracker` | `core/entity_tracker.py` | 频次统计（90 天半衰期） |
| `ClaimStore` | `core/claim_store.py` | 跨会话历史声明比对 |
| `EntityAliases` | `core/entity_aliases.py` | 别名管理（hot/cold + 生命周期） |
| `WikidataSync` | `core/wikidata_sync.py` | Wikidata 公开 API 同步（8 类别 SPARQL） |
| `FreshnessCron` | `core/freshness_cron.py` | 新鲜度扫描（衰减 + 冷条目验证 + alias/profile/claim TTL） |

### 4.4 LLM 路由与 Key 管理

| 函数 | 模块 | 简述 |
|------|------|------|
| `llm_call` | `core/llm_router.py` | 多 provider 路由（Ollama/DeepSeek/智谱/Kimi/OpenAI/Anthropic），自动 fallback + mock 降级 |
| `score_with_llm_async` | `core/contradiction_scorer.py` | LLM 矛盾评分异步版 |
| `KeyManager` | `core/key_manager.py` | 归一化 Key 管理（多后端 / 状态机 / 熔断 / 多 key 池 / 配额 / 用量统计 / 加密落盘 + keyring） |
| `keys` CLI | `scripts/infoseek_keys_cli.py` | Key 生命周期命令（add/list/stat/rotate/revoke/quota/usage/export/backup/restore/keyring-persist/keyring-load） |
| `leak_scan` | `scripts/leak_scan.py` | 密钥泄漏扫描器（合规审计，多模式 + 赋值检测） |

### 4.5 搜索与抓取（v1.2 增强）

| 能力 | 模块 | 简述 |
|------|------|------|
| `search_web` 降级链 | `scripts/infoseek_pipeline.py` | 多引擎并行 + 层间降级 + 动态保留池；query 别名扩展 / 多样性轮询 / 自适应相关性门槛 |
| 引擎生命周期 | `scripts/engine_lifecycle.py` | 健康状态机 / 配额追踪 / 认证粘滞 / 新鲜度自愈（配额重置、冷却恢复、API 漂移检测）+ CLI engine-status/reconcile/probe/reset |
| QVeris 能力路由 | `scripts/qveris_client.py` | 结构化金融/数据能力：discover→inspect→call 全流程，双端点自动选区（sk-cn-→qveris.cn 合规区），429/401 自动进入引擎生命周期 |
| 4 级抓取 | `scripts/mcp_tools_search.py` | `extraction_level` 1/2/3/4 路由：静态 / playwright 渲染 / KeyManager 凭证注入（仅内存）/ 多媒体 chunk |

### 4.6 输出导出

| 函数 | 模块 | 简述 |
|------|------|------|
| `build_traced` / `to_dot` / `to_markdown` | `core/traced_export.py` | 引用图谱导出（Graphviz / Markdown） |

### 4.7 MCP 工具（15 规范 + 12 兼容并存）

| 类别 | 工具 | 用途 |
|------|------|------|
| **研究核心（2）** | `research_v3` / `research_stream` | 异步研究 / 流式研究 |
| **异步工具（11）** | `search_anchors_async` / `fetch_content_async` / `save_archive_async` / `check_dedup_async` / `dedup_stats_async` / `fuse_analysis_async` / `cross_subject_analysis_async` / `summarize_content_async` / `conflict_detection_async` / `score_source_async` / `score_contradiction_async` | 异步包装（规范接口） |
| **Key 管理（2）** | `manage_keys` / `key_usage` | Key 生命周期（list/stat/rotate/revoke，脱敏）/ 用量成本报表 |
| **兼容并存期（12）** | 11 个同步工具 + `research` | 仍响应，附 `deprecated: true` + `migrate_to` |

> 新集成请使用 **15 个规范工具**（研究核心 + 异步 + Key 管理）；同步工具仅用于老客户端兼容。

---

## 5. 工作机制要点

### 5.1 评分门控

```
Anchor_Score = 互动深度×20% + 主题一致性×30% + 来源可信度×40% + LLM 上下文可读性×10%
门控：≥70 → 🥇 核心自动采集 | 40-69 → 🥈 需确认 | <40 → 🥉 过滤
```

详见 `references/Infoseek_Anchor_Score五维契约_v1.5.md`

### 5.2 矛盾检测语义

- **事实槽提取** → 共享槽对比
- **否定/反义词典** → 极性反转
- **极性放大** → severity 四档（high / medium / low / none）

详见 `core/contradiction_scorer.py`

### 5.3 实体生命周期

```
入库 → hit 累计 → 90 天半衰期 → hot/cold 分级
                                          ↓
                                     stale 90 天未出现 → 清理候选
```

详见 `core/entity_tracker.py` / `core/entity_aliases.py`

### 5.4 LLM 路由

6 provider 优先级：`ollama-local`（priority=1）→ `deepseek` / `zhipu`（priority=2）→ `kimi` / `openai`（priority=3）→ `anthropic`（priority=4）

支持：自动 fallback / 成本控制 / 配额感知 / mock 模式（无 key 时降级）

详见 `core/llm_router.py` 与 `references/api-keys.md`

### 5.5 风险控制

RPN Top 风险已实施工程控制（详见 `references/risk-register.md`）：

- **R06**（程序化缺失）→ degradation_router 日志告警
- **R11**（DuckDuckGo API 限流）→ 限速 + 429 熔断 + Wikipedia 兜底
- **R14**（无单元测试）→ 25 套件全绿（聚合入口 tests/run_tests.py）
- **R05**（名称搜索单引擎）→ 搜索引擎降级链 + 引擎生命周期自愈

---

## 6. 兼容性

- **0 破坏性变更**：历史 API 完整保留，同步 `research()` 兼容并存
- **MCP 工具**：15 规范工具 + 12 兼容并存期工具（附 `deprecated` 标记）
- **运行时数据**：状态文件（claims/aliases/engine_state 等）落 `~/.infoseek/`（`INFOSEEK_DATA_DIR` 可覆盖），技能更新不丢数据
- **升级方式**：备份 → 替换目录 → 运行 `python tests/run_tests.py` 验证

---

## 7. 路线图

详见 `references/ROADMAP.md`（历史脉络 · 待办 · 前景方向）。

**概要**：
- **近期**：perf 多轮基准、实体持久层、L3 真实凭证冒烟
- **中期（v2.x）**：召回深化（图谱/同义词）、L4 转录落地、多模态起步
- **长期**：编排协同、合规审计自动化、实时协作
- **设计边界（不做）**：实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话

---

## 8. 触发词

按 **场景 / 技术 / 能力** 三类组织，便于不同检索维度匹配。

### 8.1 场景类（业务用途）

`行业调研` · `趋势分析` · `工艺技术研究` · `内容采集` · `信息收集` · `竞品分析` · `市场研究` · `报告生成` · `存档归档` · `长期知识库`

### 8.2 技术类（API / 协议）

`URL 去重` · `MCP 集成` · `内容摘要` · `中文文本分析` · `链式引用追踪` · `跨源冲突检测` · `领域 Skill 矩阵` · `多平台导出` · `模板化报告` · `跨语言实体识别` · `多模型 LLM 路由` · `搜索引擎生命周期` · `4级抓取` · `召回增强`

### 8.3 能力类（具体函数 / 类名）

`实体自沉淀` · `频次统计` · `Wikidata 同步` · `新鲜度 cron` · `批量入库` · `别名 JSON 持久化` · `streaming_research` · `async_research` · `ConflictMonitor` · `detect_conflicts_v3_async` · `score_contradiction` · `EntityGraph` · `predict_heat` · `trace_entity` · `EntityProfile` · `ClaimStore` · `WikidataSync` · `FreshnessCron` · `build_traced` · `to_dot` · `llm_call` · `KeyManager` · `get_key` · `engine-lifecycle` · `extraction_level`

---

## 9. 配套文档

| 文档 | 路径 | 用途 |
|------|------|------|
| README | `README.md` | 快速导航 + 5 秒看懂 |
| RELEASE_NOTES | `RELEASE_NOTES.md` | 版本发布说明 |
| 核心库 | `core/` | 22 功能模块（实体 / 评分 / 矛盾 / 爬取等；运行时数据经 state_dir 落 `~/.infoseek`） |
| 适配层 | `scripts/` | MCP server 门面 + 工具模块 + keys CLI（16 子命令）+ 引擎生命周期 + 引擎健康探测 + perf 基准 + 符号自检 |
| 引用契约 | `references/` | 契约文档 + trusted-sources.json + configuration + external-deps + api-keys + ROADMAP |
| 报告模板 | `domains/templates.yaml` | 5 领域模板 + default（块标量合并） |
| 领域配置 | `domains/*.yaml` | 5 领域（tech / market / finance / policy / competitor） |
| 测试套件 | `tests/` | 功能验证（发布前运行） |

### 9.1 引用契约清单

| 契约 | 用途 |
|------|------|
| `references/Infoseek_Anchor_Score五维契约_v1.5.md` | 评分公式 + 门控规则 |
| `references/Infoseek_MCP集成契约_v1.5.md` | MCP 协议 + 工具 schema |
| `references/Infoseek_维度命名契约_Naming_Convention.md` | 人物 6 维分桶 |
| `references/Infoseek_存档归档契约_Archive_Convention.md` | 归档目录结构 + 命名格式 |
| `references/Infoseek_URL标准化契约_URL_Normalization.md` | URL 标准化规则（去重） |
| `references/anchor-adapter.md` | 锚点 → 意图卡片转换层 |
| `references/credential-tools.md` | 凭证工具选型 |
| `references/risk-register.md` | 风险 RPN 矩阵 + 监控计划 |
| `references/configuration.md` | 全量 env 配置项参考 |
| `references/qcm-coop-contract.md` | QCM 跨 skill 协同契约 |
| `references/external-deps.md` | 外部依赖清单 + 作用 + 降级路径 |
| `references/api-keys.md` | 外部 API Key 清单 + 效益 + 获取 |
| `references/ROADMAP.md` | 历史脉络 · 待办 · 前景方向 |
| `references/trusted-sources.json` | 5 领域 × tier1-4 信任源白名单 |

---

## 10. 测试与质量

- **测试套件**：26 个测试文件（脚本风格，聚合入口 `tests/run_tests.py`；勿用 pytest——顶层 sys.exit 会导致 INTERNALERROR）
  - 核心与质量维度：`test_infoseek_v231/v240` / `test_boundary` / `test_compat` / `test_correctness` / `test_reliability` / `test_security` / `test_stability`
  - 端到端：`test_e2e_scenarios_v240.py`
  - 流式与异步：`test_streaming_v300.py` / `test_async_tools.py`
  - 搜索链 / 工具面：`test_search_engines.py` / `test_tools_surface.py`
  - 领域编排 / 兼容：`test_domain_orchestrator_v200.py` / `test_v174_jaccard.py`
  - 生命周期与协同：`test_engine_lifecycle_v101.py`（40 用例）/ `test_qcm_bridge_v101.py` / `test_key_manager_v101.py`（29 用例）
  - v1.2 新套件：`test_freshness_cron_v101.py`（23）/ `test_recall_enhance_v101.py`（16）/ `test_fetch_levels_v101.py`（26）
  - 深度测试：`test_deep_v101.py`（单独运行）
- **运行方式**：`python tests/run_tests.py`（25 标准套件）或逐个直跑；深度 `python tests/test_deep_v101.py`
- **质量门控**：边界 / 兼容 / 正确性 / 可靠性 / 安全性 / 稳定性 6 维度全覆盖
- **质量基线**：`dist/quality_baseline.json`（v1.3.0，26/26 套件 all_ok + 27 项含 deep 全绿）

---

## 附录：发布到 Skill 平台

### A.1 客户端集成示例

```json
{
  "mcpServers": {
    "infoseek": {
      "command": "python3",
      "args": ["scripts/infoseek_mcp_server.py"],
      "env": {
        "DEEPSEEK_API_KEY": "${env:DEEPSEEK_API_KEY}",
        "KIMI_API_KEY": "${env:KIMI_API_KEY}"
      }
    }
  }
}
```

### A.2 获取帮助

- 查看 `README.md` 5 秒看懂
- 查看 `RELEASE_NOTES.md` 了解版本演进
- 查看 `references/` 下契约文档
- 依赖与 Key 配置见 `references/external-deps.md` / `references/api-keys.md`
