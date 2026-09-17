# Infoseek 路线图（当前基线 · 待办 · 前景方向）

> 版本：v2.0.0 ｜ 更新：2026-09-17
>
> 本文件只保留**面向当前**的内容：已达成基线、真实未完成待办、前景方向、验收总闸。
> 各历史版本的详细实施记录 / 缺口审计 / 设计裁决已归档至
> **`ROADMAP_archive_20260917.md`**（v1.0.0 → v2.0.0 全量脉络，1088 行）。需要追溯
> 「某 GA 在哪个版本、为何这么设计、踩过什么坑」时查归档，不在本文件堆积。

---

## 一、当前基线（v2.0.0）

| 维度 | 状态 |
| --- | --- |
| 对外版本 | SKILL_VERSION **2.0.0**（唯一真源 `scripts/mcp_tools_common.py`） |
| 全量回归 | **58 PASS / 2 SKIP / 0 FAIL / 0 TIMEOUT**（60 套件，82.7s，2026-09-17） |
| 版本体系 | 四维分离：SKILL_VERSION（对外）/ mod-v（模块内部）/ algo-v（下游契约）/ proto-v（MCP） |
| 评分口径 | 唯一聚合真源 `aggregate_score_v2()`（base 四维 + 独立加权层），三链路口径一致 |
| 分词真源 | `scripts/text_tokenizer.py` 单源（jieba 可选，缺失纯 Python 回退） |
| 人物调研 | GA9 人名消歧五步（`core/person_ner.py` + person-surnames.json）/ GA10 跨语言别名桥接（`core/xling_bridge.py`，pypinyin 可选） |
| 冲突检测 | GA11 键控事实槽（`core/contradiction_scorer.py`：keyed A 层 + LLM opt-in B 层，替代旧短语袋 Jaccard） |
| 网络边界 | GA12 五步链（net_probe 单源 + registry host 台账 + boundary_gate 默认 OFF/fail-open + compensator 预检） |
| 实体持久层 | 双层落盘：`entities_learned.json`（动态回流 + 冷清理 prune）/ `entities_state.json`（hit/last_seen/90 天半衰期衰减）；FreshnessCron 七步闭环 |
| 身份取证 | FakeDetect 账号取证扩展（L1 统计 / L2 图结构 / 时序同步 / L3 ML，consent 闸控） |
| QCM 协同 | 双向桥接（Infoseek qcm_query ←→ QCM 归因），OAuth / GraphQL / OTel / 多进程 |
| 平台发布 | GitHub（GYINT/infoseek）+ npm + ClawHub + DSH 插件包；ima 注册幂等 |

---

## 二、待办（仅列**代码级核实仍开放**的项）

> 历史 ROADMAP 中 P1「perf 多轮采样 / 实体持久层 / 召回深化 / L4 whisper / 冲突多源加权」
> 经 2026-09-17 审计**均已落地**（见归档 §三 + 本版第三章），不再列为待办。

### P1 — 近期（低风险 / 需真实环境，测试无法替代）

1. **L3 登录源真实凭证端到端冒烟**——现有覆盖均为 mock 凭证；需在有真实 key 的
   目标机对至少一个登录源做一次真实抓取冒烟（沙箱网络受限，无法在此闭环）。
2. **OSINT 双客户端真实实证**——`sherlock_client.py` 结构已对齐，需在隔离 venv 装
   `sherlock-project` 跑真实样本；Maigret/Sherlock 结果融合去重 + CN/IN/全局误报基线。
3. **能力注册表授权 UX**——Maigret/Sherlock/manual_review 仍 default_off；补
   「授权 → enable → consent 记录」端到端 CLI/UX（当前仅函数级 `grant_consent`）。
4. **跨平台安装实证**——`install.sh` 已在 Windows-Git-Bash 校验；Linux/macOS 需目标机
   联网装依赖后各跑一次 `bash install.sh --venv`。

### P2 — 中期（核心收益）

5. **矛盾检测叙述句事实槽召回增强**——GA11 键控槽对结构化数值冲突强，对长叙述句
   （无明确指标词 / 隐式比较）召回仍偏弱；扩充方面簇词典 + B 层 LLM 槽的默认策略评估。
