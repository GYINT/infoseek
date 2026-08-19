# Infoseek v1.0.0 发布说明

> 发布日期：2026-08-19 ｜ 版本：1.0.0（首个发布版本）
> 许可证：MIT ｜ 类型：开源
> 发布包：`infoseek-v1.0.0.zip`（约 985 KB，299 文件，泄漏扫描 0 命中）

---

## 一句话总结

Infoseek 是一套**端到端的内容智能采集与调研工作流**——从行业/主题/人名输入开始，自动嗅探信息源、四维评分门控、深度抓取、矛盾检测、实体图谱，最终输出结构化 Markdown 报告。**纯 Python、零关键依赖、可跨生态部署**。

v1.0.0 是首个对外发布版本，凝聚了从内测到多生态适配的全部能力，并以**纯净发布包**形式提供分发。

---

## 核心特性

| 能力 | 说明 |
|---|---|
| **多源锚点发现** | 行业/主题/人名 → 自动嗅探候选源（DDG HTML / Bing RSS / Wikipedia 真实结果兜底） |
| **4 维评分门控** | 主题一致性 + 可信度 + 互动深度 + LLM 可读性 → 滤掉低质源 |
| **跨源矛盾检测** | 共享事实槽 + 否定词典 + 极性放大 → 自动识别"同一事实的不同表述" |
| **实体图谱** | 95+ 实体类型 + 多语种 + 别名归并 + 跨会话沉淀 |
| **结构化报告** | 观点+数据+来源的 Markdown 报告，含 Wikidata 验证、热度预测 |
| **本地持久化** | 状态/实体/归档自动落 `~/.infoseek` 与 `~/infoseek-archives`（**不污染技能目录**） |
| **零依赖核心** | NLP 关键词抽取 / 句子切分 / 摘要可在**纯标准库**下运行（无 jieba / summa 也能用） |

## 多生态适配（v1.0.0 落地）

发布包内含 **7 个生态的安装/注册产物**：

| 生态 | 形态 | 状态 |
|---|---|---|
| **WorkBuddy** | 本地 SKILL 包 | ✅ 直接可用 |
| **ima.copilot** | 注册清单（stdio） | ✅ 直接可用（按平台规范提交注册） |
| **Claude Desktop** | mcp.json（stdio 本地 + SSE 远程） | ✅ 直接可用 |
| **通用 MCP** | 平台无关配置 | ✅ 任意 MCP 客户端可直连 |
| **Dify** | 插件包（Manifest + Python 实现 + 图标/隐私） | ✅ 结构对齐，待平台导入核验 |
| **Coze** | PluginManifest + 13 操作 OpenAPI | ✅ 结构对齐，待平台导入核验 |
| **Codex / Dify 工作流 / Coze 插件** | 同 MCP 通用配置 | ✅ 直连 |

## MCP 工具面（v1.0.0 收敛）

| 类别 | 工具 |
|---|---|
| **规范（13）** | `search_anchors_async`, `fetch_content_async`, `save_archive_async`, `check_dedup_async`, `dedup_stats_async`, `fuse_analysis_async`, `cross_subject_analysis_async`, `summarize_content_async`, `conflict_detection_async`, `score_source_async`, `score_contradiction_async`, `research_v3`, `research_stream` |
| **REST 桥** | `POST /tools/<tool_name>`（Bearer 鉴权）—— Coze / Dify 按 OpenAPI 导入的平台可按独立端点调用 |
| **废弃并存期（12）** | 11 个 sync + `research`（仍响应，结果附 `deprecated: true` + `migrate_to` 标记） |

## 传输与托管

- **stdio**（本地首选）｜**SSE+token**（远程一级部署）｜**HTTP**（平台兼容）
- 远程托管 CLI：`python scripts/infoseek_host.py start [--port] [--host] [--token]`
  - 进程**脱离父进程组**（setsid / DETACHED_PROCESS），父 CLI 退出后托管存活
  - 健康检查 + token 鉴权 + 每生态连接信息（Claude mcpServers 片段 / 扣子 plugin 入口）

## CI 质量门控

发行前统一检查包含：

- 编译（55+ 文件）→ 零依赖冒烟 → 构建 `--check` → 13 套件（含 `test_tools_surface` 工具面收敛）
- **环境感知跳过**：POSIX-only / 可选依赖缺失 / 网络超时 → 标记 `⏭️ SKIP(原因)`，不因环境差异误报
- 产出 `dist/quality_baseline.json` 质量基线（替代上游缺失的 perf_baseline 机制）
- 14 套件测试稳定基线：**10 通过 / 4 环境跳过 / 0 失败**

---

## 安装与快速开始

### 方式 1：WorkBuddy 本地 SKILL

```bash
# 解压发布包后
cp -r infoseek-v1.0.0/workbuddy <workbuddy-skills-dir>/infoseek
# 技能系统加载即可
```

### 方式 2：Claude Desktop 本地 stdio

编辑 Claude 配置，添加 `dist/claude/mcp.json` 的 `mcpServers.infoseek-search` 条目（路径占位 `${INFOSEEK_ROOT}` 替换为实际技能根目录）。

### 方式 3：远程 SSE 托管（多客户端共享）

```bash
python scripts/infoseek_host.py start --port 8765
# 任何 MCP 客户端：URL=http://127.0.0.1:8765/sse, Authorization=Bearer <token>
```

### 方式 4：Coze / Dify 插件

- **Coze**：`dist/coze/openapi.json`（13 操作 OpenAPI）→ 在扣子「资源库 → 插件 → 导入」
- **Dify**：`dist/dify/` 整个目录作为插件包上传（manifest + provider + tools + Python 实现 + 图标 + 隐私）

---

## 已知限制（如实声明）

- **运行时托管需 Python 环境**：Coze / Claude SSE / Dify 云端调用需先 `infoseek_host.py start`；stdio 形态完全本地
- **`fetch_content` 4 级降级 L2-L4 仍为函数壳**（T1 直取可用，L2-L4 标注"需选装"或真实集成爬虫如 crawl4ai/Scrapling）
- **Dify 插件需 `dify_plugin` SDK** 调试（生产环境无此依赖）
- **缺少真实远程主机时**：远程形态不可用；本地 stdio 形态完全可用

## 路线图

| 版本 | 计划 |
|---|---|
| v1.0.x | ima v1.0.0 重新注册；Dify/Coze 平台导入微调；根据真实用户反馈沉淀需求 |
| v1.1.x | `fetch_content` L2-L4 真实集成（按需选装 crawl4ai/Scrapling） |
| v1.2.x | 远程托管平台化（一键容器化部署） |
| v2.x | 多模态增强（图像/文档附件分析）、共享知识库深化 |

---

## 致谢

- 上游 **infoseek** 项目（expeditionhub/infoseek v1.4.0 起）奠定的核心架构
- 各依赖库的开发者（duckduckgo / Bing / Wikipedia / MCP 生态）
- 所有早期内测与反馈者

## 反馈与贡献

- 仓库：（发布时填入）
- Issues / Discussions：按平台选择
- 许可证：MIT（详见 `LICENSE`）

---

**Infoseek v1.0.0 — 让每一次调研都更可靠。**
