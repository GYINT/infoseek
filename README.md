# Infoseek

> 端到端内容智能采集与调研工作流。把"信息发现 + 内容采集 + 智能融合"封装成可复用的调研流水线。

[![Version](https://img.shields.io/badge/version-3.0.0-blue)](#)
[![MCP](https://img.shields.io/badge/MCP-25%20tools-blueviolet)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](#)

---

## 5 秒看懂

```python
from infoseek_core_v2 import streaming_research

# 流式研究：逐步 yield 中间结果（评分 → 实体 → 矛盾 → 报告）
async for partial in streaming_research("AI Agent", lite=True):
    print(partial["step"], "...")  # score_complete / wikidata_complete / ...
```

```bash
# MCP server（25 工具）
python scripts/infoseek_mcp_server.py
```

---

## 能力总览

| 能力 | 说明 |
|------|------|
| 🔍 **多源嗅探** | 关键词展开 → web/kb/note 多渠道并行检索 |
| ⚖️ **四维评分门控** | 可信度 40% + 主题一致性 30% + 互动深度 20% + LLM 可读性 10% |
| 🕸️ **深度抓取** | 4 级降级：静态 HTML → 反爬兜底 → 凭证辅助 → 多媒体处理 |
| ⚡ **智能融合** | 跨源语义矛盾检测 + 实体识别（95+ 词典、跨语种、别名归并） |
| 📄 **结构化报告** | Markdown 报告（观点 + 数据 + 来源） |
| 🗄️ **长期归档** | `[归档]` 指令触发，落盘 `infoseek-archives/<subject>/` + 主题 README |

**3 种使用方式**：Python SDK（同步/异步/流式）/ MCP 工具（25 个）/ 批量流水线。

---

## 快速上手

### 1. Python SDK

```python
from infoseek_core_v2 import research, async_research, streaming_research

# 同步
res = research("国产开源大模型", lite=True)

# 异步
import asyncio
res = asyncio.run(async_research("AI Agent 框架对比", lite=True))

# 流式
async for partial in streaming_research("DeepSeek V3", lite=True):
    print(partial["step"], "...")
```

### 2. MCP 集成

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

**工具列表（25 个）**：
- 同步工具 11 个：`search_anchors` / `fetch_content` / `save_archive` / `check_dedup` / `dedup_stats` / `fuse_analysis` / `cross_subject_analysis` / `summarize_content` / `conflict_detection` / `score_source` / `research`
- 核心工具 3 个：`research_v3` / `research_stream` / `score_contradiction`
- 异步工具 11 个：`*_async` 包装（如 `research_v3_async`）

### 3. 长期知识库

```python
# 调研指令加 [归档]，自动沉淀
res = research("AI Agent 行业 2026 Q1 [归档]", lite=True)
# → infoseek-archives/AI_Agent_行业_2026_Q1/ + 主题 README
```

---

## 工作流

```
输入主题
   ↓
① 锚点发现：关键词展开 → 多源嗅探 → 四维评分门控
   ↓
② 内容采集：URL 预检 → 标准化去重 → 4 级降级提取
   ↓
③ 智能融合：矛盾检测 → 实体识别 → 跨源融合
   ↓
④ 输出报告：Markdown（观点 + 数据 + 来源）
   ↓
⑤ 可选归档：落盘 + 主题 README + 长期沉淀
```

---

## 目录结构

```
infoseek/
├── SKILL.md               # Skill 定义（本文件）
├── manifest.yaml          # 平台 manifest
├── README.md              # 本文件
├── .mcp.json              # MCP server 配置
├── core/                  # 核心库（35+ 模块）
│   ├── anchor_score_v2.py # 锚点评分算法
│   ├── entities.py        # 跨语言实体词典（95+）
│   ├── ner.py             # 命名实体识别
│   ├── llm_router.py      # 多模型 LLM 路由
│   ├── conflict_v3.py     # 跨源矛盾检测
│   ├── entity_graph.py    # 实体图谱
│   └── ...                # 35+ 模块
├── scripts/               # 适配层（MCP server + 工具脚本）
│   ├── infoseek_mcp_server.py   # MCP server（25 工具）
│   ├── infoseek_core_v2.py      # 核心 API 入口
│   ├── infoseek_pipeline.py     # 批量调研流水线
│   ├── domain_orchestrator.py   # 领域调度 + 模板渲染
│   └── ...
├── domains/               # 领域配置（tech/market/finance/policy/competitor）
│   ├── *.yaml             # 领域 profile（信任源 + 关键词模板）
│   └── templates.yaml     # 报告模板（6 模板块标量合并）
└── references/
    └── trusted-sources.json  # 可信资源知识库（web 搜索补充）
```

---

## 环境变量

| 变量 | 用途 |
|------|------|
| `OPENCLAW_WORKSPACE` | 工作目录（默认 `~/infoseek`） |
| `INFOSEEK_ROOT` | Skill 根目录（默认脚本所在目录上级） |
| `INFOSEEK_DB` | 数据库路径（默认 `~/.infoseek/infoseek_db.json`） |
| `INFOSEEK_ARCHIVE` | 归档目录（默认 `~/infoseek-archives`） |
| `DEEPSEEK_API_KEY` / `KIMI_API_KEY` / `OPENAI_API_KEY` 等 | LLM provider 凭据（可选，无 key 时降级 mock 模式） |

---

## 适用 / 不适用

✅ 行业调研 / 趋势分析 / 竞品分析 / 市场研究 / 技术研究 / 内容采集 / 报告生成 / 长期知识库建设

❌ 实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话
