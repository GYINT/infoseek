# Infoseek 路线图（当前基线 · 待办 · 前景方向）

> 版本：v2.2.0 ｜ 更新：2026-09-21
>
> 本文件只保留**面向当前**的内容：已达成基线、真实未完成待办、前景方向、验收总闸。
> 各历史版本的详细实施记录 / 缺口审计 / 设计裁决已归档至
> **`ROADMAP_archive_20260917.md`**（v1.0.0 → v2.0.0 全量脉络，1088 行）。需要追溯
> 「某 GA 在哪个版本、为何这么设计、踩过什么坑」时查归档，不在本文件堆积。

---

## 一、当前基线（v2.2.0）

| 维度 | 状态 |
| --- | --- |
| 对外版本 | SKILL_VERSION **2.2.0**（唯一真源 `scripts/mcp_tools_common.py`）；MINOR：三元事件身份 + LLM 时间维度 |
| 全量回归 | **75 PASS / 0 SKIP / 0 FAIL / 0 TIMEOUT**（75 套件，2026-09-21 复核；新增 `test_hf_r1_v220.py`、`test_hf_p1_v220.py`；jieba/pypinyin 需手动补装（环境重置后丢失），playwright 缺失不影响——相关套件自兜底不产 SKIP） |
| openfix 批次（2026-09-18，版本号不变） | P0-OPEN-04 score 量纲归一化+禁空骨架 / P0-OPEN-06 时间事实槽+无冲突·未评估区分 / P0-OPEN-05 GPT-5 路由+模板按域收窄+跨源综合段 / P1 README 对齐 19 工具+run_tests SKIP 判定修复；守护 `test_open_fixes_v210.py`（60 断言） |
| 版本体系 | 四维分离：SKILL_VERSION（对外）/ mod-v（模块内部）/ algo-v（下游契约）/ proto-v（MCP） |
| OSINT 客户端 | sherlock v0.16.x（`--csv` 读结果）/ maigret 0.6.x（`-J simple` 报告文件 + 嵌套 status）契约对齐实测 |
| 身份归因 | Maigret×Sherlock **双源聚合去重**（`core/identity_aggregator.py`：交叉确认 +0.10/误报抑制/分区）+ AccountTrustScorer 验证 |
| 授权 UX | `scripts/infoseek_consent_cli.py`（list/grant/revoke/shell-init/doctor + consent.log 审计） |
| 评分口径 | 唯一聚合真源 `aggregate_score_v2()`（base 四维 + 独立加权层），三链路口径一致 |
| 分词真源 | `scripts/text_tokenizer.py` 单源（jieba 可选，缺失纯 Python 回退） |
| 人物调研 | GA9 人名消歧五步（`core/person_ner.py` + person-surnames.json）/ GA10 跨语言别名桥接（`core/xling_bridge.py`，pypinyin 可选） |
| 冲突检测 | GA11 键控事实槽（`core/contradiction_scorer.py`：keyed A 层 + LLM opt-in B 层，替代旧短语袋 Jaccard）+ P0-OPEN-06 **时间事实槽**（年月/季度→ISO 区间，第三路 max 融合）+ **v2.2.0 三元事件身份 `(subject,aspect,time_key)`**（多 span 扇出 + 半开区间事件对拍 + 多期间逐对明细 + LLM 全路径字段收口）+ verdict 三态（conflict/no_conflict/not_assessable）+ 覆盖率 |
| 报告模板 | `domains/templates.yaml` v3.2.0：5 域 + default，高分源（≥70）跨源综合段、空源守卫、filter_block 注入；tech 已按域收窄为通用技术（制造 + AI/软件） |
| 评分健壮性 | P0-OPEN-04 入口 0–1 量纲自动 ×100（`scale_normalized`）；核心源为 0 禁止空骨架（`filtered_out` 清单及原因） |
| 网络边界 | GA12 五步链（net_probe 单源 + registry host 台账 + boundary_gate 默认 OFF/fail-open + compensator 预检） |
| 实体持久层 | 双层落盘：`entities_learned.json`（动态回流 + 冷清理 prune）/ `entities_state.json`（hit/last_seen/90 天半衰期衰减）；FreshnessCron 七步闭环 |
| 身份取证 | FakeDetect 账号取证扩展（L1 统计 / L2 图结构 / 时序同步 / L3 ML，consent 闸控） |
| QCM 协同 | 双向桥接（Infoseek qcm_query ←→ QCM 归因），OAuth / GraphQL / OTel / 多进程 |
| 平台发布 | GitHub（GYINT/infoseek）+ npm + ClawHub + DSH 插件包；ima 注册幂等 |

---

## 二、待办（仅列**代码级核实仍开放**的项）

> 2026-09-18（v2.1.0）代码级审计：沙箱装包实测发现并修复了 Maigret/Sherlock
> 客户端 CLI 契约全错的 P0（旧实现真实环境必然落空），并交付双源聚合 + 授权 CLI，
> 销去原 P1#2/#3 的**代码侧**。下列项均**必须在真实环境闭环**，沙箱网络受限无法替代。

### P1 — 近期（低风险 / 需真实环境，测试无法替代）