6. **三链路口径旁路字段收口**——链 B（MCP 活跃链路）`domain_bonus` 仍是旁路字段，
   待统一并入 `aggregate_score_v2()` 的环节化计算。
7. **research 全链路性能**——3000 源实测 research（2k 子集 lite）P50 ≈ 83.6s、
   冲突检测 P50 ≈ 39.3s（见 `dist/perf_baseline_v101.json`）；多轮统计已建立，
   下一步定位 research 融合阶段热点。

### P3 — 远期（v2.x 立项，保持零依赖 + 降级路径哲学）

8. **多模态理解**——图片 / 视频 / 音频内容理解与检索（独立大模块；当前 L4 whisper 仅音频转录）。
9. **编排 / 多 agent 协同**——与搜索 / 验证 / 归档工具深度整合，作为可观测子任务被组合。
10. **合规审计增强**——抓取合规 / 版权 / 凭证审计的自动化报告。
11. **本地文件集成**——按用户反馈触发（不预设）。

### 明确不做（设计边界）

实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话——交由更专业的专用工具。

---

## 三、2026-09-17 收尾记录（ROADMAP P1 旧待办销项）

审计发现旧 §三 P1/P2 待办表与 v2.0.0 代码脱节，本轮做代码级核实并补齐最后缺口：

- **perf 多轮 P50/P95 采样（旧 #1）**：`scripts/perf_baseline_v101.py --rounds ≥3`
  本就支持；本轮修复 `dist/` 目录缺失时写基线 FileNotFoundError 的真实 bug（自动建目录），
  并首次产出多轮基线 `dist/perf_baseline_v101.json`（3000 源 ×3 轮：
  评分 P50/P95=0.6/3.7s · 冲突 39.3/41.2s · research 83.6/83.6s）。
- **FreshnessCron 实体持久层（旧 #2）**：主体早已落地（EntityTracker 状态层
  90 天半衰期 + 跨进程；learn_entity 回流层）。本轮补齐最后一公里——
  `entities.prune_learned_entities()` learned 层冷 / 噪声条目清理（180 天超龄 +
  低置信 + active 保护 + 默认 dry_run 安全模式 + 原子写），接入 FreshnessCron 第 7 步
  （env `INFOSEEK_LEARNED_PRUNE_APPLY=1` 才真删）；修正 freshness_cron 中
  「entities.py 无持久层」的过时注释。守护 `tests/test_entity_prune_v200.py`（20 断言）。
- **旧 #4 召回深化 / #5 L4 whisper / #6 冲突多源加权**：核实分别已于
  v2.5.0 前后落地（`entity_graph.get_neighbors` 接入 pipeline L876 /
  mcp_tools_search L4 转录可选路径 / conflict_v3 多源可信度加权 L295），销项。
- 版本号保持 **2.0.0**（改动为 P1 待办收尾 + 文档纯净版，按版本规则不触发 bump）。

---

## 四、前景方向

- **短期**：把已实现能力从「测试验证」推向「生产可用」——真实凭证 / 真实 OSINT 样本 /
  跨平台安装三类**必须在真实环境闭环**的冒烟（P1 #1-#4）。
- **中期（v2.x）**：矛盾检测叙述句召回、research 性能、口径旁路收口；与 QCM 等
  跨 skill 协同继续扩展（已有契约基础）。
- **长期**：AI Agent 深度协作、实时协作调研、合规审计自动化；坚持零依赖哲学
  （所有增强均有降级路径，可选依赖缺失不塌）。

---

## 五、验收总闸（每次合入）

- 全量回归全绿：`python3 tests/run_all.py` → 0 FAIL / 0 TIMEOUT（SKIP 需为可选依赖 preflight）。
- 零破坏性变更：状态文件（`~/.infoseek/*.json`）/ env 向后兼容；清理类动作默认 dry_run。
- 版本单源：对外版本只改 `scripts/mcp_tools_common.py:SKILL_VERSION`，并与
  SKILL.md / manifest / package.json / CHANGELOG 联动；四维版本语义不得混用。
- 文档同步：SKILL.md / README / 本路线图；历史实施细节入归档而非堆积本文件。
- 发布前：备份 tar（命名带章节/内容标识 + 解包校验）→ ima 幂等注册 → 嵌套终检
  （`find -maxdepth 2 -type d | grep "/infoseek/infoseek$"` 须零命中）。
