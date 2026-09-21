# Infoseek v2.2.0 发布说明

> 发布日期：2026-09-19 ｜ 版本：**2.2.0**（GA11 事件槽批次阶段 1-3：三元事件身份 + 跨文本时间槽键 + LLM 路径收口）｜ 许可证：MIT
> 前置：v2.1.2（F-06 事件抽取 + F-04 期间裁决 + F-07 time 路主体闸）。MINOR 定级：跨文本事件身份从
> 二元 `(subject, aspect)` 升级为三元 `(subject, aspect, time_key)`，跨 2 核心模块 + LLM 全路径字段收口。

## v2.2.0 核心改动

**主题**：把「时间」从对齐后的裁决维度，升级为**事件身份的一等组成**，并根治 LLM 成功路径的字段截断。

- **阶段 1 · 事件身份规范化**：事件 schema 增 `time_key`（`{start,end,label}`，半开 `[start,end)`）；
  单句多时间 span 时事件**扇出**（不再只取 `spans[0]`）；跨文本签名升级三元 `(subject, aspect, time_key)`。
  三类键严格分离（事件身份 ／ 单侧去重 `(subject,aspect,value,polarity,time_label)` ／ 时间关系用真实 span）。
- **阶段 2 · 跨文本时间槽键**：`_event_time_score` 三元对拍，不同 time_key 的同主体同方面事件按真实
  半开区间对拍，任一 overlap → conflict、全 disjoint 按 label 给 60/35；端点相接视为 disjoint；
  透出 `event_disjoint_slots`/`event_overlap_slots`/`event_pairs` 与多期间逐对 `period_pair_details`。
- **阶段 3 · LLM 与调用路径收口**：B 层 LLM 槽表增时间维度（`_llm_slot_span`/`_llm_slot_conflicts`，
  真实 span 可解析且 disjoint → suppression）；**同步 `score_with_llm` 成功分支原窄白名单截断全部事件/期间/
  keyed 字段，改为完整 A 层 `dict(local)` 为底**；同步/异步/hybrid/batch/`infoseek_core_v2`/MCP wrapper
  统一字段透传，纯 A 层默认补 `llm_time_suppressed*=[]`。

**兼容性**：返回体为超集（新增字段，老字段语义不动）；二元 `event_sig` 内部保留，三元对齐为新增事件层逻辑。
B 层 LLM 仍默认关闭（`INFOSEEK_CONTRADICTION_LLM=1` 启用）。默认不推送 GitHub。

**验证**：`test_event_slots_v220.py` 54 断言 + 新增 `test_llm_fields_v220.py` 26 断言（五出口 A 层 31 字段
完整超集契约，防窄白名单回归；固化 R4：router 返 None 不再被伪装成异常降级）；版本单源守护 22 断言；
全量回归 `python tests/run_tests.py` **67 PASS / 0 SKIP / 0 FAIL（231s）ALL GREEN**。

---

# Infoseek v2.1.2 发布说明

> 发布日期：2026-09-19 ｜ 版本：**2.1.2**（GA11 事件槽批次：事件抽取 + 期间裁决 + time 路主体闸）｜ 许可证：MIT
> 前置：v2.1.1（边界审计 P2 四项硬化；同日另有不 bump 的 P3 阶段0 主体贯通 openfix）。

## v2.1.2 核心改动

**主题**：把矛盾检测从「短语级信号」推进到「**结构化事件级对齐**」——GA11 事件槽重构的阶段 1-3。

- **F-06 事件抽取**（`_split_clauses` / `_extract_events`）：标点分句 → 逐事件
  `{subject, aspect, value, polarity, time_span, event_sig}`；数值/方面**句内独立归属**，
  修「全文单次扫描」导致的多事实句串味。事件在**原始正文**上抽取并随 claim 穿透
  （跨越 `text[:500]` / `text[:300]` 两级截断）。
- **F-04 期间裁决**（`_period_adjudicate`）：同主体同方面且双方期间均已知且不相交 → 判为非冲突，
  落地为**降权（×0.5，只降权、不滤除、不归零）**；期间相同/相交/缺失 → 走现值冲突规则；
  空主体不裁决（保持旧契约）。
- **F-07 time 路主体闸**（`time_slot_score`）：签名向后兼容地扩展主体参数与事件参数；
  同主体 → 事件级对齐，跨主体 → 不可对拍，**消除跨主体时间误报**。
- **research 链路字段穿透对齐**：单条冲突补齐 `verdict/time_coverage/keyed_score/conflict_slots/period_adjudication`。