1. **L3 登录源真实凭证端到端冒烟**——现有覆盖均为 mock 凭证；需在有真实 key 的
   目标机对至少一个登录源做一次真实抓取冒烟（沙箱网络受限，无法在此闭环）。
2. **OSINT 真实样本误报基线校准**——双源聚合引擎与高误报平台表（实测 Droners 对必不
   存在用户名误报 Claimed，已入内置表）已就位，但 CN/IN/全局误报率阈值、平台别名表、
   长尾 rank 阈值仍是**规则框架默认值**；需在隔离 venv 用 sherlock-project / maigret
   对真实样本标定（含 sherlock/maigret 结果融合的精度/召回）。
3. **跨平台安装实证**——`install.sh` 已在 Windows-Git-Bash 校验；Linux/macOS 需目标机
   联网装依赖后各跑一次 `bash install.sh --venv`。

### P2 — 中期（核心收益）

4. **矛盾检测叙述句事实槽召回增强**——GA11 键控槽对结构化数值冲突强，对长叙述句
   （无明确指标词 / 隐式比较）召回仍偏弱；扩充方面簇词典 + B 层 LLM 槽的默认策略评估。
5. **三链路口径旁路字段收口**——链 B（MCP 活跃链路）`domain_bonus` 仍是旁路字段
   （仅报告不计入 final），待统一并入 `aggregate_score_v2()` 的环节化计算。
6. **research 全链路性能**——3000 源实测 research（2k 子集 lite）P50 ≈ 83.6s、
   冲突检测 P50 ≈ 39.3s（见 `dist/perf_baseline_v101.json`）；多轮统计已建立，
   下一步定位 research 融合阶段热点。

### P3 — 远期（v2.x 立项，保持零依赖 + 降级路径哲学）

7. **多模态理解**——图片 / 视频 / 音频内容理解与检索（独立大模块；当前 L4 whisper 仅音频转录）。
8. **编排 / 多 agent 协同**——与搜索 / 验证 / 归档工具深度整合，作为可观测子任务被组合。
9. ~~**合规审计增强**~~ ✅ **已落地（2026-09-21，D-8/P3-9）**——抓取合规 / 版权 / 凭证审计自动化报告（`scripts/compliance_audit.py` + 守护 `tests/test_compliance_audit_v220.py` 30 断言 + `references/compliance-audit-report.{md,json}`）。
10. **本地文件集成**——按用户反馈触发（不预设）。

### 明确不做（设计边界）

实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话——交由更专业的专用工具。

### 二·补、2026-09-20 六类分层待办盘点（规划任务清单）

> 起点：v2.2.0（GA11 事件槽批次）**发布收尾暂停**，转入优先级重排后再定发布。本清单交叉核验
> 本路线图 / `ROADMAP_archive_20260917.md` / `CHANGELOG.md` / `RELEASE_NOTES.md` / 风险与边界文档 /
> 代码接线 / 测试现状；**历史快照与归档中的开放描述不计为当前开放缺陷**。
> 唯一对外版本真源 `scripts/mcp_tools_common.py`（现 `SKILL_VERSION="2.2.0"`）。

**分层**：① 阻塞层 P1（沙箱不可替代）→ ② 收敛层 P2+D（沙箱可执行）→ ③ 清理层 B+C（文档/语义）→ ④ 远期层 P3。

