# Infoseek Changelog
> 所有版本变更遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范（Added/Changed/Deprecated/Removed/Fixed/Security）。
> 版本号遵循 [Semantic Versioning](https://semver.org/) MAJOR.MINOR.PATCH。

## [3.1.0] — 2026-08-17

### Changed
- 版本号 3.0.0 → 3.1.0（PATCH 级更新，内容与 v3.0.0 一致）
- SKILL.md frontmatter 版本同步更新

### Fixed
- 环境重置恢复：补齐 CHANGELOG.md / scripts/sync_manifest.py / scripts/validate_skill.py / LICENSE

---

## [3.0.0] — 2026-08-12

### Added (GA 发布)
- **streaming_research() AsyncIterator**：7 步 yield（score_complete → wikidata_complete → ...），first yield 4ms（vs v2.7.3 一次性 ~460ms，115x 提速）
- **全面 asyncio 化**：`detect_conflicts_v3_async` + `ConflictMonitor.ingest_*_async`
- **MCP server 25 工具**：11 sync + 11 async + 3 v3.0 核心（`research_v3` / `research_stream` / `score_contradiction`）
- **完整回归测试 121/121 PASS**（v2.3.1/v2.4.0/L1-L7/E2E/streaming/MCP/async）

### Changed
- `research()` 标记 deprecated（保留 12 个月缓冲，v3.6.0 强制迁移，v4.0.0 移除）
- v2.7.x 转为 LTS（12 个月维护）

### Fixed
- 0 破坏性变更（兼容策略：0 breaking changes）

---

## [2.4.1] — 2026-08-08

### Fixed (P0 缺陷修复)
- **DEF-F 核心别名归并失效**：`core/entity_aliases.py:105` 改相对导入 `from entities import get_all_entities`，恢复 alias_map 186 条命中
- **DEF-C predict_heat 容错**：`core/entity_heat.py` 对 `_find_entity` / `claim_store.get_claims` 加 try-catch 降级
- **DEF-D contradiction_scorer 容错**：`_extract_slots` 加 try-except 返回空集合
- **DEF-E research() 性能 28x 退化**：加 `lite: bool = False` 参数跳过 wikidata/traced_export/heat/trajectory；EntityAliases 单例化 + 模块级缓存

### Changed
- 测试通过率：90.0% → 97.4%（78 用例 76 PASS）
- 性能基线 v2.4.1：research(lite=True) 10 源 P95=750ms（vs v2.4.0 28s）

---

## [2.4.0] — 2026-08-08

### Added
- 语义矛盾评分（contradiction_scorer：共享事实槽 + 否定/反义词典 + 极性放大，severity 四档）
- 跨会话历史冲突（claim_store 比对 + cross_session 字段 + severity 升级）
- 实体轨迹（entity_trajectory 跨 research 时间序列 + is_rising 趋势）
- 热度预测（entity_heat 线性趋势 + 衰减外推 + hot/warm/cold/stale 四档）
- freshness_cron 加 entity_profiles.json stale 标注 + claim_store TTL 清理

---

## [2.3.1] — 2026-08-08

### Added
- 实时冲突管道（ConflictMonitor 增量 ingest_source + live_alerts + alias_map TTL 缓存）
- 图谱接入 traced_export（联合 entity_graph 导出 digraph/markdown 引用图）

---

## [2.3.0] — 2026-08-08

### Added
- 实体图谱（core/entity_graph.py 共现关系网络：加权边 + get_neighbors + Graphviz 导出）
- 实体画像（core/entity_profile.py + entity_profiles.json 长期知识库）
- 冲突检测 v3（core/conflict_v3.py 跨别名归并 + aliases_involved）
- NER span 重叠修复（v220_alias 循环防子串误报）

---

## [2.2.1] — 2026-08-08

### Added
- alias 生命周期接入 freshness_cron（三态自动扫描 + stale 清理）
- rejected 日志生命周期（180 天自动清理 + get_rejected_stats）
- get_prioritized_aliases 性能缓存（TTL 300s）

---

## [2.2.0] — 2026-08-08

### Added
- alias 生命周期管理（父实体热→alias 活跃 / 冷→降级 / 90 天未出现→stale 可清理）
- 高频别名优先检索（get_prioritized_aliases 分级 static/hot/cold）
- 报告实体索引（research() 输出附带 entity_index）

---

## [2.1.3] — 2026-08-08

### Fixed
- auto_expand 噪声消除（NER 句子切分限定子句 + jieba 词性过滤纯名词）
- alias 热冷分离（hot_aliases.json + cold_aliases.json，hit≥5 热 / hit<5 冷）

### Added
- rejected_entities CLI（get_rejected_sorted / get_rejected_by_source / clear_rejected）

---

## [2.1.2] — 2026-08-08

### Changed
- entity_aliases.py 数据结构升级（[str] → [{alias, source, created_at}] 含 auto/manual 标签 + 自动迁移）
- auto_expand 算法优化（实体类型感知窗口 ORG=30/TECH=20 + STOPWORDS + CapitalCase）

---

## [2.1.1] — 2026-08-08

### Added
- Wikidata 公开 API 同步（core/wikidata_sync.py，8 类别 × SPARQL）
- freshness_cron 定期扫描（衰减 + 冷条目 Wikidata 验证 + 调度器）
- entity_pending 批量入库 CLI（approve/reject/batch）
- entity_aliases JSON 持久化（不污染 entities.py）+ auto_expand（hit≥5 触发）

---

## [2.1.0] — 2026-08-08

### Added
- 实体自沉淀机制：元数据 schema（created_at/last_verified_at/hit_count_30d/source/confidence 共 6 字段，146 实体迁移）
- entity_tracker 频次统计 + 90 天半衰期衰减 + 热/冷条目识别
- entity_enricher LLM 自动抽取 + 置信度阈值 + pending 队列
- ner.py 集成 record_hit + research() 集成 enricher

---

## [2.0.2] — 2026-08-08

### Changed
- anchor_score_v2.py 重构（脱离 v1 shim，纯函数式：trust_bonus/jaccard/domain_bonus 各算各的）
- anchor_adapter.py 标记 v1（保留 calculate_score() 向后兼容 + DeprecationWarning）

---

## [2.0.1] — 2026-08-08

### Changed
- 实体词典扩充（95→146，咨询/媒体/电池/互联网/半导体/AI 模型等 51 个）
- trust_sources 扩充（+7 URL 模式：ScienceDirect/IEEE/Springer/ResearchGate/巨潮/证券时报/中国证券网）
- MCP server 新增 score_source + research 工具（9→11）

---

## [2.0.0] — 2026-08-07

### Added (MAJOR 架构重构)
- core/ 子目录独立（核心层与适配层分离）
- core/trust_sources.py 统一信任源白名单（5 领域 × tier1-4）
- core/entities.py + core/ner.py 跨语言实体词典（95 实体 × 5 类型）
- core/llm_router.py 多模型路由（4 provider + 自动 fallback）
- scripts/infoseek_core_v2.py v2 API 入口（research 端到端 + deprecation shim）

---

## [1.9.0] — 2026-08-07

### Added
- 领域 Skill 矩阵全启用（domain_orchestrator + 6 个 Jinja2 模板）
- traced_export 引用图嵌入导出（md+csv）
- fetch_content 链式追踪 v3 多层递归（受控深度 + 防环 + 深度折扣）
- 特殊 subject 字典展开（Last30days/Arxiv/GitHub/知乎/微信）

---

## [1.8.1] — 2026-08-07

### Added
- conflict_detection 第 9 工具（跨源事实冲突自动检测）
- domain_router 与 Anchor_Score 联动（领域信任源加权 0-20 分）
- fuse_analysis 多平台导出（md/json/csv/claude/openai/lobehub 6 格式）
- Jaccard 自适应系数（短文本 1.8 / 中等 1.5 / 长文本 1.2）

---

## [1.8.0] — 2026-08-07

### Added
- 领域垂直 Skill 矩阵（tech/market/finance/policy/competitor-intel）
- fetch_content 链式引用追踪 v2（多层抓取 + dot 引用图 + 相关性评分）
- 多平台导出器（md/json/csv/claude/openai/lobehub）
- 跨源冲突检测 v1（conflict_detection）

---

## [1.7.4] — 2026-08-07

### Fixed
- 第 8 维 TF-IDF 公式修复（退化为常数 stub）→ 关键词集合 Jaccard 相似度

---

## [1.7.3] — 2026-08-07

### Added
- TF-IDF 公式调优（对数平滑 + 子线性 TF）
- fetch_content 链式引用追踪 v1（follow_links 发现 URL 内引用）
- summarize 三跑择优（summa + jieba + regex 兜底）

---

## [1.7.2] — 2026-08-07

### Added
- auto 路由双跑择优 + 第 8 维 TF-IDF 加权相似度（鲁棒中英混合）
- cross_subject_analysis 概念级共享（jieba 关键词 jaccard）

---

## [1.7.1] — 2026-08-07

### Changed
- summarize_content 智能路由（auto/summa/jieba/llm 四选）
- jieba 中文 Textrank 关键词路径

---

## [1.7.0] — 2026-08-07

### Added
- 第 8 个 MCP 工具 summarize_content（summa TextRank 主路径 + LLM 兜底）
- Anchor_Score 第 8 维（语义相似度，基于 summa 关键词）

---

## [1.6.2] — 2026-08-07

### Changed
- MCP 健康检查细化（uptime + 工具调用统计）+ GET /auth-check 端点 + audit.log

---

## [1.6.1] — 2026-08-07

### Changed
- MCP token 认证加固（来源诊断 + 错误响应 hint + token 脱敏日志）

---

## [1.6.0] — 2026-08-07

### Added
- 多 server 拆分（infoseek-search 只读 + infoseek-archive 写）
- 第 7 工具 cross_subject_analysis + Anchor_Score 第 6 维（跨平台分布度）

---

## [1.5.2] — 2026-08-07

### Added
- MCP 新增 HTTP /rpc（短调用）+ GET /health 健康检查

---

## [1.5.1] — 2026-08-06

### Added
- MCP 新增 SSE 传输 + Bearer Token 认证

---

## [1.5.0] — 2026-08-06

### Added
- MCP 集成（Claude/Codex 原生对接）
- Anchor_Score 五维评分（LLM 上下文可读性维度）
- manifest.yaml 双绑 + 主题 README 自动生成
- 严格向后兼容（CHANGELOG + manifest 双绑）

---

## [1.4.0] — 2026-08-06

### Added
- 吸收 @expeditionhub/infoseek v2.0.0 核心能力（存档/去重/任务报告/删除保护）
- URL 标准化 + 去重
- 四级降级提取（静态页面 → 反爬兜底 → 凭证辅助 → 多媒体处理）

---

## [1.3.0] — 2026-07

### Added
- 原始版本（锚点发现 / 内容采集 / 融合输出）