**兼容性**：所有既有公开调用（2 参 `time_slot_score`、不带 `events` 的 `score_contradiction`）
行为不变；返回体为**超集**（新增字段，老字段语义不动）。B 层 LLM 槽维持默认关闭。

**验证**：新增 `tests/test_event_slots_v220.py`（47 断言 / 5 组）；全量回归
**65 PASS / 2 SKIP / 0 FAIL / 0 TIMEOUT**（67 套件，2 SKIP = jieba/pypinyin 可选依赖缺失）。

---

# Infoseek v2.1.1 发布说明

> 发布日期：2026-09-18 ｜ 版本：**2.1.1**（边界审计 P2 四项硬化）｜ 许可证：MIT
> 前置：v2.1.0（OSINT 身份归因 P0 契约修复 + 双源聚合 + 授权 UX；同日另有不 bump 的 P1 openfix）。
> PATCH 定级：4 项均为边界/安全缺陷硬化，无对外工具契约破坏性变更；但横跨 4 模块 + 词表 schema
> 扩展（strict_keywords/false_compounds）+ 新增清洗管线，按 Xes 版本规则（改动幅度 >30%）递增 PATCH 位。

## 本版要点
- **DEF-13** `detect_domain` 入参类型守卫（None/数字不再 AttributeError）。
- **DEF-15** legacy 短语袋路统一 50k 有界窗口，500k 长文本从 >150s 超时降到 <0.2s，三路窗口一致。
- **DEF-14** 报告输出层注入清洗（危险标签/on*=/危险协议，无 SSTI）+ CSV 公式注入防护（正常 markdown/数值零误伤）。
- **DEF-16** 中文 2 字歧义词「回测」误嵌守卫（strict_keywords + false_compounds 区间覆盖判定），yaml/内置双源同步。
- 新增守护 `tests/test_p2_v211.py`（70 断言）；P1 守护 41 断言与全量既有套件零回归。

---

# Infoseek v2.1.0 发布说明

> 发布日期：2026-09-18 ｜ 版本：**2.1.0**（OSINT 身份归因 P0 契约修复 + 双源聚合 + 授权 UX）｜ 许可证：MIT
> 前置：v2.0.0（GA11 键控事实槽 + GA12 网络边界门控）。MINOR 定级：修复 Maigret/Sherlock 客户端真实 CLI 契约（功能性），新增双源聚合引擎与授权 CLI，向后兼容。

## 一句话总结

**让身份归因从"测试通过但真实环境跑不通"变为"契约对齐真实 CLI + 双源交叉验证"**：
沙箱装包实测发现 sherlock 0.16.2 / maigret 0.6.5 的调用参数与输出解析与代码完全不符
（sherlock `--json` 是数据输入、无 JSON 结果；maigret `-J simple` 写报告文件、状态嵌套 dict），
重写两客户端后新增 **Maigret×Sherlock 双源聚合去重引擎**（平台/URL 归一去重、交叉确认置信增强、
高误报平台抑制、CN/IN/全局分区）与**能力授权 consent CLI**。全量回归
**62 PASS / 0 SKIP / 0 FAIL（91.4s）**，默认 OFF / 降级链 / 老契约不变。

## v2.1.0 核心改动

1. **P0 契约修复**：`sherlock_client` 改 `--csv --print-found` 读 CSV（回填 username）；
   `maigret_client` 改 `--top-sites N --no-recursion -J simple` 读报告文件 + 嵌套 status 解析。
2. **双源聚合**（新增 `core/identity_aggregator.py`）：去重 / 双源交叉 +0.10（封顶 0.99）/
   单源高误报平台丢弃、跨源复活 / 单源长尾降档 / CN/IN/global 分区。
3. **pipeline 接线**：`search_identity_attribution` 双源聚合优先，全空回退单源代偿链；
   锚点新增 attribution_sources / cross_source_confirmed / weak_single_source / region。
4. **授权 UX**（新增 `scripts/infoseek_consent_cli.py`）：list / grant / revoke / shell-init / doctor，
   grant/revoke 写 consent.log 审计并输出 export 引导（registry.yaml 保持只读）。
5. **测试加固**：消除三处"依赖本机未装 CLI"的环境脆弱性（注入式 mock，零网络）；
   新增 2 套件（聚合 24 断言、CLI 16 断言），客户端契约守护扩至 20 断言。

## 真实环境待办（本版不闭环）

真实登录源凭证冒烟、真实 OSINT 样本误报阈值校准（误报表为规则框架，Droners 等实测平台先行入表）、
Linux/macOS 安装实证。见 `references/ROADMAP.md` P1。

---

# Infoseek v2.0.0 发布说明