> **状态更新（2026-09-20 21:03，已确认）**：当前环境**不支持真实环境与跨平台**验证——**P1-1 / P1-3 暂缓挂起**；P1-2 可在沙箱预置阈值/别名框架，真机样本标定仍需目标机。**可执行最高优先级切换至收敛层 P2 + D**，其次清理层 B + C；P3 维持归档。下列 P2/B/C/D 条目均已工具取证确认仍开放。
> **Phase-R1 执行记录（2026-09-20 21:44，已落地并全量回归 69 PASS / 0 FAIL·232s ALL GREEN）**：R1-a（D-1）cron 第7步传真实 `active_names` + stats 透出 `applied`，新增 `test_learned_prune_cron_r1a.py`；R1-b（P2-5/D-2）`domain_bonus` 收敛锁定，新增 `test_domain_bonus_no_double_count.py`（链B 传 0 为刻意设计防双重计分）；R1-c（D-5/B-1~B-4）删 `infoseek_mcp_server.py` 死分支、CHANGELOG→67 PASS/231s、risk-register L35 更正、freshness_cron 头注释 v2.2.0+7 步枚举、mcp_tools_forensics docstring→v2.2.0。版本号维持 **2.2.0**（清理/守护类，变动量<10%）。
> **Phase-R2 执行记录（2026-09-20 22:08，层 C 历史归档误命中销账，全量回归 69 PASS / 0 FAIL·230s ALL GREEN）**：C-1 `CHANGELOG.md` L235「边界审计遗留（已立案…）」段标题下加**事后闭合注**（DEF-13~16 系 v2.1.0 立案快照、v2.1.1 全修，守护 test_p2_v211.py 四组 70 断言；历史正文与「Open 口径（保持现状）」三条刻意保留、一字不改）；C-2 死路径 `roadmap_ch812.md` 经 glob 证实**文件已不存在**，仅在 ROADMAP 行内注明（A5 命中皆 GA5 分词单源化已闭合 / GA9 测试分组见归档 L933），不触碰冻结归档；C-3 复核 `ROADMAP_archive_20260917.md` L3-9 冻结快照声明成立，只查不改。连带如实化（同表已完成项，均工具复核）：B-1/B-2/B-3/B-5、D-1/D-2/D-5、P2-5 勾 ✅；L17 基线 67→**69 PASS**/231s→**232s 口径**（本次回归实测 230s）、L3 更新日→2026-09-20。零代码逻辑改动、零新增测试，版本号维持 **2.2.0**（变动量<10%）。
> **Phase-R2b 执行记录（2026-09-20 22:46，P2 收敛层销账，全量回归 72 PASS / 0 SKIP / 0 FAIL / 0 TIMEOUT ALL GREEN）**：P2-4/D-4 矛盾检测 term-value 事实槽闭环（aspects 24 簇 / opposites 9 组 / NUMERIC_ASPECTS 12 类 / 守护 22 断言，含 3 项 known_boundary 记延伸项）；P2-6/D-3 性能 profiling 闭环（`--profile` 热点 + `--rounds` 多轮 P50/P95 + `dist/perf_profile_v101.json`，守护 23 断言）；D-6 文档状态同步守护首建（25 断言）并修复 ROADMAP 尾部 2 个非 UTF-8 字节（L239/L240 重复行残迹去重，属错字级修复）；D-7 证据归档模板 + 验收台账立项交付。零代码逻辑破坏、零公开签名改动，版本号维持 **2.2.0**（守护/文档/采样类，变动量<10%）。
> **Phase-R3 执行记录（2026-09-21，P3 层推进：D-8/P3-9 合规审计自动化 + A5–A7 编号映射收口）**：**D-8/P3-9 落地**——新增 `scripts/compliance_audit.py`（凭证审计 / 抓取合规 / 网络边界 / 版权来源四维 → Markdown+JSON 双产物，全文零明文；`_iter_files` 跳过自产物 + `_redact` 命中不回显，自指污染根治，幂等复证）+ 守护 `tests/test_compliance_audit_v220.py`（**30 断言**，连续 4 次 exit=0）+ 产物 `references/compliance-audit-report.{md,json}`；**A5–A7 复核收口**——全仓命中皆 `GA5/GA6/GA7` 前缀子串，判为「编号来源不可考·命名缺失（非代码缺口）」，非开放缺陷。连带文档销账：L17 基线 72→**73 套件**、D 表 D-8/D-审、P3 表拆分（P3-9 已落地）、前景方向移出「长期」、B-5 复核、`tests/run_tests.py` docstring 套件数如实化。**连带修复（测试环境隔离）**——回归发现 `tests/test_identity_clients_v100.py` C1「能力默认 OFF」断言失效，根因定位为 **env 旁路**（`core/capability_registry.is_enabled` 中 `_env_override` 优先于 registry 声明 `enabled:false`，而沙箱进程遗留 `INFOSEEK_ENABLE_MAIGRET/SHERLOCK/ACCOUNTTRUSTSCORER=1` → 默认 OFF 早退闸口被绕过 → 落到 `_consent_gate` 抛 `ConsentRequired`）；修复 = 测试头部显式 pop 该三项 per-capability env，锁定「纯注册表默认态」，并沉淀为 **㉖铁律**：凡测试依赖「能力默认 OFF」语义，必须显式隔离 `INFOSEEK_ENABLE_*`，禁止依赖环境偶然性（单跑复现 20 PASS / 0 FAIL，幂等复证）。零代码逻辑破坏、零公开签名改动，版本号维持 **2.2.0**（新增模块/文档类，变动量<10%）。

#### P1 — 真实环境验证（阻塞层 · 环境受限暂缓 · 发布可信度闸）

| 编号 | 任务 | 路径 | 处置 |
| --- | --- | --- | --- |
| P1-1 | L3 登录源真实凭证端到端冒烟（现覆盖均 mock） | `scripts/mcp_tools_search.py`(`_fetch_with_credential`) · `references/credential-tools.md` · `references/api-keys.md` | 真机闭环；完成前发布可信度不成立 |
| P1-2 | OSINT 真实样本误报基线校准（CN/IN/全局阈值、平台别名表、长尾 rank、双源融合精度/召回） | `core/identity_aggregator.py` · `references/trusted-sources.json` | 真机闭环（隔离 venv + sherlock/maigret 真实样本） |
| P1-3 | Linux/macOS 跨平台安装实证（Windows-Git-Bash 已校验） | `install.sh` · `references/external-deps.md` | 真机闭环 |

#### P2 — 代码 / 性能（收敛层 · 沙箱可执行 · 核心收益）

