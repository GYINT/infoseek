# Infoseek Anchor Score 评分契约（五维 + 兜底）

> 版本：v1.0.1 ｜ 状态：✅ 已提供 ｜ 对齐全：`scripts/infoseek_core_v2.py` / `core/anchor_score_v2.py` / `scripts/anchor_adapter.py`

## 1. 评分公式（v1.5.0 五维，v2.0.0 合并信任源）

```
Anchor_Score = 互动深度×20% + 主题一致性×30% + 来源可信度×40% + LLM 上下文可读性×10%
```

完整链路（`anchor_score_v2.py` 逐步实现）：

```
base_score（四维 0-100）
  → 白名单复活（base ≥90 时保底 70）
  → 时间衰减（days_since_published 因子）
  → 跨平台第 6 维（可选，占 5%）
  → Jaccard 语义第 8 维（可选，占 5%）
  → Trust 信任源加权（tier1-4 白名单，0-30 分）
  → Domain 领域加权（可选，0-20 分）
  → clamp 到 0-100
```

## 2. 门控规则

| 分数 | 分类 | 处理 |
|------|------|------|
| ≥70 | 🟢 核心 | 自动进入采集队列 |
| 40-69 | 🟡 潜力 | 需人工确认 |
| <40 | ❌ 噪声 | 过滤 |

## 3. v1.0.1 语义兜底（P0-1 修复）

真实搜索源（`search_web` 返回 `url/title`，无 `interaction/topic_match/credibility` 三字段）时：

```
base_score = max(
    compute_semantic_similarity(title+snippet, subject),   # Jaccard 三跑关键词
    int(string_containment(title+snippet, subject) × 0.8)  # 主题词包含比例
)
```

- 三字段齐全 → 走五维原路径（不触发兜底）
- source 自带 `score` 字段 → 尊重该值（`base_score <= 0` 才兜底）

## 4. 信任源加权（core/trust_sources.py）

| Tier | 权重 | 来源 |
|------|------|------|
| 1 官方权威 | 25-30 | gov / arxiv / iso / astm / 知网 / ieee 等 |
| 2 行业头部 | 15-25 | 头部企业 / 官方媒体 / zhihu / github 等 |
| 3 一般可信 | 5-15 | 虎嗅 / 钛媒体 / 财新 等 |
| 4 低优先 | 0-5 | 未命中白名单（默认） |

领域：`tech-research` / `market-research` / `finance-research` / `policy-research` / `competitor-intel` / `general`。

## 5. 兼容性

- v2 API `score_source()` 与 v1 `calculate_score()` 行为一致（v2 不修改 source dict、不返回 version 字段）
- `method='tfidf'` 已废弃 → 内部重定向到 Jaccard（DeprecationWarning）
