# Changelog

## [1.4.1] - 2026-08-30

### 新增（P0/P1 能力增强）

- **public-apis 免费 API 目录**（`scripts/public_apis_catalog.py`）：README→本地 JSON 索引（1712 条/51 分类/799 无 key），关键词/分类/认证检索，离线内嵌集兜底；注册为 `free_api` 能力族（L0 免费优先层）
- **账号人因验证器**（`scripts/account_trust_scorer.py`）：成熟度/粉丝/行为/内容四维评分 → real/bot/suspicious/unknown，纯规则零依赖；`identity_attribution` 能力族，consent 闸控
- **三级路由**（`scripts/tiered_router.py`）：意图识别（finance/sentiment/identity/tech/general）→ L0 免费 → L1 网关 → L2 专用 → 人工核实；免费优先、credits 预算保护
- **AgentKey 网关适配器**（`ecosystem/adapters/agentkey.py`）：MCP find_tools→describe_tool→execute_tool 骨架（金融子集优先，社交默认 OFF），mcp 缺失优雅降级；`gateway_api` 能力族
- **注册表 v2**：新增 kind `free_api` / `gateway_api`；7 能力双源一致（YAML + 内嵌默认）

### 修复

- `infoseek_zerodep_nlp.py` ZD3 关键词提取：min_count=2 空回退 min_count=1 重建（单次短语列表召回丢失）

### 测试

- 新增 `tests/test_capability_extension_v101.py` 17 用例；全量回归 29/29 PASS
