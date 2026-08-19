# Infoseek

> 端到端内容智能采集工作流。**v1.0.0 已正式发布**（最终对外版本）。

[![Status](https://img.shields.io/badge/status-GA%20stable-brightgreen)](#)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](#)
[![Tests](https://img.shields.io/badge/tests-121%2F121%20PASS-success)](#)
[![MCP](https://img.shields.io/badge/MCP-25%20tools-blueviolet)](#)

---

## 5 秒看懂

```python
from infoseek_core_v2 import streaming_research

# 流式研究：first yield 仅 4ms（vs v2.7.3 一次性 ~460ms，115x 提速）
async for partial in streaming_research("AI", sources, lite=True):
    print(partial["step"], "...")  # 7 步 yield
```

```bash
# MCP server 25 工具（11 sync + 11 async + 3 v3.0 核心）
python scripts/infoseek_mcp_server.py
```

---

## 🎉 v3.0.0 GA 重大亮点

| 能力 | 数字 |
|------|------|
| 🚀 **streaming_research() AsyncIterator** | first yield **4ms**（115x vs v2.7.3） |
| 🛠️ **MCP server 工具** | **25** 个（11 sync + 11 async + 3 v3.0 核心） |
| ✅ **完整回归测试** | **121/121 PASS**（v2.3.1/v2.4.0/L1-L7/E2E/streaming/MCP） |
| 🔧 **全面 asyncio 化** | detect_conflicts_v3_async + ConflictMonitor.ingest_*_async |
| ⏸️ **兼容策略** | 0 破坏性变更，research() 12 个月 deprecated 缓冲 |

---

## 快速上手

### 1. 安装 / 升级

```bash
pip install --upgrade infoseek==3.1.0
```

### 2. Python SDK

```python
from infoseek_core_v2 import (
    research,           # 同步（v1.x 兼容，v3.0+ deprecated 12 月）
    async_research,     # 异步（v2.5+ 推荐）
    streaming_research, # v3.0 流式（推荐目标）
)

# 同步用法
res = research("AI", sources, lite=True)
# res['version'] == '3.1.0'

# 流式用法
async for partial in streaming_research("AI", sources, lite=True):
    # partial['step'] ∈ {score_complete, wikidata_complete, ...}
    pass
```

### 3. MCP 集成

项目提供 `.mcp.json`（双服务器配置，可直接被 Claude/Codex 等客户端加载）：

```json
{
  "mcpServers": {
    "infoseek-search": {
      "command": "${INFOSEEK_ROOT}/scripts/infoseek_mcp_server.py",
      "args": ["--transport", "stdio"],
      "env": {
        "INFOSEEK_ROOT": "${INFOSEEK_ROOT}",
        "INFOSEEK_DB": "${HOME}/.infoseek/infoseek_db.json",
        "INFOSEEK_ARCHIVE": "${HOME}/infoseek-archives"
      }
    },
    "infoseek-archive": {
      "command": "${INFOSEEK_ROOT}/scripts/infoseek_archive_server.py",
      "args": ["--transport", "stdio"],
      "env": {
        "INFOSEEK_ROOT": "${INFOSEEK_ROOT}",
        "INFOSEEK_DB": "${HOME}/.infoseek/infoseek_db.json",
        "INFOSEEK_ARCHIVE": "${HOME}/infoseek-archives"
      }
    }
  }
}
```

> 💡 Windows 环境请将 `command` 改为 `python3` + `args: ["脚本路径", "--transport", "stdio"]`（不依赖可执行位）。

**工具列表（25）**：
- 同步工具 11 个：`search_anchors` / `fetch_content` / `save_archive` / `check_dedup` / `dedup_stats` / `fuse_analysis` / `cross_subject_analysis` / `summarize_content` / `conflict_detection` / `score_source` / `research`
- 核心工具 3 个：`research_v3` / `research_stream` / `score_contradiction`
- 异步工具 11 个：`*_async` 包装（如 `research_v3_async`）

> ℹ️ 两个服务器均暴露读写工具（search 含 save_archive/dedup_stats，archive 复用同组）。若需严格只读隔离，可在客户端侧按需配置权限。

---

## 文档导航

| 文档 | 用途 |
|------|------|
| [SKILL.md](SKILL.md) | Skill 完整定义（概念/能力/触发词） |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | 版本发布说明 |
| [requirements.txt](requirements.txt) | 运行时依赖清单（pip install -r） |
| [tests/](tests/) | 测试套件（13 个核心测试） |

> ℹ️ **冷启动说明**：运行时状态（`claims.json`、`entity_aliases.json`、`pending_entities.json`、`anchor_db.json` 等）首跑为空占位，运行后随调研逐步积累（跨会话声明 / 别名库 / 锚点缓存）。这些文件**不再写入技能源码目录**，而是落在运行时数据目录（默认 `~/.infoseek/`，可由环境变量 `INFOSEEK_DATA_DIR` 或 `INFOSEEK_DB` 所在目录配置），技能更新不会丢失数据。详见 `core/state_dir.py`。

---

## 版本路线

| 版本 | 状态 | 备注 |
|------|------|------|
| v1.0.0 | 🟢 **GA stable** | **最终对外发布版本** |
| ~~v3.1.0~~ | 🔴 已取代 | 旧版本线，由 v1.0.0 取代 |
| 后续 PATCH | 🟡 待办 | 收集反馈后规划 |
| ~~v3.6.0~~ | 🔴 取消 | 并入 v1.0.0 版本线 |
| ~~v4.0.0~~ | 🔴 取消 | 并入 v1.0.0 版本线 |

---

## 测试矩阵

| 套件 | 用例 | 类别 |
|------|------|------|
| test_infoseek_v231.py | 10 | v2.3.1 回归 |
| test_infoseek_v240.py | 15 | v2.4.0 回归 |
| test_boundary_v240.py | 12 | L2 能力边界 |
| test_compat_v240.py | 5 | L7 兼容性 |
| test_correctness_v240.py | 18 | L1 正确性 |
| test_reliability_v240.py | 11 | L3 可靠性 |
| test_security_v240.py | 5 | L6 安全 |
| test_stability_v240.py | 9 | L4 稳定性 |
| test_e2e_scenarios_v240.py | 12 | E2E 实战 |
| test_streaming_v300.py | 6 | v3 streaming |
| test_mcp_v300_beta.py | 7 | v3 MCP |
| test_async_tools.py | 11 | async 包装 |
| **合计** | **121** | **100% PASS** |

---

## 项目结构

```
infoseek/
├── SKILL.md            # Skill 定义（yfm + 文档）
├── manifest.yaml       # 平台 manifest
├── RELEASE_NOTES.md   # 版本发布说明
├── README.md           # 本文件
├── core/               # 核心库（22 功能模块 + __init__；运行时状态经 state_dir 落 ~/.infoseek）
│   ├── trust_sources.py
│   ├── entity_graph.py
│   ├── entity_heat.py
│   ├── entity_profile.py
│   ├── entity_trajectory.py
│   ├── contradiction_scorer.py
│   ├── conflict_v3.py
│   ├── wikidata_sync.py
│   ├── freshness_cron.py
│   └── ...
├── scripts/            # 适配层 + MCP server
│   ├── infoseek_core_v2.py    # v2 API 入口（含 streaming_research）
│   ├── infoseek_mcp_server.py # MCP server 25 工具
│   └── ...
├── references/         # 引用契约
│   ├── Infoseek_维度命名契约_Naming_Convention.md
│   ├── Infoseek_Anchor_Score五维契约_v1.5.md
│   └── ...
├── domains/            # 领域配置（tech/market/finance/policy/competitor）
│   ├── *.yaml          # 领域 profile（信任源 + 关键词模板）
│   └── templates.yaml  # 报告模板（6 模板块标量合并，替代旧 .j2 文件）
```

---

## 贡献与反馈

- **Bug 报告**：附 `infoseek --version` + 最小复现
- **性能问题**：附 `perf_sample_v300.py` 输出 + `perf_baseline_v300.json` 对比
- **功能请求**：附用例 + 期望输出

---

> v1.0.0 | 工具面收敛 25→13 | 多生态（ima/Claude/Codex/Dify/Coze） | 最终对外版本