| 编号 | 任务 | 路径 | 处置 |
| --- | --- | --- | --- |
| P2-4 | 矛盾检测叙述句事实槽召回增强（扩充方面簇词典 + 评估 B 层 LLM 槽默认策略） | `core/contradiction_scorer.py` · `references/contradiction-synonyms.json` | ✅ 已收口（R2b 收敛层：`references/contradiction-synonyms.json` aspects 20→24 簇 / opposites 8→9 组、`_meta.version` 1.1.0；`core/contradiction_scorer.py` 新增 term-value 事实槽链路（`_TERM_VALUE_ASPECTS` 4 类）与 `NUMERIC_ASPECTS` 10→12 类；守护 `test_p24_term_value_v250.py` **22 断言**；B 层 LLM 槽维持默认关闭（评估结论记延伸项）。零公开签名改动 → 19 处消费点安全） |
| P2-5 | 三链路口径 `domain_bonus` 旁路收口（链 B 旁路并入 `aggregate_score_v2()` 环节化计算） | `scripts/infoseek_core_v2.py:185-197/308/313/339` · `core/anchor_score_v2.py` | ✅ 已收口（R1-b，见 D-2：代码级核实 L313 传 `domain_bonus=0` 系刻意防双重计分、L339 仅回填观测；新增 `test_domain_bonus_no_double_count.py` 守护锁定，不重构评分核心） |
| P2-6 | research 全链路性能热点定位（旧基线 research P50≈83.6s、冲突检测 P50≈39.3s） | `scripts/perf_baseline_v101.py` · `dist/perf_baseline_v101.json` · `scripts/infoseek_core_v2.py` | ✅ 已收口（R2b 收敛层：`scripts/perf_baseline_v101.py` 增 `--profile`（cProfile+pstats 热点）与 `--rounds` 多轮采样；实测 scale 1000×3（`python -u`）评分 P50/P95 1.5/6.4s、冲突 41.0/42.2s、research 126.7/127.1s；产物 `dist/perf_profile_v101.json` + `dist/perf_baseline_v101.json`；守护 `test_perf_profile_d3.py` **23 断言**） |
| P2-A5A7 | 编号 A5–A7 映射**未证实**——当前仓 `A5/A7` 命中均指 GA5 分词单源化（已闭合）或 GA9 测试分组 | `CHANGELOG.md` · `RELEASE_NOTES.md`（历史段） | ✅ 已收口（R3-2026-09-21 全仓复核：`A5/A6/A7` 命中皆为 `GA5/GA6/GA7` **前缀子串**（CHANGELOG L499/L502/L636 · RELEASE_NOTES L165/L219/L224 · 归档 L778-780/L933），无独立 A5–A7 规划编号 → 判为「**编号来源不可考·命名缺失（非代码缺口）**」，非开放缺陷，cancelled） |

#### P3 — 前景（远期层 · 归档，v2.x 立项）

| 编号 | 方向 | 路径 | 处置 |
| --- | --- | --- | --- |
| P3-9 | 合规审计增强（抓取合规 / 版权 / 凭证审计自动化报告） | `scripts/compliance_audit.py` · `references/compliance-audit-report.{md,json}` | ✅ 已落地（R3-2026-09-21；守护 `tests/test_compliance_audit_v220.py` **30 断言**，幂等 4 次复证；全文零明文） |
| P3-7/8/10 | 多模态理解 / 编排·多 agent 协同 / 本地文件集成 | 独立大模块（保持零依赖 + 降级路径） | 归档（远期立项） |
| P3-边界 | 明确不做：实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话 | — | 归档（设计边界） |

#### B — 文档陈旧（清理层）

| 编号 | 陈旧点 | 路径 | 处置 |
| --- | --- | --- | --- |
| B-1 | v2.2.0 回归数写 **66 PASS / 229s**（实为 67 PASS / 231s，含新增 llm_fields 套件） | `CHANGELOG.md`（v2.2.0「守护与回归」段 L43） | ✅ 已清理（R1-c：L43 已为 67 PASS/231s；2026-09-20 复核） |
| B-2 | 风险注册表 v1.0.1 称 `fetch_content` L2–L4 为函数壳（现行代码 L2 渲染 / L3 凭证 / L4 多媒体均已接线） | `references/risk-register.md` | ✅ 已清理/归档（R1-c：L35 改为「L2-L4 已接线（L2 渲染/L3 凭证/L4 多媒体）」；2026-09-20 复核无「函数壳」残留） |
| B-3 | `freshness_cron.py` 版本/步骤/docstring 漂移（头注释标 v2.4.0；头列 6 步、实现称第 7 步；`run_full_scan` docstring 仅述衰减/冷条目/alias） | `core/freshness_cron.py:1-16/128-167` | ✅ 已清理（R1-c：头注 v2.2.0 + 7 步枚举；2026-09-20 复核 L3/L5/L12 属实） |
| B-4 | `mcp_tools_forensics.py` 内部 docstring 标 v1.0.0（MCP 集成语境 v1.6.0） | `scripts/mcp_tools_forensics.py:3` | ✅ 已清理（R1-c：docstring 改 v2.2.0） |
| B-5 | 本路线图自身基线表标题/回归数旧（L12 v2.1.0 / L17 65 PASS·2 SKIP·87.9s） | `references/ROADMAP.md`（本次计入时已同步） | ✅ 已清理（R1-c 起逐步同步；R3-2026-09-21 复核：L3 已 2026-09-21、L17 已 75 PASS / 0 SKIP / 0 FAIL / 0 TIMEOUT（75 套件）、验收总闸已 75 PASS） |

#### C — 历史归档误命中（归档层）

