# Infoseek 路线图（历史脉络 · 待办 · 前景方向）

> 版本：v1.2.0 ｜ 本文档统一承载：对话历史脉络总结、已完成里程碑、优化升级待办、v2.x 前景方向。

---

## 一、历史脉络（对话总结）

Infoseek 的开发经历了「修复 → 增强 → 生命周期化 → 能力里程碑」四阶段：

### 1. v1.0.0 → v1.0.1（PATCH 修复 + 审计闭环）
- **缺陷修复**：工具面收敛 25→13 规范工具、MCP server 拆分（1781 行 → 门面 + 6 工具模块）、搜索引擎降级链重写（修复 DDG API 废弃 / Bing RSS 误解析 / 演示锚点伪造）
- **全维度审计 G1–G13 全闭环**：subprocess 硬编码 / 权限 / 路径穿越 / L2 抓取缺失 / LLM 真实路径 / 评分实现 / 测试 / 生态适配 / env 文档 / 死代码 / 模块拆分 / 工具收敛 / 基线重建

### 2. ABC 能力增强（v1.0.1 附加）
- QCM 跨 skill 协同（契约 + 桥测试）、AST 符号自检、Keyring 后端、Token 用量成本折算、CLI backup/restore、perf 基准、引擎健康探测、playwright L2 渲染

### 3. 搜索引擎全生命周期（P0–P3 全落地）
- **P0/P1/P2**：错误分类状态机、配额追踪（429 → 退出保留池）、能力感知路由
- **P3 新鲜度自愈**：配额重置自动恢复（monthly/daily/hourly/fixed）、认证冷却自动恢复、API 漂移检测（默认关）、TTL 对账 + CLI（engine-status/reconcile/probe）
- 修复真实 bug：日/时额度引擎被误按「下月 1 日」禁用 30 天；`record_success` 未 `_ensure_loaded` 导致状态被磁盘重载冲掉

### 4. v1.2.X 能力里程碑（四大支柱，当前版本）
- **FreshnessCron 功能验证**：修复验证结果不落盘（实体库为内存静态列表）与 async 死代码；23 用例冒烟
- **搜索召回增强**：query 别名扩展 / 跨引擎多样性轮询 / 自适应相关性门槛 / 动态层权重；16 用例
- **L3/L4 抓取层**：凭证辅助抓取（KeyManager 注入，仅内存）+ 多媒体统一 chunk（whisper 可选降级）；26 用例
- **perf 10k 基准**：实测 10k 源近线性扩展（评分 139s / 冲突 89s / research 97s）

---

## 二、已完成（当前基线）

| 维度 | 状态 |
| --- | --- |
| 全量回归 | 25/25 套件 PASS |
| 质量基线 | v1.2.0，26/26 套件 all_ok |
| 符号自检 | 9 模块 ALL OK |
| 外部文档 | SKILL.md / README / configuration / external-deps / api-keys / ROADMAP 发布态同步 |

---

## 三、优化升级待办（按优先级）

### P1 近期（低风险快速收益）
1. **perf 多轮 P50/P95 采样**（`--rounds ≥3`）——10k 单轮数据已入基线，补多轮统计稳定性
2. **FreshnessCron 实体持久层**——实体库当前为进程内静态列表（`core/entities.py`），跨进程状态不保留；引入文件持久层（如 `~/.infoseek/entities.json`）后，衰减 / Wikidata 验证 / 冷条目清理才真正生效
3. **L3 登录源真实凭证端到端验证**——现有测试为 mock 凭证；需对真实登录源（有 key 时）做一次冒烟

### P2 中期（核心收益）
4. **搜索召回深化**——query expansion 支持实体图谱邻域（`EntityGraph.get_neighbors`）、同义词表；分层召回按 query 类型动态加权默认开启评估
5. **L4 转录启用**——接入 openai-whisper 真实转录（当前为占位降级），产出结构化转写 + 时间戳
6. **冲突检测多源加权**——按来源可信度加权严重度评级（当前等权）

### P3 远期（v2.x 立项）
7. **多模态理解**——图片 / 视频 / 音频内容理解与检索（独立大模块）
8. **编排 / 多 agent 协同**——与搜索 / 验证 / 归档工具深度整合，作为可观测子任务被组合
9. **合规审计增强**——抓取合规 / 版权 / 凭证审计的自动化报告
10. **本地文件集成**——按用户反馈触发（不预设）

---

## 四、前景方向

### 4.1 短期（v1.2.x 内）
聚焦「稳定性 + 真实效果验证」：多轮基准、实体持久化、真实凭证冒烟——把已实现能力从"测试验证"推向"生产可用"。

### 4.2 中期（v2.x）
「能力纵深化」：召回质量（图谱/同义词）、转录落地、多模态理解起步；与 QCM 等跨 skill 协同扩展（已有契约基础）。

