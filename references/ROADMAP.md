# Infoseek 路线图（当前基线 · 待办 · 前景方向）

> 版本：v2.1.0 ｜ 更新：2026-09-18
>
> 本文件只保留**面向当前**的内容：已达成基线、真实未完成待办、前景方向、验收总闸。
> 各历史版本的详细实施记录 / 缺口审计 / 设计裁决已归档至
> **`ROADMAP_archive_20260917.md`**（v1.0.0 → v2.0.0 全量脉络，1088 行）。需要追溯
> 「某 GA 在哪个版本、为何这么设计、踩过什么坑」时查归档，不在本文件堆积。

---

## 一、当前基线（v2.1.0）

| 维度 | 状态 |
| --- | --- |
| 对外版本 | SKILL_VERSION **2.1.0**（唯一真源 `scripts/mcp_tools_common.py`）；openfix 批次不 bump |
| 全量回归 | **61 PASS / 2 SKIP / 0 FAIL / 0 TIMEOUT**（63 套件，80.3s，2026-09-18；2 SKIP=jieba/pypinyin 可选依赖缺失） |
| openfix 批次（2026-09-18，版本号不变） | P0-OPEN-04 score 量纲归一化+禁空骨架 / P0-OPEN-06 时间事实槽+无冲突·未评估区分 / P0-OPEN-05 GPT-5 路由+模板按域收窄+跨源综合段 / P1 README 对齐 19 工具+run_tests SKIP 判定修复；守护 `test_open_fixes_v210.py`（60 断言） |
| 版本体系 | 四维分离：SKILL_VERSION（对外）/ mod-v（模块内部）/ algo-v（下游契约）/ proto-v（MCP） |
| OSINT 客户端 | sherlock v0.16.x（`--csv` 读结果）/ maigret 0.6.x（`-J simple` 报告文件 + 嵌套 status）契约对齐实测 |
| 身份归因 | Maigret×Sherlock **双源聚合去重**（`core/identity_aggregator.py`：交叉确认 +0.10/误报抑制/分区）+ AccountTrustScorer 验证 |
| 授权 UX | `scripts/infoseek_consent_cli.py`（list/grant/revoke/shell-init/doctor + consent.log 审计） |
| 评分口径 | 唯一聚合真源 `aggregate_score_v2()`（base 四维 + 独立加权层），三链路口径一致 |
| 分词真源 | `scripts/text_tokenizer.py` 单源（jieba 可选，缺失纯 Python 回退） |
| 人物调研 | GA9 人名消歧五步（`core/person_ner.py` + person-surnames.json）/ GA10 跨语言别名桥接（`core/xling_bridge.py`，pypinyin 可选） |
| 冲突检测 | GA11 键控事实槽（`core/contradiction_scorer.py`：keyed A 层 + LLM opt-in B 层，替代旧短语袋 Jaccard）+ P0-OPEN-06 **时间事实槽**（年月/季度→ISO 区间，第三路 max 融合）+ verdict 三态（conflict/no_conflict/not_assessable）+ 覆盖率 |
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
9. **合规审计增强**——抓取合规 / 版权 / 凭证审计的自动化报告。
10. **本地文件集成**——按用户反馈触发（不预设）。

### 明确不做（设计边界）

实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话——交由更专业的专用工具。

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

## 四、前景方向

- **短期**：把已实现能力从「测试验证」推向「生产可用」——真实凭证 / 真实 OSINT 样本阈值 /
  跨平台安装三类**必须在真实环境闭环**的冒烟（P1 #1-#3）。
- **中期（v2.x）**：矛盾检测叙述句召回、research 性能、口径旁路收口；与 QCM 等
  跨 skill 协同继续扩展（已有契约基础）。
- **长期**：AI Agent 深度协作、实时协作调研、合规审计自动化；坚持零依赖哲学
  （所有增强均有降级路径，可选依赖缺失不塌）。

---

## 五、验收总闸（每次合入）

- 全量回归全绿：`python3 tests/run_all.py` → 0 FAIL / 0 TIMEOUT（SKIP 需为可选依赖 preflight）。
- 零破坏性变更：状态文件（`~/.infoseek/*.json`）/ env 向后兼容；清理类动作默认 dry_run。
- 版本单源：对外版本只改 `scripts/mcp_tools_common.py:SKILL_VERSION`，并与
  SKILL.md / manifest / package.json / RELEASE_NOTES / CHANGELOG 联动；四维版本语义不得混用。
- 文档同步：SKILL.md / README / 本路线图；历史实施细节入归档而非堆积本文件。
- 发布前：备份 tar（命名带章节/内容标识 + 解包校验）→ ima 幂等注册 → 嵌套终检
  （`find -maxdepth 2 -type d | grep "/infoseek/infoseek$"` 须零命中）。