| 编号 | 误命中点 | 路径 | 处置 |
| --- | --- | --- | --- |
| C-1 | DEF-13~16 早期「开放」表述实为 v2.1.0 立案快照，v2.1.1 已修复/关闭 | `CHANGELOG.md` 历史段（L235 立案快照） · `tests/test_p2_v211.py` | ✅ 已归档（R2-2026-09-20：CHANGELOG 立案段标题下加事后闭合注，历史正文/Open 口径段不改；守护 test_p2_v211.py 四组 70 断言） |
| C-2 | 「A5」在归档中为 GA9 测试分组，非当前 A5–A7 规划项 | `CHANGELOG.md` · `RELEASE_NOTES.md` 历史段（命中皆 GA5 分词单源化，已闭合）· `ROADMAP_archive_20260917.md:933`（A4/A5=GA9 检测守卫测试分组）；原引 `roadmap_ch812.md` **文件已不存在（死路径，2026-09-20 glob 证实）** | ✅ 已归档（R2-2026-09-20：A5 去伪结论见 P2-A5A7 行；死路径仅在本行注明，不触碰冻结归档） |
| C-3 | GA5/GA9/GA10/GA11/GA12 等历史事项已闭合；G6 真实网络运行并入 P1 | `ROADMAP_archive_20260917.md`（冻结快照 L3-9 声明明确「仅供追溯」） | ✅ 已归档（R2-2026-09-20 复核归档声明成立；冻结快照只查不改，G6 真机诉求见 P1 表） |

#### D — 建议立项（收敛层 · 规划任务 + 审计缺口）

| 编号 | 建议 | 路径 | 处置 |
| --- | --- | --- | --- |
| D-1 | learned prune cron **apply 安全测试**：补「cron 携带 `INFOSEEK_LEARNED_PRUNE_APPLY=1` 时真实落盘」用例 + 真实 `active_names` 传入 + stats 透出 `remaining/dry_run/applied` | `core/freshness_cron.py:128-167` · `core/entities.py` · `tests/test_learned_prune_cron_r1a.py` | ✅ 已收口（R1-a：真实 active_names + stats 透出 applied，守护 test_learned_prune_cron_r1a.py） |
| D-2 | 三链路评分字段**归属统一 + 架构守护测试**（与 P2-5 同源，落地为守护） | `scripts/infoseek_core_v2.py` · `core/anchor_score_v2.py` · `tests/test_domain_bonus_no_double_count.py` | ✅ 已收口（R1-b：守护锁定链 B 传 `domain_bonus=0` 防双重计分、链 A 计入） |
| D-3 | research 融合阶段**性能 profiling**（与 P2-6 同源，落地为采样基线） | `scripts/perf_baseline_v101.py` | ✅ 已收口（R2b 收敛层：与 P2-6 同源；`_STATS_ROW` 表格解析 + `profile_research()` 采样，可离线复跑） |
| D-4 | 叙述句事实槽召回增强（与 P2-4 同源） | `core/contradiction_scorer.py` | ✅ 已收口（R2b 收敛层：与 P2-4 同源；`_term_value()` 槽抽取 + `_build_keyed_slots` 接线） |
| D-5 | `infoseek_mcp_server.py` 连续重复 `elif tool_name == "account_forensics"` 死分支清理（L549/L551 第二个不可达） | `scripts/infoseek_mcp_server.py:549-552` | ✅ 已清理（R2b 收敛层复核：`elif tool_name == "account_forensics"` 现存 **1 处**（L549），连续重复死分支已于 R1-c 删除） |
| D-6 | 文档真实状态**同步机制**（风险表 / ROADMAP / CHANGELOG / 内部 docstring 版本与结论自动校验） | `references/` · CI/`tests/` | ✅ 已收口（R2b 收敛层：新增 `tests/test_doc_state_sync_d6.py` **25 断言**（A 8 / B 9 / C 4 / D 4）——五文档版本单源 + ROADMAP 基线行自校验 + docstring 分歧白名单 + 产物 JSON 契约 + ROADMAP 严格 UTF-8；三处解码点加 `errors=replace`；ROADMAP 尾部 2 个非 UTF-8 字节已同行清理） |
| D-7 | 真实环境验证**证据归档模板 + 验收台账**（支撑 P1 可复现） | `references/`（新增） | ✅ 已立项并交付模板（R2b 收敛层：新增 `references/real-env-evidence-log.md` 证据归档模板 + 验收台账；真机样本仍受 P1 环境阻塞，模板冻结可填） |
| D-8 | 合规审计自动化（= P3-9 立项形式） | `scripts/compliance_audit.py` · `tests/test_compliance_audit_v220.py`（30 断言） · `references/compliance-audit-report.{md,json}` | ✅ 已落地（R3-2026-09-21：凭证审计 / 抓取合规 / 网络边界 / 版权来源四维 → Markdown+JSON 双产物，全文零明文；自指污染已修（自产物跳过 + `_redact` 不回显明文），幂等 4 次复证 30 PASS / exit=0） |
| D-审 | 无 `audit`/`审计` 命名文件**≠ 无审计缺口**；A5–A7 编号仅「命名缺失」 | — | ✅ 已闭合（R3-2026-09-21：审计能力已由 D-8/P3-9 落地补齐；A5–A7 经全仓整词复核判为「编号来源不可考·命名缺失（非代码缺口）」，详见 P2-A5A7 行） |