> 发布日期：2026-09-16 ｜ 版本：**2.0.0**（GA11 键控事实槽 C 混合分层 + GA12 网络边界门控）｜ 许可证：MIT
> 前置：v1.9.0（GA9 人名消歧 + GA10 跨语言别名桥接）。MAJOR 定级：GA11 更换矛盾评分的事实槽口径（判定语义变更）。

## 一句话总结

**P3 深水区落地**：GA11 把语义矛盾检测从「短语袋 Jaccard 相似度」重构为「同槽键值冲突」
键控事实槽（keyed slots），段落级口径差异从 **12/12 漏判（none）→ 8/8 真实差异召回、同义复述零误报**；
GA12 建立网络边界探测门控（probe 单源化 + 能力 host 声明 + 执行前门控），让沙箱受限能力
**显式降级留痕而非静默长超时**。两路均守既有设计哲学：默认 OFF / 降级链 / 老契约不变。
全量回归 **59 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT（90.0s）**。

## v2.0.0 核心改动

### GA11 · 键控事实槽（C 混合分层）

1. **数据真源** `references/contradiction-synonyms.json`：20 个方面谓词簇（中英）+ 8 方面互斥对
   + 否定词 + 主体停用词。
2. **A 层（默认零依赖）** `core/contradiction_scorer.py`：槽键=(主体键, 方面)，值=数值/枚举/极性；
   仅同键值冲突计分。主体契约驱动（entity/subject，不同主体不比）；数值全文单次扫描+左优先
   就近唯一归属（指标后侧窄窗、趋势词仅收百分比、年份专归 founded_year、季度/裸年份排除、
   「百分之二十」中文归一）；同值对跨槽去重；keyed 与否定路 **max 融合不叠加**。
   裁决：单冲突 35（medium）/2 槽 60/≥3 槽 85；长文本保护 50k/500/200 上限。
3. **B 层（opt-in）** `score_contradiction_hybrid()`（`INFOSEEK_CONTRADICTION_LLM=1`）：
   LLM JSON 槽表复用键控比对，表外方面动态键，温度 0 + 缓存，降级链 B→A→legacy。
4. **契约**：新增 `keyed_score`/`conflict_slots`/`scorer_mode`，老字段语义不变。
5. **质量**：12 组段落语料 8 真实差异全召回（4 强 ≥medium + 4 细微 low+），4 同义复述零误报；
   L1-01..06 逐条零回归；100 字 vs 300 字同判不稀释。守护测试 38 断言。

### GA12 · 网络边界门控（五步链，默认 OFF 零行为变更）

1. `scripts/net_probe.py` 探测唯一真源（TTL 进程内+磁盘缓存、幂等、并发、fail-safe）；
   engine_router/search_engine_health 重复 probe_http 收口为委托。
2. registry.yaml：12 能力补 requires_hosts + network_boundary；新增 WikiVerify/L4Transcribe/
   PatentLookup 三能力；network_boundaries 11-host 台账；core 访问器 + 内嵌默认同步。
3. `scripts/boundary_gate.py`：静态声明校验/实测可达/执行前 preflight（默认 OFF、fail-open）。
4. `references/network-boundary-report.md`：沙箱受限清单（目标机可重刷对照）。
5. `capability_compensator` 边界预检：不可达沿 degrade_to 显式降级 + boundary_restricted 留痕。

## 兼容性

- 矛盾评分老字段与 score→severity 主映射保留；GA11 仅新增字段，短句/极性对立判定零回归。
- GA12 门控默认关闭，未设置 `INFOSEEK_BOUNDARY_GATE=1` 时对现有抓取/代偿路径零影响。
- 无新增硬依赖（GA11-B 为 opt-in，复用既有 llm_router）。

## 验证

- 新增守护：`tests/test_ga11_slots_v200.py`（38）+ `tests/test_ga12_boundary_v200.py`（36）。
- 全量回归：59 套件 59 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT（90.0s）。

---

# Infoseek v1.9.0 发布说明

> 发布日期：2026-09-14 ｜ 版本：1.9.0（GA9 人名消歧五步 + GA10 跨语言别名桥接）｜ 许可证：MIT
> 前置：v1.8.4（GA5 分词单源化）→ v1.8.3（§8.4 三链路口径一致性）→ v1.8.2（遗留 4 缺口闭合）

## 一句话总结