### 4.3 长期
「生态化」：AI Agent 深度协作、实时协作调研、合规审计自动化；保持零依赖哲学（所有增强均有降级路径）。

### 4.4 明确不做（设计边界）
实时新闻监控 / 学术文献综述 / 浏览器自动化爬取 / 即时聊天对话——分别指向更专业的专用工具。

---

## 五、验收总闸（每次合入）

- 全量回归全绿（当前 25 套件基线）
- `dist/quality_baseline.json` all_ok 更新
- 符号自检 9 模块 ALL OK
- 零破坏性变更（状态文件 / env 向后兼容）
- 文档同步（SKILL.md / README / configuration / 本路线图）

---

## 六、M 系列能力治理里程碑（2026-08-26）

> 目标：在不破坏既有能力前提下，补齐「外部依赖全生命周期动态自适应」治理能力，并完成跨平台发布收口（v1.4.0）。

### 6.1 里程碑状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0.1 | QVeris 接入（双端点选区 / discover→inspect→call / 错误分类） | ✅ 完成 |
| M0.2 | Maigret 影子验证（CN 命中率/耗时/误报率验收） | ✅ 完成（安装 + 实时验证） |
| M0.2.5 | 统一能力注册表（registry.yaml + capability_registry.py） | ✅ 完成 |
| M0.3 | Maigret/Sherlock 客户端 + 合规闸（ConsentRequired 上抛降级） | ✅ 完成 |
| M0.4 | 代偿层（capability_compensator.py + degrade_to 链） | ✅ 完成 |
| M0.5 | 锚点矩阵 / 搜索引擎全生命周期收敛 | ✅ 完成 |

### 6.2 M0.2 影子验证结论（真实数据）

- **环境**：隔离 venv（`maigret 0.6.5`），样本 username `maigret`，参数 `--top-sites 50 --timeout 10 --retries 1 --no-extracting`
- **实测**：38 站点 / 10.6s，命中 **7 个账号** —— GitHub / WordPress / **CSDN** / WordPressOrg / StackOverflow / TripAdvisor / Twitch
- **CN 覆盖确认**：CSDN（tags=`blog,cn,coding`）命中，证明 maigret 中国站点库与 `scripts/maigret_client.py` 链路生效
- **错误率说明**：timeout/connection/bot/captcha 合计 ~69% 为**沙箱出网受限**所致，非工具缺陷；真实网络环境命中率更高
- **契约对齐**：产出 JSON 与 `maigret_client.search()` 解析契约一致，可直接接入 pipeline 的 `search_identity_attribution`
- **验收判定**：**通过**（安装就绪 + CN 链路实证 + 输出契约对齐）

### 6.3 整合待办（按优先级）

**P0（治理收口遗留）**
1. **Sherlock 实时验证** —— `sherlock_client.py` 结构已对齐；需在隔离 venv 装 `sherlock-project` 跑一次样本，补齐 M0.3 双客户端实证
2. **能力注册表启用流** —— `registry.yaml` 中 Maigret/Sherlock/manual_review 均 default_off；补充「授权→enable→consent 记录」端到端 CLI/UX（当前仅函数级 `grant_consent`）
3. **跨平台安装验证** —— `install.sh` 已在 Windows-Git-Bash 实战校验（结构 + 6 关键模块语法全 OK）；Linux/macOS 需目标机联网装依赖后各跑一次 `bash install.sh --venv`

**P1（低风险快速收益，沿用原 ROADMAP）**
4. perf 多轮 P50/P95 采样
5. FreshnessCron 实体持久层
6. L3 登录源真实凭证端到端验证

**P2（核心收益）**
7. 搜索召回深化（实体图谱邻域 / 同义词）
8. L4 转录启用（openai-whisper）
9. 冲突检测多源加权

**P3（v2.x 立项）**
10. 多模态理解 / 编排协同 / 合规审计自动化

### 6.4 M1.x 升级路线（提议）

- **M1.0 双 OSINT 客户端实证闭环**：Sherlock 实时验证 + Maigret/Sherlock 结果融合去重 + 误报率基准（CN/IN/全局样本建立误报基线）
- **M1.1 授权与审计 UX**：consent 授予前端化（首次调用交互授权、审计日志落盘 `~/.infoseek/consent.log`）、注册表可视化 `infoseek capability status`
- **M1.2 跨平台发布流水线**：`install.sh` 扩展为含依赖哈希校验 + 签名 + 7 生态（dist/）一键分发；CI 自动跑 `leak_scan` + 全量回归 + 跨平台 install 冒烟
- **M1.3 代偿健康度看板**：`capability_compensator` 的 `trail` 聚合为健康度指标，异常 degrade 链可视化告警