**补注**：全仓 `TODO|FIXME|XXX|HACK` 仅命中 `scripts/leak_scan.py` 文档示例 `XXX_API_KEY`，无真实未处理标记；`scripts/perf_baseline_v101.py` 支持 `--rounds` 多轮采样，非缺陷。

#### HF — 人因指纹增强（2026-09-21 评估立项 · 载体须换 · 红线不碰）

> 评估结论：**目标可行、载体须换、增量靠补齐、红线不碰**。三类因子对既有资产重叠度 60%～75%，增量在**既有资产补齐**而非新建体系。
> 全文：`references/human-fingerprint-feasibility-report.md`（八节：结论摘要 / 任务背景 / 三类因子逐项可行性 / 合规边界裁决 / 重叠度矩阵 / 开发路径与优先级 / 风险与合规闸 / 结论与建议）。
> 红线边界：**设备 / 客户端指纹（UA / 屏幕 / Canvas·WebGL / 字体 / 时区 / JA3·JA4）一律不采集**——触碰 `PRIVACY.md §4/§6`、`network-boundary-report.md §4`，且与 `scripts/l2_renderer.py` 反指纹方向相反。

| 编号 | 任务 | 路径 | 处置 |
| --- | --- | --- | --- |
| HF-P0 | 既有资产补齐（零合规风险）：AccountTrustScorer 增「内容伪造特征」子维→五维；FakeDetect L1 增列活跃时段熵 / 节奏规律 / 内容模板化 n-gram；粉丝质量回灌；`account_forensics` dataset 增 `profile` / `timing` 字段（向后兼容） | `scripts/account_trust_scorer.py` · `extensions/fake_detect/l1_engine.py` · `scripts/mcp_tools_forensics.py` | ✅ 已落地（HF-R1 · 2026-09-21）：仅扩字段 + 规则，**不新增能力项 / 不改 network_boundary / 不动默认开关**；守护 `tests/test_hf_r1_v220.py`（33 断言，零回归） |
| HF-P1 | 协同簇增强 + 融合权重校准：`coord_clusters` / `sync_groups` 群体置信注入融合→A/B/C/D 四因子（D = 协同簇强度）；四因子动态重归一化；FPR ≤ 2% + `train_l1_thresholds` 重训 | `scripts/identity_confidence_fusion.py` · `extensions/fake_detect/` | ⏳ 部分落地（HF-P1 · 2026-09-21）：D 因子 + 四因子动态重归一化 + 样本接入契约/校准套件已就绪；**权重最终值与生产 FPR 待真实样本校准**（沙箱无真实环境） |
| HF-P2 | 设备层窄口（**暂缓**）：仅「账号自身主动公开发布的设备标签」作弱证据；前置 = 修订 `PRIVACY.md §4` + 重评 network_boundary + 6 条合规判据全通 + 用户显式授权 | `PRIVACY.md` · `references/network-boundary-report.md` | 仅登记不排期（红线内候选，当前建议：不实施） |
| HF-否 | **否决项**：设备 / 客户端指纹（义 1）不采集；保留「机械化行为指纹」（义 2） | — | 红线（`PRIVACY §4/§6` / `network §4`） |

> 术语口径：义 1「设备 / 客户端指纹」（采「设备是什么」，被动计算，红线）与义 2「机械化行为指纹」（采「行为像不像机器」，主动观察账号可见行为，安全）**命名分离**，禁用「机器指纹」混指。
> 版本：评估类文档，零代码逻辑改动，对外版本维持 **v2.2.0**。

> **HF-R1 执行记录（2026-09-21，人因指纹增强 P0 落地 · 全量回归 74 PASS / 0 SKIP / 0 FAIL / 0 TIMEOUT ALL GREEN）**：四子项编码完成——① `scripts/account_trust_scorer.py` v1.0.0→**v1.1.0**，新增「内容伪造特征」子维（`template_similarity` / `ai_generated_ratio` / `duplicate_ratio`）四维升五维，权重各 0.20，`confidence` 分母随维度数自适应；② `extensions/fake_detect/l1_engine.py` 增列 `hour_entropy` / `rhythm_cv` / `tpl_ngram` / `follower_quality` 四特征并新增 L1f-L1i 四条规则（**默认关闭** `*_enabled=False`，不进入既有 `n_flags` 聚合 → 零回归），`l1_rule_names()` 扩至 9 项；③ `extensions/fake_detect/data_adapter.py` 增 `follower_quality` / `template_similarity` 别名与归一化（0-100 自动降 0-1）；④ `scripts/mcp_tools_forensics.py` dataset 增 `profile` / `timing` 字段（向后兼容，`from_raw` 透传 → `Dataset.profile/timing`）。守护 `tests/test_hf_r1_v220.py`（**33 断言**：A 五维 / B 零回归 / C 粉丝质量回灌 / D profile·timing 兼容 / E 合规判据）；连带修复 `l1_engine.spike_feats` 空数组守卫（growth 缺失不再崩，非空行为不变）；D-6 白名单同步（`scripts/account_trust_scorer.py` → `1.1.0`）。零破坏性变更（`DIM_WEIGHTS` 四维→五维、`apply_l1_rules` 返回追加键属扩展型），对外版本维持 **v2.2.0**。

