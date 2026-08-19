---
name: infoseek
version: 1.0.0
description: 端到端内容智能采集与调研工作流。从行业/主题/人名/公司输入开始，自动嗅探信息源、按可信度+主题一致性+互动深度+LLM可读性四维评分门控、深度抓取（4级降级）、语义矛盾检测（共享事实槽+否定词典+极性放大）、实体识别（95+实体+多语种+别名归并）、跨源融合分析，最终输出结构化 Markdown 报告（观点+数据+来源），可选自动归档到 `infoseek-archives/` 长期沉淀。适用：行业调研、趋势分析、竞品分析、市场研究、技术研究、内容采集、报告生成、长期知识库建设。不适用：实时新闻监控、学术文献综述、浏览器自动化爬取、即时聊天对话
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
6. [升级与兼容](#6-升级与兼容)
7. [路线图](#7-路线图)
8. [触发词](#8-触发词)
9. [配套文档](#9-配套文档)
10. [测试与质量](#10-测试与质量)

---

## 1. 这是什么

Infoseek 接收一个调研主题（行业/公司/人名/技术问题），自动完成：

1. **多源嗅探** — 关键词展开后从 web/kb/note 多渠道并行检索
2. **多维评分** — 按可信度（40%）+ 主题一致性（30%）+ 互动深度（20%）+ LLM 上下文可读性（10%）四维评分门控
3. **深度抓取** — 4 级降级：静态 HTML → 反爬兜底 → 凭证辅助 → 多媒体处理
4. **智能融合** — 跨源语义矛盾检测 + 实体识别（95+ 实体词典 + 跨语种 + 别名归并）
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

# 同步（v1.x 兼容，v3.0 起 deprecated 12 个月）
res = research("国产开源大模型", lite=True)

# 异步（v2.5+ 推荐）
import asyncio
res = asyncio.run(async_research("AI Agent 框架对比", lite=True))

# 流式（v3.0 推荐，AsyncIterator 7 步 yield）
async for partial in streaming_research("DeepSeek V3", lite=True):
    print(partial["step"], "...")  # score_complete / wikidata_complete / ...
```

### 2.2 MCP 工具调用（25 工具）

| 类别 | 工具数 | 用途 |
|------|--------|------|
| **v1.x sync** | 11 | `search_anchors` / `fetch_content` / `save_archive` / `check_dedup` / `dedup_stats` / `fuse_analysis` / `cross_subject_analysis` / `summarize_content` / `conflict_detection` / `score_source` / `research` |
| **v3.0 核心** | 3 | `research_v3` / `research_stream` / `score_contradiction` |
| **v3.0 async** | 11 | `*_async` 工具（如 `research_v3_async` / `summarize_content_async` 等） |

启动 MCP server：

```bash
python scripts/infoseek_mcp_server.py --transport http --port 8765
# 详见 references/Infoseek_MCP集成契约_v1.5.md
```

### 2.3 长期知识库

```python
# 调研指令加 [归档]
res = research("AI Agent 行业 2026 Q1 [归档]", lite=True)
# → 自动落盘 infoseek-archives/AI_Agent_行业_2026_Q1/
# → 自动生成主题 README
```

---

## 3. 工作流

```
输入主题
   ↓
阶段一：锚点发现（search_anchors / research）
   关键词展开 → 多源嗅探 → 多维评分门控
   ↓
阶段二：内容采集（fetch_content / fetch_content_chain）
   URL 预检 → 标准化去重 → 4 级降级提取
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
| 内容采集 | `scripts/infoseek_mcp_server.py` `fetch_content` | URL | Markdown/JSON/TXT 文本 |
| 智能融合 | `core/contradiction_scorer.py` / `core/entity_graph.py` | 多个源 | 矛盾列表 + 实体图谱 |
| 报告输出 | `core/contradiction_scorer.py` / `scripts/summarize_adapter.py` | 融合结果 | 结构化报告 |
| 存档归档 | `scripts/infoseek_helper.py` | 报告 + 元数据 | `infoseek-archives/<subject>/` |

---

## 4. 核心能力

> 按 **职能分层**：调研入口 → 智能分析 → 实体管理 → LLM 路由 → 输出导出 → MCP 工具

### 4.1 调研入口（核心 API）

| 函数 | 模块 | 类型 | 简述 |
|------|------|------|------|
| `streaming_research` | `scripts/infoseek_core_v2.py` | 核心入口 | 流式研究（AsyncIterator 7 步 yield） |
| `async_research` | `scripts/infoseek_core_v2.py` | 核心入口 | 异步研究（5 步并发 asyncio.gather） |
| `research` | `scripts/infoseek_core_v2.py` | 核心入口 | 同步研究（v1.x 兼容，deprecated 12 月） |

### 4.2 智能分析

| 函数 / 类 | 模块 | 简述 |
|-----------|------|------|
| `detect_conflicts_v3` | `core/conflict_v3.py` | 跨源矛盾检测（别名归并 + 严重度评级） |
| `detect_conflicts_v3_async` | `core/conflict_v3.py` | 异步矛盾检测 |
| `ConflictMonitor.ingest_*_async` | `core/conflict_v3.py` | 实时冲突管道（async 接口） |
| `score_contradiction` | `core/contradiction_scorer.py` | 两句话矛盾评分（severity 四档） |
| `score_contradiction_async` | `core/contradiction_scorer.py` | 矛盾评分 async 版 |
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
| `FreshnessCron` | `core/freshness_cron.py` | 定期扫描（衰减 + 冷条目 Wikidata 验证） |

### 4.4 LLM 路由

| 函数 | 模块 | 简述 |
|------|------|------|
| `llm_call` | `core/llm_router.py` | 6 provider 路由（OpenAI / Anthropic / 智谱 / DeepSeek / Kimi / Ollama） |

### 4.5 输出导出

| 函数 | 模块 | 简述 |
|------|------|------|
| `build_traced` / `to_dot` / `to_markdown` | `core/traced_export.py` | 引用图谱导出（Graphviz / Markdown） |

### 4.6 MCP 工具（25 个）

| 类别 | 工具 | 用途 |
|------|------|------|
| **v1.x sync（11）** | `search_anchors` / `fetch_content` / `save_archive` / `check_dedup` / `dedup_stats` / `fuse_analysis` / `cross_subject_analysis` / `summarize_content` / `conflict_detection` / `score_source` / `research` | 单次同步调研 |
| **v3.0 核心（3）** | `research_v3` / `research_stream` / `score_contradiction` | 异步 / 流式 / 矛盾评分 |
| **v3.0 async（11）** | `*_async` 工具（如 `research_v3_async` / `summarize_content_async` 等） | async 包装 |

> ⚠️ **遗留标注**：`v1.x sync（11）` 为向后兼容遗留接口，仅用于老客户端；v3.0+ 新集成请优先使用 **v3.0 核心（research_v3 / research_stream / score_contradiction）+ v3.0 async（`*_async`）**。计划于 **v4.0.0** 起冻结 v1.x sync 工具（与 `research()` 同步路径一致）。

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

详见 `core/llm_router.py`

### 5.5 风险控制

RPN Top 5 风险已实施工程控制（详见 `references/risk-register.md`）：

- **R06**（Tier2/Tier3 程序化缺失）→ degradation_router 日志告警
- **R11**（DuckDuckGo API 限流）→ 1.5s 间隔 + 429 熔断 + Wikipedia 兜底
- **R14**（无单元测试）→ 121/121 测试套件覆盖
- **R05**（名称搜索单引擎）→ 搜索引擎降级链

---

## 6. 升级与兼容

### 6.1 兼容性承诺

- **0 破坏性变更**：所有 v1.x / v2.x API 完整保留
- **同步 `research()` 12 个月并存期**：v3.6.0 强制迁移，v4.0.0 移除
- **MCP 工具 25 个**：11 sync + 11 async + 3 v3.0 核心（`research_v3` / `research_stream` / `score_contradiction`）全向下兼容

### 6.2 从 v1.x 升级

1. 备份当前版本目录
2. 替换 Skill 目录
3. 运行测试套件验证完整性
4. 确认 SKILL.md ↔ manifest.yaml 双绑一致
5. 跑现有测试套件，确保回归通过

### 6.3 发布前验证清单

发布到外部 Skill 平台前，建议运行：

```bash
# SKILL.md 完整性（发布前人工检查）
# 双绑一致性（发布前人工检查）
python -m pytest tests/ -v                # 测试套件全绿
```

---

## 7. 路线图

### 7.1 短期（按需触发）

- 性能监控（perf_baseline 漂移 > 10% 启动 PATCH）
- 用户反馈闭环（沉淀 v3.0.1 需求）

### 7.2 中期（6 个月）

- 多模态扩展（图片 / 视频 / 音频内容理解）
- 更智能的实体识别（跨语言 + 跨模态 NER）
- 跨工具编排（与搜索 / 验证 / 归档等工具深度整合）

### 7.3 长期（12 个月+）

- 本地文件集成（按用户反馈触发，不预设）
- 实时协作（多人调研会话 + 共享知识库）
- AI Agent 深度协作（作为可观测的子任务被组合）

### 7.4 设计哲学（不做）

- ❌ 实时新闻监控（→ news-search 类工具）
- ❌ 学术文献综述（→ paper-lookup 类工具）
- ❌ 浏览器自动化爬取（→ firecrawl 类工具）
- ❌ 即时聊天对话（→ 对话工具）

---

## 8. 触发词

按 **场景 / 技术 / 能力** 三类组织，便于不同检索维度匹配。

### 8.1 场景类（业务用途）

`行业调研` · `趋势分析` · `工艺技术研究` · `内容采集` · `信息收集` · `竞品分析` · `市场研究` · `报告生成` · `存档归档` · `长期知识库`

### 8.2 技术类（API / 协议）

`URL 去重` · `MCP 集成` · `内容摘要` · `中文文本分析` · `链式引用追踪` · `跨源冲突检测` · `领域 Skill 矩阵` · `多平台导出` · `模板化报告` · `跨语言实体识别` · `多模型 LLM 路由`

### 8.3 能力类（具体函数 / 类名）

`实体自沉淀` · `频次统计` · `Wikidata 同步` · `新鲜度 cron` · `批量入库` · `别名 JSON 持久化` · `streaming_research` · `async_research` · `ConflictMonitor` · `detect_conflicts_v3_async` · `score_contradiction` · `EntityGraph` · `predict_heat` · `trace_entity` · `EntityProfile` · `ClaimStore` · `WikidataSync` · `FreshnessCron` · `build_traced` · `to_dot` · `llm_call`

---

## 9. 配套文档

| 文档 | 路径 | 用途 |
|------|------|------|
| README | `README.md` | 快速导航 + 5 秒看懂 |
| RELEASE_NOTES | `RELEASE_NOTES.md` | 版本发布说明 |
| 核心库 | `core/` | 22 功能模块（实体 / 评分 / 矛盾 / 爬取等；含 state_dir 运行时数据目录解析） |
| 适配层 | `scripts/` | MCP server + 工具脚本 |
| 引用契约 | `references/` | 命名 / 评分 / 归档 / MCP 集成等 8 份 |
| 报告模板 | `domains/templates.yaml` | 5 领域 Jinja2 模板 + default（块标量合并，v2.0.0 起替代 .j2 文件） |
| 领域配置 | `domains/*.yaml` | 5 领域（tech / market / finance / policy / competitor） |
| 测试套件 | `tests/` | 功能验证（发布前运行） |
| 双绑检查 | manifest.yaml | SKILL.md ↔ manifest.yaml 一致性（自动生成） |

### 9.1 引用契约清单

| 契约 | 用途 |
|------|------|
| `references/Infoseek_Anchor_Score五维契约_v1.5.md` | 评分公式 + 门控规则 |
| `references/Infoseek_MCP集成契约_v1.5.md` | MCP 协议 + 25 工具 schema |
| `references/Infoseek_维度命名契约_Naming_Convention.md` | 人物 6 维分桶（其他人物类工具对齐） |
| `references/Infoseek_存档归档契约_Archive_Convention.md` | 归档目录结构 + 命名格式 |
| `references/Infoseek_URL标准化契约_URL_Normalization.md` | URL 标准化规则（去重） |
| `references/anchor-adapter.md` | 锚点 → 意图卡片转换层 |
| `references/credential-tools.md` | Tier 2.5 凭证工具选型 |
| `references/risk-register.md` | 14 项风险 RPN 矩阵 + 监控计划 |
| `references/trusted-sources.json` | 5 领域 × tier1-4 信任源白名单 |

---

## 10. 测试与质量

- **测试套件**：13 个测试文件，121 用例，100% PASS
  - 核心功能：`test_infoseek_v240.py` / `test_infoseek_v231.py`
  - 质量维度：`test_boundary_v240.py` / `test_compat_v240.py` / `test_correctness_v240.py` / `test_reliability_v240.py` / `test_security_v240.py` / `test_stability_v240.py`
  - 端到端：`test_e2e_scenarios_v240.py`
  - v3.0 新特性：`test_streaming_v300.py` / `test_mcp_v300_beta.py` / `test_async_tools.py`
- **性能基线**：`perf_baseline_v300.json`（8 指标 P50/P95 + L5 阈值达标）
- **质量门控**：边界 / 兼容 / 正确性 / 可靠性 / 安全性 / 稳定性 6 维度全覆盖

---

## 附录：发布到 Skill 平台

### A.1 平台支持

Infoseek 已通过 Skill 注册流程发布到 ima.copilot 平台（Skill ID 由平台分配，注册后可在 Skill 列表查询）。

### A.2 客户端集成示例

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

### A.3 获取帮助

- 查看 `README.md` 5 秒看懂
- 查看 `RELEASE_NOTES.md` 了解版本演进
- 查看 `references/` 下契约文档