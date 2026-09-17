# Infoseek

> 端到端内容智能采集与调研工作流。**v1.5.0 发布版**。

[![Status](https://img.shields.io/badge/status-GA%20stable-brightgreen)](#)
[![Version](https://img.shields.io/badge/version-1.5.0-blue)](#)
[![Tests](https://img.shields.io/badge/tests-25%20suites%20PASS-success)](#)
[![MCP](https://img.shields.io/badge/MCP-15%20tools-blueviolet)](#)

---

## 5 秒看懂

```python
from infoseek_core_v2 import streaming_research

# 流式研究：lite 模式 7 步 yield，秒级完成
async for partial in streaming_research("AI", sources, lite=True):
    print(partial["step"], "...")  # 7 步 yield
```

```bash
# MCP server（15 规范工具：2 研究核心 + 11 异步 + 2 Key 管理）
python scripts/infoseek_mcp_server.py
```

---

## 🎉 v1.5.0 发布亮点

| 能力 | 说明 |
|------|------|
| 🧭 **搜索引擎全生命周期** | 健康状态机 / 配额追踪 / 认证粘滞 + 新鲜度自愈（配额重置、冷却恢复、API 漂移检测）+ CLI engine-status/reconcile/probe |
| 🎯 **搜索召回增强** | query 别名扩展 / 跨引擎多样性轮询（防单源垄断）/ 自适应相关性门槛 / 动态层权重 |
| 🕸️ **4 级抓取** | L1 静态 → L2 浏览器渲染 → L3 凭证辅助（KeyManager 注入，仅内存）→ L4 多媒体 chunk（whisper 可选降级） |
| 🔑 **Key 管理** | 归一化 Key 管理（多后端 / 熔断 / 多 key 池 / 配额 / keyring / CLI 16 子命令） |
| ⚡ **perf 10k 基准** | 10k 源近线性扩展（评分 139s / 冲突 89s / research 97s） |
| ✅ **回归测试** | **25 套件**全绿 + 质量基线 26/26 all_ok |

---

## 快速上手

### 1. 安装 / 升级

```bash
pip install -r requirements.txt            # 核心 + 文本分析 + 可选 LLM
pip install -r requirements-extra.txt      # playwright（L2/L3 浏览器抓取，可选）
# 完整依赖说明见 references/external-deps.md
```

### 2. Python SDK

```python
from infoseek_core_v2 import (
    research,           # 同步（兼容入口）
    async_research,     # 异步（推荐）
    streaming_research, # 流式（推荐目标）
)

res = research("AI", sources, lite=True)
async for partial in streaming_research("AI", sources, lite=True):
    pass  # partial['step'] ∈ {score_complete, wikidata_complete, ...}
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
    }
  }
}
```

> 💡 Windows 环境请将 `command` 改为 `python3` + `args: ["脚本路径", "--transport", "stdio"]`。

**工具列表（15 规范 + 12 兼容并存）**：
- 研究核心 2 个：`research_v3` / `research_stream`
- 异步工具 11 个：`search_anchors_async` / `fetch_content_async` / `save_archive_async` / `check_dedup_async` / `dedup_stats_async` / `fuse_analysis_async` / `cross_subject_analysis_async` / `summarize_content_async` / `conflict_detection_async` / `score_source_async` / `score_contradiction_async`
- Key 管理 2 个：`manage_keys`（list/stat/rotate/revoke，脱敏）/ `key_usage`（用量成本报表）
- 兼容并存期 12 个：11 个同步工具 + `research`（附 `deprecated` 标记）

---

## 预留接口（可选扩展 · 默认关闭）

> 原则：**默认零风险、扩展按需开启**。以下能力**默认不启用**；接口与开关语义已定义，
> 供你按需选择。如需启用，按表中说明配置，或提出需求由我们按你的策略实现
> （启用不改变其他默认行为）。

### 1. 搜索层引擎请求缓存（预留 · 默认关闭）

**默认行为（现状）**：**不缓存**。每次搜索都实时请求搜索引擎端点
（DuckDuckGo / Bing-RSS / Jina / Wikipedia / 智谱 / CN-Web …），保证结果时效性。

> 注：**目标网页内容抓取**（`tool_fetch_content` 链路）**已接入** `http_cache.py`
> （LRU + TTL + 容量上限，默认存 `~/.infoseek/http_cache/`），与本节无关。

| 项 | 值 |
|---|---|
| 预留开关 | `INFOSEEK_SEARCH_HTTP_CACHE`（`0` = 关闭·默认 / `1` = 开启）|
| 预留 TTL | `INFOSEEK_SEARCH_HTTP_CACHE_TTL`（秒，建议 60–300）|
| 生效范围 | 搜索层 `infoseek_pipeline._http_get()` 的引擎端点请求 |

**取舍（请选择）**：缓存引擎请求可**省流量、抗限流、加速降级**，但会**直接返回陈旧搜索结果**。
各引擎时效特性不同（DuckDuckGo / Bing 变化快，Wikipedia 较稳定），建议启用时按引擎分级 TTL，
或仅在「失败重试 / 降级」场景缓存。

### 2. 知识库（KB）接线（预留 · 默认未接线）

**默认行为（现状）**：infoseek 为**闭环采集器** —— 调研产物落本地（archive / dedup），
**不从外部知识库取信源，也不回写知识库**。

> 注：`trusted_kb.py`（可信资源库）与 `core/entity_profile.py`（实体画像）为 skill
> **内部**可信库，与知识库平台无关。

| 预留方向（请选择其一） | 含义 | 规划接口 |
|---|---|---|
| A. KB → infoseek | 知识库作为信源召回，与 web 结果混排 | `INFOSEEK_KB_ID` + 检索 API |
| B. infoseek → KB | 调研报告自动归档进知识库 | `INFOSEEK_KB_ID` + 新建 / 写入文档 API |
| C. 双向 | A + B | 同上 |

**启用前需确定**：知识库 ID（`kb_id`）、写入格式（Markdown / 文件）、去重与更新策略。

**如何选择**：提供 kb_id 与方向（A / B / C），我们按契约接线；未选择时保持默认
（不接线、零行为变化）。

---

## 搜索层并发与聚合窗口（G5 · 默认开启）

> 搜索层默认**全引擎并发 + 聚合窗口**：并发上限 12、窗口 8s（仅对超慢引擎生效）。

| 参数 | 默认 | 说明 |
|---|---|---|
| `INFOSEEK_SEARCH_MAX_WORKERS` | `12` | 进程级常驻池并发上限（懒创建，不浪费线程）|
| `INFOSEEK_SEARCH_WINDOW_MS` | `8000` | 聚合窗口；到期即聚合返回，未达引擎结果丢弃；`0` = 关闭（回退原语义）|
| `INFOSEEK_SEARCH_EARLY_FACTOR` | `1.5` | 提前收敛倍数：已完成引擎去重结果 ≥ max_results×factor 即提前返回；`0` = 关闭 |
| `INFOSEEK_SEARCH_SHARED_BUDGET` | `1` | 层间共享总预算（AI 层与默认层共享一个窗口，总延迟有上界）；`0` = 各层各耗窗口 |
| `INFOSEEK_SEARCH_TOTAL_BUDGET_MS` | `= WINDOW_MS` | 层间共享总预算时长（覆盖默认窗口值）|
| `INFOSEEK_SEARCH_OVERRUN_LIMIT` | `2` | 连续超窗次数阈值；达阈值 → 引擎临时降权（暂停拉起）；`0` = 关闭降级 |
| `INFOSEEK_SEARCH_OVERRUN_MUTE_S` | `60` | 降权冷却秒；到期自动复活（计数清零重新积累）；`0` = 静默到进程结束 |

**行为**：
- **常驻池**：线程池进程级复用，单次搜索退出不再 `shutdown(wait=True)` 阻塞 → 层耗时不再等于最慢引擎。
- **聚合窗口**：`wait(timeout=window)` 到期即返回；**正常场景（引擎均 < 窗口）零影响** —— 全部完成即立即返回，
  仅截断病态慢引擎（实测：3s 慢引擎 + 快引擎，窗口 400ms → **0.8s** 返回完整快引擎结果）。
- **提前收敛（v1.6.1）**：轮询 `FIRST_COMPLETED`，已完成引擎结果 ≥ max_results×1.5 即提前返回（不等满窗）——
  高产出批次（如 5 引擎×10 条）最快 0.05s 返回，且不丢已完成结果。
- **超窗引擎（v1.6.1）**：结果丢弃，健康记录仍由线程内正常完成（**不误杀引擎**）；连续超窗达阈值
  → 临时降权（`_filter_muted` 不再拉起该引擎），冷却后自动复活 —— 慢引擎自我淘汰，不拖累整链。
- **保留兜底入预算（v1.6.1）**：主并行不足 `min_expected` 时兜底，保留引擎**并行提交并纳入剩余预算**
  （原 0.8s×N 串行兜底移除）；预算耗尽（剩余 ≤0.05s）则跳过兜底，延迟上界优先。
- **层间共享总预算（v1.6.1）**：AI 层与默认层共享一个 deadline（总预算 = WINDOW 或 TOTAL_BUDGET_MS）——
  AI 层未耗完则默认层继承剩余预算（结果不丢）；AI 层耗完则默认层快速失败，**总延迟不再两层叠加**；
  层间 0.8s 限速在剩余预算不足时自动跳过（预算即节流）。
- **一键回退**：`INFOSEEK_SEARCH_WINDOW_MS=0` 回到旧行为。`INFOSEEK_SEARCH_SHARED_BUDGET=0`
  `INFOSEEK_SEARCH_EARLY_FACTOR=0` `INFOSEEK_SEARCH_OVERRUN_LIMIT=0` 分别关闭 P1/P2 各子能力。

---

## 文档导航

| 文档 | 用途 |
|------|------|
| [SKILL.md](SKILL.md) | Skill 完整定义（概念/能力/触发词） |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | 版本发布说明 |
| [requirements.txt](requirements.txt) | 运行时依赖清单 |
| [references/external-deps.md](references/external-deps.md) | 外部依赖清单 + 作用 + 降级路径 |
| [references/api-keys.md](references/api-keys.md) | 外部 API Key 清单 + 效益 + 获取 |
| [references/ROADMAP.md](references/ROADMAP.md) | 当前基线 · 待办 · 前景方向（纯净版） |
| references/ROADMAP_archive_20260917.md | v1.0.0→v2.0.0 历史实施记录归档 |
| [tests/](tests/) | 测试套件（25 标准 + deep，run_tests.py 聚合） |

> ℹ️ **冷启动说明**：运行时状态（`claims.json`、`entity_aliases.json`、`pending_entities.json`、`anchor_db.json`、`engine_state.json` 等）首跑为空占位，运行后随调研逐步积累。这些文件**不写入技能源码目录**，落在运行时数据目录（默认 `~/.infoseek/`，可用 `INFOSEEK_DATA_DIR` 覆盖），技能更新不丢数据。详见 `core/state_dir.py`。

---

## 版本路线

| 版本 | 状态 | 备注 |
|------|------|------|
| v1.5.0 | 🟢 **当前发布版** | 身份归因能力链 P0：发现→验证闭环 + MCP 工具 + 主链集成（合规双闸） |
| v1.4.1 | ✅ 历史 | 能力里程碑：引擎生命周期 / 召回增强 / 4 级抓取 / Key 管理 / perf 10k |
| v1.0.1 | ✅ 历史 | 审计 G1–G13 + ABC 能力增强 + 引擎生命周期 P0–P3 |
| v1.0.0 | ✅ 历史 | 工具面收敛 + 搜索引擎降级链重写 |
| 后续 | 🟡 待办 | 见 `references/ROADMAP.md` |

---

## 测试矩阵

> ⚠️ 用例数为各套件自报（PASS 计数）。运行入口：`python tests/run_tests.py`（脚本风格，勿用 pytest）。

| 套件 | 用例 | 类别 |
|------|------|------|
| test_infoseek_v231.py | 10 | 回归 |
| test_infoseek_v240.py | 15 | 回归 |
| test_boundary_v240.py | 12 | 能力边界 |
| test_compat_v240.py | 5 | 兼容性 |
| test_correctness_v240.py | 18 | 正确性 |
| test_reliability_v240.py | 11 | 可靠性 |
| test_security_v240.py | 5 | 安全 |
| test_stability_v240.py | 9 | 稳定性 |
| test_e2e_scenarios_v240.py | 12 | E2E 实战 |
| test_streaming_v300.py | 6 | 流式 |
| test_search_engines.py | 16 | 搜索链 |
| test_tools_surface.py | 11 | 工具面 |
| test_async_tools.py | 11 | async 包装 |
| test_domain_orchestrator_v200.py | 13 | 领域编排 |
| test_v174_jaccard.py | 3 | Jaccard 兼容 |
| test_qcm_bridge_v101.py | 10 | QCM 协同 |
| test_key_manager_v101.py | 29 | Key 管理 |
| test_engine_lifecycle_v101.py | 40 | 引擎生命周期 |
| test_freshness_cron_v101.py | 23 | 新鲜度 cron |
| test_recall_enhance_v101.py | 16 | 召回增强 |
| test_fetch_levels_v101.py | 26 | 4 级抓取 |
| test_deep_v101.py | 22 | 深度（单独运行） |

> 环境差异说明：`test_stability_v240.py` 在 POSIX 环境含内存维度（ru_maxrss）；Windows 下自动跳过内存判定，不误报 FAIL。

---

## 项目结构

```
infoseek/
├── SKILL.md            # Skill 定义（yfm + 文档）
├── manifest.yaml       # 平台 manifest
├── RELEASE_NOTES.md   # 版本发布说明
├── README.md           # 本文件
├── core/               # 核心库（22 功能模块；运行时状态经 state_dir 落 ~/.infoseek）
│   ├── conflict_v3.py / contradiction_scorer.py
│   ├── entity_*.py     # graph/heat/profile/trajectory/tracker/aliases
│   ├── wikidata_sync.py / freshness_cron.py / claim_store.py
│   ├── key_manager.py / llm_router.py / ner.py / trust_sources.py
│   └── state_dir.py
├── scripts/            # 适配层 + MCP server
│   ├── infoseek_core_v2.py       # 核心 API（research/async/streaming）
│   ├── infoseek_mcp_server.py    # MCP server 门面（15 规范工具）
│   ├── mcp_tools_*.py            # 工具模块（search/archive/analysis/keys/async/common/qcm）
│   ├── infoseek_pipeline.py      # 搜索降级链 + 召回增强
│   ├── engine_lifecycle.py       # 搜索引擎全生命周期
│   ├── infoseek_keys_cli.py      # keys CLI（16 子命令）
│   └── ...
├── references/         # 契约 + 配置 + 依赖/Key/路线图
├── domains/            # 领域配置（tech/market/finance/policy/competitor）
│   ├── *.yaml          # 领域 profile
│   └── templates.yaml  # 报告模板（块标量合并）
├── tests/              # 测试套件（25 标准 + deep）
└── dist/               # 质量基线 + perf 基准 + 生态构建产物
```

---

## 贡献与反馈

- **Bug 报告**：附 `infoseek --version` + 最小复现
- **性能问题**：附 `dist/quality_baseline.json` 对比
- **功能请求**：附用例 + 期望输出
- **路线图**：见 `references/ROADMAP.md`

---

> v1.5.0 | 身份归因能力链 P0（发现→验证→MCP→主链）| 多生态（ima/Claude/Codex/Dify/Coze）| MIT License