> **HF-P1 执行记录（2026-09-21，协同簇融合 + 真实样本准备落地）**：① `scripts/identity_confidence_fusion.py` v1.6.0→**v1.7.0** —— A/B/C **扩为 A/B/C/D**（D = 协同簇强度，`norm_cluster` 综合 `coord_clusters` 规模/密度 + `sync_groups` 规模，复用 CROSS_SOURCE_BOOST 群体证据语义）；D 以**互补证据**参与（协同越强 → 身份置信越下调），四因子**动态重归一化**（缺因子不中断）；默认权重 `a:b:c:d = 0.28:0.32:0.20:0.20`，其中 **a:b:c 保持旧 7:8:5 比例 → 无 D 信号时逐点等价旧三因子（零回归）**；`INFOSEEK_FUSION_WEIGHTS` 兼容旧 3 值（d=0）与新 4 值。② **真实样本准备物**：`references/hf-real-sample-schema.md`（接入契约：schema / 来源清单 / 合规闸 / 校准流程）+ `scripts/hf_sample_kit.py`（`--validate` 契约校验 / `make_synthetic` 半合成打样 / `--calibrate` 在 FPR ≤ 2% 约束下搜 D 权重 + 扫描曲线）。③ 守护 `tests/test_hf_p1_v220.py`（**33 断言**：A D 因子归一化 / B 四因子融合 / C 零回归 / D 权重契约 / E 样本套件 / F 合规容错）；既有 `test_identity_fusion_v160.py` **39/39 全绿**（零回归）。④ 半合成打样实测 **D 因子增益显著**：`d=0 → recall 0.52`；`d=0.20（默认）→ recall 1.00 @ FPR 0.6%`。**遗留（须真实样本）**：权重最终值、CN/IN/全局分区 FPR、`train_l1_thresholds` 重训 —— 待 Phase-B 真机样本。（对外版本维持 **v2.2.0**）

#### P1 执行 phase（最高优先级任务 · 真实环境）

- **Phase-A · L3 凭证冒烟**（约 0.5d）：目标机 `export` 真实 key → 对 1 个登录源跑 `fetch_content` → 断言 `extraction_level==3` 且正文非空 → 响应摘要/`cosKey` 入验收台账。
- **Phase-B · OSINT 阈值标定**（约 1d）：隔离 venv 装 `sherlock-project`/`maigret` → 真实样本跑双源聚合 → 标定 CN/IN/全局误报阈值、平台别名、长尾 rank → `test_identity_aggregator*` 不劣化。
- **Phase-C · 跨平台安装**（约 0.5d）：Linux/macOS 各跑 `bash install.sh --venv` → 回归全绿 → 记录 OS/依赖版本。
- **解冻条件**：A/B/C 三绿 → 才恢复 v2.2.0 发布收尾（备份 tar → ima 幂等注册 → 终检）。
- **验收总闸**：`cd /root/.skills/infoseek && python tests/run_tests.py` → 0 FAIL / 0 TIMEOUT。
- **状态**：⏸️ 暂缓——当前环境不支持真实环境/跨平台，待目标机就绪后按 A→B→C 执行。

---

## 三、2026-09-18 收尾记录（v2.1.0 OSINT 契约修复 + 双源聚合）

审计起点是原 P1「OSINT 双客户端真实实证」。沙箱 pip 实测 sherlock-project 0.16.2 /
maigret 0.6.5 后，发现 ROADMAP 未记录的 **P0 集成故障**，并交付三项产物：

- **P0 契约修复**：sherlock `--json` 实为站点数据输入、无 JSON 结果 → 改 `--csv` 读回；
  maigret `-J simple` 是报告类型且写文件、`status` 为嵌套 dict、`-a` 是全量站点 →
  改临时目录读 `reports/report_<user>_simple.json` + `--top-sites N`。修掉 sherlock
  username 写死空串致 cross_platform 统计失效的连带 bug。
- **双源聚合**（`core/identity_aggregator.py`）：旧 compensate 只取首个成功源、两源从不
  融合；现双源优先 + 归一去重 + 交叉确认置信增强 + 误报抑制 + 分区，全空回退单源代偿链。
- **授权 CLI**（`infoseek_consent_cli.py`）：闭合原 P1#3 代码侧；真实长驻授权仍靠
  shell export（设计上不改写只读 registry.yaml）。
- **测试脆弱性根因修复**：三处身份相关测试原依赖"本机恰好未装 CLI"，装了真 CLI 即挂死；
  统一改注入式 mock 显式锁定路径。新增 2 套件（聚合 24 + CLI 16 断言），客户端契约
  守护扩至 20 断言。版本 2.0.0 → **2.1.0**（MINOR）。
- 仍开放：上述真实环境三类冒烟（凭证 / OSINT 样本阈值 / 跨平台安装）。

---

## 三·续、2026-09-19 收尾记录（v2.1.2 事件槽批次 · GA11 阶段 1-3）

起点是 2.1.1 立案的 P3 三缺口（F-06 多事实句召回 / F-04 相邻季语义裁决 / F-07 time 路跨主体误报）；
其主体前置依赖已由 2.1.1-openfix-p3stage0（主体贯通）解除，本批按 GA11 事件槽重构阶段顺序落地：