**P2 人物调研补强落地**：闭合 ROADMAP §8.12.2 **GA9**（人名消歧五步：多音字姓氏白名单 →
复姓整词长匹配 → heteronym 全读音枚举 → person 实体族动态注册 → 拼音别名接 `_expand_query`）
与 **GA10**（跨语言别名桥接，修复 D1 英文源系统性低估）。实测 D1 实锤场景
`ualberta.ca` 官方一手源由 **9 分 ❌噪声 → 55 分 🟡潜力**；D2 场景英文源经拉丁拼音别名
命中中文人名实体（`Linjian Xiang` → 项林坚），人物调研融合链不再空转。

## v1.9.0 核心改动

1. **GA9① 数据真源** `references/person-surnames.json`：单姓 245 / 复姓 68（含 4 字复姓）/
   多音字姓氏 26（preferred + heteronym）/ 误报防护词 662 + 地名 47 + 地名后缀 56 + 虚词 143。
2. **GA9②③ 检测与别名** `core/person_ner.py`：复姓整词长匹配（禁逐字拆）；text/subject 双层模式
   （精度/召回分层）；姓氏多音字全枚举 + 名连写英文化（`xiang linjian` / `linjian xiang`）；
   五重守卫（虚词 / 常用词 jieba-FREQ / 地名后缀 / blocklist / 叠字）。
3. **GA9④ D2 闭合**：`bootstrap_subject()` 把人名注册进 person 实体族（运行时会话级），
   NER 对中文源按 `name` 命中、对英文源按注册别名（拉丁拼写）命中；冲突检测/图谱可按人名索引。
4. **GA9⑤ + GA10 桥接** `core/xling_bridge.py`：subject 的人名拼音别名组 + 词典实体拉丁别名组
   构成跨语言桥接；`score_source` fallback 与 `_filter_relevant` 双闸门命中即保底 55（多组至 70）；
   区分度守卫（常见英文词 blocklist）防误抬；env `INFOSEEK_XLING_BRIDGE` 可关。
5. **召回扩展**：`_expand_query` 追加拼音别名（与词典别名去重），提升英文源召回面。
6. **守护测试**：`test_ga9_person_v190`（40 断言）+ `test_ga10_xling_v190`（29 断言），
   含导入纪律 AST 守护（固化「禁 from-import」永久约束）。

## 零回归契约

| 项 | 保证 |
|----|------|
| 既有命中路径 | 桥接为 `max()` 抬底语义——原 Jaccard/containment 命中不受影响 |
| 中文×中文 | `xling_bridge=0`，env on/off 分数**恒等**（B3 断言） |
| algo-v 契约 | `score_source` 返回体 `version` 保持 **1.2.0**；仅新增 `xling_bridge` 字段 |
| RE1/RE2/RE3 | `_expand_query` 既有契约全保（BYD 扩展 / 无命中原样 / 异常防御） |
| 导入纪律 | `_anchor_mod`/`_person_mod`/`_xling_mod` 模块对象晚绑定，禁 from-import（AST 守护） |

## 升级注意

- **新增可选依赖** `pypinyin>=0.55`：未安装时检测/注册仍工作，拼音别名降级为空 +
  一次性告警（不阻断主链路）。
- env 开关：`INFOSEEK_XLING_BRIDGE`（默认开）/ `INFOSEEK_PERSON_NER`（默认开）；
  关闭后完全回退 v1.8.4 行为。
- 人名注册默认**会话级内存态**（零文件写入，无跨会话噪声累积）；需持久化可显式
  `register_person_runtime(..., persist=True)` 走 `learn_entity` 通道。

## 剩余路线

- **P3 / v2.x**：GA11 段落级事实槽重构（`contradiction_scorer` 长叙述句召回近零）、
  GA12 目标机闭环（沙箱网络边界受限项）。

---

# Infoseek v1.8.4 发布说明

> 发布日期：2026-09-13 ｜ 版本：1.8.4（GA5 分词单源化 + 局部 import 收口）｜ 许可证：MIT
> 前置：v1.8.3（§8.4 三链路口径一致性闭合）→ v1.8.2（遗留 4 缺口闭合）→ v1.8.1（版本号单源化治理）

## 一句话总结

**P1 治理收口**：闭合 ROADMAP §8.12.2 最后一个 P1 余项 **GA5「分词单源化」**——把
`anchor_adapter._tokenize_subject` 与 `infoseek_pipeline._tokenize_query` 两份并存的同构分词
实现抽为唯一真源 `scripts/text_tokenizer.py::tokenize_text()`，两侧退化为薄封装委托；连带收口
§8.12.4 点名的 `infoseek_pipeline.py:996-999`（函数体内 `sys.path.insert` + 局部 import）。
18 样本旧↔新**零差异**对拍，全量回归 **55 PASS / 0 FAIL（90.1s）**。

## v1.8.4 核心改动

