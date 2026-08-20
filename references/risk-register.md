# Infoseek 风险注册表（Risk Register）

> 版本：v1.0.1 ｜ 状态：✅ 已提供 ｜ 对齐全：代码注释 + 测试套件

## 1. RPN 风险矩阵（Top 风险）

| ID | 风险 | 严重度 S | 概率 P | RPN | 状态 | 工程控制 |
|----|------|---------|--------|-----|------|----------|
| R06 | Tier2/Tier3 程序化缺失 | 4 | 4 | 16 | ✅ 已控 | `degradation_router` 日志告警 + 凭证引导 |
| R11 | DuckDuckGo API 限流 | 3 | 4 | 12 | ✅ 已控 | 1.5s 间隔 + 429 熔断 + Wikipedia 兜底 |
| R14 | 无单元测试 | 5 | 3 | 15 | ✅ 已控 | 16 文件测试套件（run_tests.py 聚合） |
| R05 | 名称搜索单引擎 | 4 | 3 | 12 | ✅ 已控 | 搜索引擎降级链（DDG/Bing/Jina/Wiki） |
| R21 | 搜索主题相关性跑偏 | 4 | 3 | 12 | ✅ 已控 | `_filter_relevant` 语义过滤（v1.0.1） |
| R22 | 中文主题评分过低 | 4 | 3 | 12 | ✅ 已控 | 字符串包含兜底评分（v1.0.1） |
| R23 | pytest 聚合崩溃 | 4 | 3 | 12 | ✅ 已控 | `tests/run_tests.py` 聚合入口（v1.0.1） |
| R24 | Windows 平台崩溃（resource） | 3 | 3 | 9 | ✅ 已控 | `HAS_RESOURCE` 守卫（v1.0.1） |
| R25 | 文档与实现漂移 | 3 | 4 | 12 | ✅ 已控 | 契约文档补齐 + SKILL.md 对齐（v1.0.1） |

## 2. 监控计划

| 维度 | 方式 | 触发动作 |
|------|------|----------|
| 测试回归 | `python tests/run_tests.py` | 任何 FAIL → 阻止发布 |
| 性能漂移 | `dist/quality_baseline.json` 对比 | 关键指标劣化 >10% → PATCH |
| 搜索可用性 | 引擎超时/429 日志 | 连续失败 → 检查降级链配置 |

## 3. 环境感知跳过

- POSIX-only（`import resource`）→ Windows 自动跳过内存维度
- 可选依赖缺失（jieba/summa/playwright）→ 自动降级零依赖 NLP / L1 抓取
- 网络超时 → 引擎级降级，不误报 FAIL

## 4. 已知限制（如实声明）

- `fetch_content` L2-L4 仍为函数壳（L1 静态抓取 v1.0.1 已落地）
- 搜索质量依赖网络环境：DDG/Jina 受限时仅 Bing 存活，中文召回偏低（建议配 Exa/Tavily key）
- references 契约文档 v1.0.1 补齐（此前 8 份缺失）

## 5. 升级方向（风险驱动的 Roadmap）

| 优先级 | 方向 | 对应风险 |
|--------|------|----------|
| P0 | 搜索→评分→报告链路（已修） | R21/R22 |
| P1 | 正文抽取 L2-L4 真实集成（playwright） | R06 |
| P2 | 多模态扩展 / 跨工具编排 | R05 演进 |