- **F-06 事件抽取**：新增零依赖 `_split_clauses` / `_extract_events`，逐事件
  `{subject, aspect, value, polarity, time_span, event_sig}`；数值/方面由「全文单次扫描」改为
  **句内独立归属**（这才是 F-06 的原始缺口）；事件在**原始正文**抽取并随 claim 穿透
  （`conflict_v3._extract_fact_claims` → `_group_and_detect`），跨越 `text[:500]` / `text[:300]` 两级截断。
- **F-04 期间裁决**：新增 `_period_adjudicate`；同主体同方面且双方期间均已知且不相交 → 判为非冲突，
  落地为**降权（×0.5，不滤除、不归零）**；触发条件严格三连（含「空主体不裁决」以守旧契约）。
- **F-07 time 路主体闸**：`time_slot_score` 向后兼容扩展 `subject/subject_b/events_a/events_b`；
  同主体 → 事件级对齐（`_event_time_score`），跨主体 → 不可对拍（消除跨主体时间误报）。
- **research 链路字段穿透对齐**（异步两处 + 同步一处统一）。
- **零回归口径**：唯一行为变化点是「双方主体非空且相等 + 同方面 + 双方期间非空且不相交」，
  对应 `test_p1_hardening_v211` L105-109 语料（60→30），断言已按 F-04 语义更新并补两例。
- 版本 2.1.1 → **2.1.2**（PATCH）；守护 `tests/test_event_slots_v220.py`（47 断言 / 5 组）。

## 三·续二、2026-09-19 收尾记录（v2.2.0 三元事件身份 + 时间槽键 + LLM 收口 · GA11 阶段 1-3 续）

v2.1.2 后跨文本对齐仍用二元 `event_sig=(subject, aspect)`，不同期间的同主体同方面事件无法在事件层
区分；且 B 层 LLM 成功路径返回窄白名单 dict，截断 v2.1.2/v2.2.0 全部事件槽字段。本批：

- **阶段 1**：事件增 `time_key`（`{start,end,label}` 半开 `[start,end)`），单句多 span 事件**扇出**，
  跨文本签名升三元 `(subject, aspect, time_key)`；三类键严格分离。
- **阶段 2**：`_event_time_score` 三元对拍（同键才对；不同 time_key 按真实半开区间，overlap→conflict、
  全 disjoint→60/35，端点相接=disjoint，span 缺失保守不误判）；透出 `event_disjoint_slots`/
  `event_overlap_slots`/`event_pairs` 与多期间逐对 `period_pair_details`。
- **阶段 3**：B 层 LLM 槽表增时间维度（真实 span disjoint→suppression）；同步 `score_with_llm`
  **窄白名单截断根治**——以完整 A 层 `dict(local)` 为底；同步/异步/hybrid/batch/`infoseek_core_v2`/
  MCP wrapper 统一字段透传（审计无白名单）；纯 A 层默认补两个 suppression 空列表。
- 守护：`tests/test_event_slots_v220.py` **54 断言**；新增 `tests/test_llm_fields_v220.py` **26 断言**
  锁定五出口 A 层 31 字段完整超集契约（防窄白名单回归），并固化 R4 修复（router 返 None 旧被伪装成
  异常降级，同步/异步统一 raw 归一化）。
- 全量回归（`tests/run_tests.py` 唯一入口）**75 PASS / 0 SKIP / 0 FAIL ALL GREEN**
  （较 bump 前 66 套件新增 llm_fields）；版本单源守护 22 PASS。
- 版本 2.1.2 → **2.2.0**（MINOR：事件身份签名升级跨 2 核心模块）；联动 7 处版本号（含
  `core/__init__.__version__`）+ CHANGELOG/RELEASE_NOTES；代码内 `v2.1.2` 历史标注为历史锚点不改；
  B 层 LLM 默认关闭；默认不推 GitHub。

---

## 四、前景方向

- **短期**：把已实现能力从「测试验证」推向「生产可用」——真实凭证 / 真实 OSINT 样本阈值 /
  跨平台安装三类**必须在真实环境闭环**的冒烟（P1 #1-#3）。
- **中期（v2.x）**：矛盾检测叙述句召回、research 性能、口径旁路收口；与 QCM 等
  跨 skill 协同继续扩展（已有契约基础）。
- **长期**：AI Agent 深度协作、实时协作调研；坚持零依赖哲学
  （所有增强均有降级路径，可选依赖缺失不塌）。

---

## 五、验收总闸（每次合入）

- 全量回归全绿：`python tests/run_tests.py` → 0 FAIL / 0 TIMEOUT（SKIP 需为可选依赖 preflight）。
- 零破坏性变更：状态文件（`~/.infoseek/*.json`）/ env 向后兼容；清理类动作默认 dry_run。
- 版本单源：对外版本只改 `scripts/mcp_tools_common.py:SKILL_VERSION`，并与
  SKILL.md / manifest / package.json / RELEASE_NOTES / CHANGELOG 联动；四维版本语义不得混用。
- 文档同步：SKILL.md / README / 本路线图；历史实施细节入归档而非堆积本文件。
- 发布前：备份 tar（命名带章节/内容标识 + 解包校验）→ ima 幂等注册 → 嵌套终检
  （`find -maxdepth 2 -type d | grep "/infoseek/infoseek$"` 须零命中）。