1. **唯一分词真源**：新增 `text_tokenizer.tokenize_text(text, require_chinese=False,
   warn_on_fallback=False)`——jieba 优先（探测进程内缓存，避免热路径重复 try-import）→ 缺失
   回退纯 Python（中文连续段 ≤4 字整段 / >4 字滑窗 2-gram；英数 ≥2 字符整词；小写归一）。
   零 infoseek 内部依赖 → 天然无循环依赖，原「独立实现避免循环依赖」的理由消解。
2. **两侧退化委托**：`_tokenize_subject` 33 行 / `_tokenize_query` 32 行算法体各收敛为一句
   `return tokenize_text(...)`；保留函数名（兼容 `_string_containment_similarity` 调用点与
   `test_relevance_gate_v177` 断言）；`_RELEVANCE_WARNED` 移除（一次性告警迁真源）。
3. **有意差异显式参数化**：`require_chinese=True`（query 侧）——纯英文/数字 → 空集，服务
   `_filter_relevant`「中文多字词硬门槛」；subject 侧无门控（处理任意语言）。这是**唯一**
   保留差异，已由 G9/G10 断言锁定「含中文样本分叉恒为 0」。
4. **局部 import 收口**：删原 996-999 每次调用的 `sys.path.insert` + 局部 import → 顶层
   `import anchor_adapter as _anchor_mod` + 属性访问；300 次调用实测 `sys.path` **Δ=0**
   （原 +300，与 GA8 已治的 O(n²) 隐患同源）。
5. **新增守护测试**：`tests/test_ga5_tokenizer_v184.py`（170 行 / **24 断言**，6 组），含
   **G23/G24 mock 可patch性**——把本轮踩到的隐性契约固化为断言。

## 本轮最大教训：from-import 早绑定击穿 mock 契约

首次收口用顶层 `from anchor_adapter import compute_semantic_similarity,
_string_containment_similarity`，全量回归**击穿 3 套件 9 项断言**
（`test_p1p3p2_fixes` 12/15、`test_recall_enhance_v101` 13/16、`test_relevance_gate_v177` 9/12，
含 C2 `calls=0`）。

- **根因**：from-import 在导入期把函数对象**固化**进 pipeline 命名空间（早绑定）；测试替换的是
  `anchor_adapter` 的**模块属性**，晚替换无效。原「函数体内局部 import」虽是性能反模式，却每次
  重新取属性（晚绑定）——这是既有测试依赖的隐性契约，不只是路径注入。
- **修法**：模块对象 + 属性访问（调用时求值）→ patch 恢复生效，同时 `sys.path` 不膨胀。
- **铁律**：收口局部 import 时，若被导入对象存在 mock/monkey-patch 契约，必须用模块对象属性
  访问；**全量回归是唯一判据**（单套件自测发现不了）。

## 验收

| 项 | 结果 |
|----|------|
| 行为等价 | 备份 exec 旧实现，18 样本旧↔新**零差异**（含中/英/混排/空/单字/全角/标点） |
| 分叉口径 | 旧 3 == 新 3，全为纯英文/数字样本（有意门控 A）；含中文分叉 **0** |
| sys.path | 300 次调用 **Δ=0**（原 +300） |
| AST 收口 | `_filter_relevant` 体内 Import/ImportFrom/path.insert 节点 **0 命中** |
| 守护测试 | `test_ga5_tokenizer_v184` **24 PASS / 0 FAIL** |
| 全量回归 | **55 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT / 0 KNOWN，90.1s** |
| 版本联动 | SKILL.md / mcp_tools_common.SKILL_VERSION / core.__version__ / manifest.yaml 四处 1.8.4 |

## 升级注意

- **行为零变更**：本版为等价重构，评分/门控/召回口径均不变，无需调整任何调用方或 env。
- 若你在外部代码里 patch 过 `anchor_adapter` 的相似度函数，**继续可用**（G23/G24 已守护）；
  但请勿把 pipeline 顶层的 `import anchor_adapter as _anchor_mod` 改回 from-import。
- jieba 仍为可选依赖：未安装时自动回退纯 Python 并发一次性 WARNING（精度下降，建议安装）。

## 剩余路线

- **P2 / v1.9.0**：GA9 人名消歧五步（多音字白名单 → 整词长匹配 → heteronym 枚举 → person
  实体族 → 拼音别名接 `_expand_query`）、GA10 跨语言别名桥接（英文一手源被中文 query 系统性低估）。
  **P1 5/5 全闭合 → P2 启动条件已满足**。
- **P3 / v2.x**：GA11 段落级事实槽重构、GA12 目标机闭环（沙箱网络边界受限）。
