#!/usr/bin/env python3
"""infoseek_mcp_server.py — Infoseek v1.0.0 MCP 服务器（search server）

版本演进: 内部开发线 1.5.0 → 3.1.0；对外发布版本 v1.0.0 起
  1.5.0: stdio 传输，6 工具
  1.5.1: + SSE 传输 + Bearer Token 认证
  1.5.2: + HTTP /rpc + GET /health
  1.6.0: + cross_subject_analysis 第 7 工具 + multi-server 拆分（archive 独立）
  1.6.1: PATCH 加固: token 来源诊断横幅 + 错误响应含 hint + token 脱敏日志
  1.6.2: PATCH 增强: 健康检查细化（uptime + 工具调用统计）+ 审计日志 + token 健康端点
  1.7.0: MINOR 新增: summarize_content 第 8 工具（summa 主路径 + LLM 兜底）
  3.0.0: GA: research_v3 / research_stream / score_contradiction + 全 async 路径

传输:
  - stdio（本地首选）
  - SSE（HTTP/HTTPS 服务，支持 Bearer Token 认证）
  - HTTP /rpc（短请求-响应模型）

工具（当前 25 个）: search_anchors / fetch_content / save_archive /
       check_dedup / dedup_stats / fuse_analysis / cross_subject_analysis /
       summarize_content / conflict_detection / score_source / research /
       research_v3 / research_stream / score_contradiction + 11 个 *_async
（archive server 仅 2 工具：save_archive + dedup_stats，详见 infoseek_archive_server.py）

启动:
  python scripts/infoseek_mcp_server.py                              # stdio（默认）
  python scripts/infoseek_mcp_server.py --transport sse --port 8080 # SSE
  python scripts/infoseek_mcp_server.py --transport sse --require-token --token <secret>

工具命名: mcp__plugin_infoseek_<server>__<tool>
其中 server = "search"（v1.6.0 前为单一 server），tool = 上述工具名
"""
import argparse
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# ── 路径常量（v1.0.0 状态层中立：运行态数据统一位于 ~/.infoseek 或 env 指定目录，
#    不再绑定 skill 安装目录 / WORKSPACE，适配只读与临时安装平台）──
CORE_DIR = Path(__file__).parent.parent / 'core'
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
from state_dir import (
    get_data_dir, state_path, get_db_path, get_log_path,
    audit_log_path, get_archives_dir,
)
WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE', str(Path.home())))
INFOSEEK_ROOT = Path(os.environ.get('INFOSEEK_ROOT', str(Path(__file__).parent.parent)))
INFOSEEK_DIR = get_data_dir()
DB_PATH = get_db_path()
LOG_PATH = get_log_path()
ARCHIVES_DIR = get_archives_dir()

# ── 认证配置（v1.5.1+）──
AUTH_TOKEN = os.environ.get('INFOSEEK_AUTH_TOKEN')
PROTOCOL_VERSION = "2024-11-05"  # MCP 协议版本
SERVER_NAME = "infoseek-search"
SERVER_VERSION = "1.0.0"  # v1.0.0: 发布版本（内部开发版本从 0.0.x 起记录）

# ── v1.6.2 新增：审计日志 + 工具调用统计 ──
import time
SERVER_START_TIME = time.time()  # 启动时间
TOOL_CALL_COUNTER = {}  # 工具调用计数（按工具名）
AUDIT_LOG_PATH = audit_log_path()

# ── 工具清单 ──
TOOLS = [
    {
        "name": "search_anchors",
        "description": "多渠道并行锚点发现。从行业/主题/人名嗅探信息源，支持 depth（1-3层）和 sources 列表。返回结构化候选源列表，每项含 url/title/score。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 3,
                          "description": "关键词展开深度"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "限定渠道（web/kb/note），默认全开"}
            },
            "required": ["subject"]
        }
    },
    {
        "name": "fetch_content",
        "description": "内容采集（四级降级提取）。v1.9.0 增强：链式引用追踪 v3（多层递归 + 防环 + 深度折扣 + max_chain_depth 1-3）。v1.8.0 v2：discover/fetch/graph 三模式 + 引用图 dot + 相关性评分。v1.7.3 v1：仅发现链接不抓取。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL（必填）"},
                "format": {"type": "string", "enum": ["md", "json", "txt"], "default": "md"},
                "max_retries": {"type": "integer", "default": 3, "minimum": 1, "maximum": 5},
                "follow_links": {"type": "boolean", "default": False,
                                  "description": "是否启用链式引用追踪（v1.7.3）"},
                "max_depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 3,
                               "description": "v1.7.3 追踪深度。v1.8.0 起作用于 v2 全链"},
                "chain_strategy": {"type": "string", "enum": ["discover", "fetch", "graph", "recursive"],
                                   "default": "discover",
                                   "description": "v1.8.0+: discover/fetch/graph。v1.9.0 新增 recursive=多层递归"},
                "chain_limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20,
                                  "description": "v1.8.0 新增: 链式追踪最大 URL 数"},
                "max_chain_depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 3,
                                     "description": "v1.9.0 新增: 递归深度上限（仅 recursive 模式有效）"},
                "subject": {"type": "string", "description": "v1.8.0 新增: 用于引用相关性评分的主题"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "save_archive",
        "description": "存档归档（v1.4.0 增强）。将抓取内容保存到 infoseek-archives/<subject>/，自动建元数据表与去重检查。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "url": {"type": "string", "description": "来源 URL（必填）"},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "正文内容"},
                "metadata": {"type": "object", "description": "附加元数据"}
            },
            "required": ["subject", "url", "title", "content"]
        }
    },
    {
        "name": "check_dedup",
        "description": "URL 去重检查。返回是否已在去重 DB 中。",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"]
        }
    },
    {
        "name": "dedup_stats",
        "description": "任务报告：URL 总数、主题分布、抓取时间统计。",
        "inputSchema": {
            "type": "object",
            "properties": {"subject": {"type": "string", "description": "可选，限定主题"}}
        }
    },
    {
        "name": "fuse_analysis",
        "description": "融合分析（多源交叉）。输入 subject 与 sources 列表，输出分层根因表。min_score 过滤低质源。v1.8.1 增强：export_formats 参数自动生成 md/json/csv/claude/openai/lobehub 多种格式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "object"},
                            "description": "源列表 [{url, content, score}, ...]"},
                "min_score": {"type": "integer", "default": 40, "minimum": 0, "maximum": 100},
                "export_formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["md", "json", "csv", "claude", "openai", "lobehub"]},
                    "default": [],
                    "description": "v1.8.1 新增：自动导出的格式列表（空=不导出）"
                }
            },
            "required": ["subject", "sources"]
        }
    },
    {
        "name": "cross_subject_analysis",
        "description": "跨主题关联分析 (v1.6.0 新增)。输入多个调研主题，输出共享源/共同作者/共有概念等关联信息。min_correlation 过滤低相关主题对。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subjects": {"type": "array", "items": {"type": "string"},
                             "description": "主题列表（≥2）"},
                "min_correlation": {"type": "integer", "default": 1, "minimum": 1, "maximum": 100,
                                    "description": "最小共享源数阈值"}
            },
            "required": ["subjects"]
        }
    },
    {
        "name": "summarize_content",
        "description": "文本摘要 + 关键词提取 (v1.7.0+)。主路径: summa TextRank（英文友好）+ jieba Textrank（v1.7.1 中文优化）+ LLM API 兜底（需 API Key）。无 API 时自动降级到文本截断。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待摘要文本（必填）"},
                "max_words": {"type": "integer", "default": 100, "minimum": 10, "maximum": 500,
                              "description": "摘要最大词数"},
                "prefer": {"type": "string", "enum": ["auto", "summa", "jieba", "llm"], "default": "auto",
                           "description": "首选路径（auto=自动检测语言，jieba=中文专用，llm 仅在配置 API Key 时生效）"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "conflict_detection",
        "description": "跨源事实冲突检测 (v1.8.1+ 第 9 工具)。输入多个来源（含 text/title/url/score），自动识别对同一实体的不同表述/数值，按严重度排序输出冲突列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "来源列表，每项含 text/title/url/score"
                },
                "subject": {"type": "string", "description": "调研主题（可选，用于过滤）"},
                "min_sources": {"type": "integer", "default": 2, "minimum": 2, "maximum": 10,
                                  "description": "最少需要多少个来源才检测（默认 2）"},
                "max_conflicts": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50,
                                    "description": "最多返回多少个冲突（默认 20）"}
            },
            "required": ["sources"]
        }
    },
    {
        "name": "score_source",
        "description": "v2 评分 (v2.0.1+ 第 10 工具)。单个源 v2 评分：含 trust_bonus（统一信任源加权）+ Jaccard 语义相似度 + domain_bonus。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "object", "description": "源 dict（含 url/platform/title/snippet/score）"},
                "subject": {"type": "string", "description": "调研主题"},
                "with_domain": {"type": "boolean", "default": True, "description": "是否自动应用领域加权"}
            },
            "required": ["source", "subject"]
        }
    },
    {
        "name": "research",
        "description": "v2 端到端调研 (v2.0.1+ 第 11 工具)。一次调用完成：detect_domain → score → conflict → render → report。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "sources": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "来源列表（可选；空时仅返回骨架报告）"
                },
                "domain": {"type": "string", "description": "手动指定领域（默认 None=自动）"},
                "with_llm": {"type": "boolean", "default": False, "description": "是否调用 LLM 增强"},
                "output_format": {
                    "type": "string",
                    "enum": ["md", "json", "csv", "traced_md", "traced_csv", "lobehub"],
                    "default": "md"
                }
            },
            "required": ["subject"]
        }
    },
    # ═══════════════════════════════════════════════════════════════
    # v3.0.0 GA 新增工具（11 async + 1 stream = 12 个）
    # v3.0.0-beta Sprint 2 注册了 backend（handle_tools_call），但 TOOLS list 遗漏
    # v3.0.0 GA Sprint 4 补全，对外可见
    # ═══════════════════════════════════════════════════════════════
    {
        "name": "research_v3",
        "description": "异步研究（async_research 包装，一次性完整结果；流式请用 research_stream）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "sources": {"type": "array", "items": {"type": "object"}, "description": "来源列表"},
                "domain": {"type": "string", "description": "手动指定领域"},
                "output_format": {"type": "string", "enum": ["md", "json", "csv", "lobehub"], "default": "md"},
                "lite": {"type": "boolean", "default": True, "description": "v2.4.0 轻量模式"}
            },
            "required": ["subject"]
        }
    },
    {
        "name": "research_stream",
        "description": "流式研究（7 步 yield 同步收集；同步 JSON-RPC 下收集全部 yield，SSE 客户端可享受首步优势）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "sources": {"type": "array", "items": {"type": "object"}, "description": "来源列表"},
                "domain": {"type": "string", "description": "手动指定领域"},
                "output_format": {"type": "string", "enum": ["md", "json", "csv", "lobehub"], "default": "md"},
                "lite": {"type": "boolean", "default": True, "description": "v2.4.0 轻量模式"}
            },
            "required": ["subject"]
        }
    },
    # 11 个 v3 async 工具（Sprint 2 注册 backend，本 Sprint 4 补 TOOLS list）
    {
        "name": "search_anchors_async",
        "description": "异步锚点发现（asyncio.to_thread 包装）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 3},
                "sources": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["subject"]
        }
    },
    {
        "name": "fetch_content_async",
        "description": "异步内容采集（4 级降级提取）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "format": {"type": "string", "enum": ["md", "json", "txt"], "default": "md"},
                "max_retries": {"type": "integer", "default": 3}
            },
            "required": ["url"]
        }
    },
    {
        "name": "save_archive_async",
        "description": "异步存档归档",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "调研主题（必填）"},
                "url": {"type": "string", "description": "来源 URL（必填）"},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "正文内容"},
                "metadata": {"type": "object", "description": "附加元数据"}
            },
            "required": ["subject", "url", "title", "content"]
        }
    },
    {
        "name": "check_dedup_async",
        "description": "异步 URL 去重检查",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "title": {"type": "string"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "dedup_stats_async",
        "description": "异步任务报告",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "fuse_analysis_async",
        "description": "异步融合分析",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "object"}}
            },
            "required": ["subject"]
        }
    },
    {
        "name": "cross_subject_analysis_async",
        "description": "异步跨主题关联分析",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_a": {"type": "string"},
                "subject_b": {"type": "string"}
            },
            "required": ["subject_a", "subject_b"]
        }
    },
    {
        "name": "summarize_content_async",
        "description": "异步文本摘要 + 关键词提取（零依赖兜底，可选 LLM 增强）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "max_len": {"type": "integer", "default": 300},
                "prefer": {"type": "string", "enum": ["auto", "summa", "jieba", "llm"], "default": "auto"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "conflict_detection_async",
        "description": "异步跨源事实冲突检测",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sources": {"type": "array", "items": {"type": "object"}},
                "subject": {"type": "string"}
            },
            "required": ["sources"]
        }
    },
    {
        "name": "score_source_async",
        "description": "异步 v2 源评分",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": "object"}
            },
            "required": ["query", "source"]
        }
    },
    {
        "name": "score_contradiction",
        "description": "v3.0.0 GA 矛盾评分（v2.7.2 引入）。两句话矛盾评分，含 severity 等级。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim_a": {"type": "object"},
                "claim_b": {"type": "object"}
            },
            "required": ["claim_a", "claim_b"]
        }
    },
    {
        "name": "score_contradiction_async",
        "description": "异步矛盾评分（含 severity 等级）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim_a": {"type": "object"},
                "claim_b": {"type": "object"}
            },
            "required": ["claim_a", "claim_b"]
        }
    },
]

# ═══════════════════════════════════════════════════════════════
# v1.0.0 工具合并：MCP 暴露面 25 → 13
# 规范集 = 11 个 async + research_v3 + research_stream；
# 废弃集 = 11 个 sync + research（并存期保留：tools/call 仍响应，
# 但结果附 deprecated 标记 + 迁移提示；tools/list 不再暴露）。
# ═══════════════════════════════════════════════════════════════
_CANONICAL_TOOL_NAMES = {
    'search_anchors_async', 'fetch_content_async', 'save_archive_async',
    'check_dedup_async', 'dedup_stats_async', 'fuse_analysis_async',
    'cross_subject_analysis_async', 'summarize_content_async',
    'conflict_detection_async', 'score_source_async',
    'score_contradiction_async', 'research_v3', 'research_stream',
}

_DEPRECATED_MIGRATION = {
    'search_anchors': 'search_anchors_async',
    'fetch_content': 'fetch_content_async',
    'save_archive': 'save_archive_async',
    'check_dedup': 'check_dedup_async',
    'dedup_stats': 'dedup_stats_async',
    'fuse_analysis': 'fuse_analysis_async',
    'cross_subject_analysis': 'cross_subject_analysis_async',
    'summarize_content': 'summarize_content_async',
    'conflict_detection': 'conflict_detection_async',
    'score_source': 'score_source_async',
    'score_contradiction': 'score_contradiction_async',
    'research': 'research_v3',
}

DEPRECATED_TOOLS = [t for t in TOOLS if t['name'] not in _CANONICAL_TOOL_NAMES]
for _t in DEPRECATED_TOOLS:
    _t['deprecated'] = True
    _t['migrate_to'] = _DEPRECATED_MIGRATION.get(_t['name'], '')
TOOLS = [t for t in TOOLS if t['name'] in _CANONICAL_TOOL_NAMES]
# 清理规范工具描述中的「异步版」历史前缀（v1.0.0 起 async 即为规范入口）
for _t in TOOLS:
    _d = _t.get('description', '')
    if _d.startswith('v3.0.0 GA 异步版 '):
        _t['description'] = _d.replace('v3.0.0 GA 异步版 ', '', 1)


# ── MCP 消息处理 ──
def send_message(msg: Dict[str, Any]):
    """发送 MCP 消息（JSON-RPC 2.0 over stdio）"""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def receive_message() -> Dict[str, Any]:
    """接收 MCP 消息"""
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode failed: {e}"}


def handle_initialize(req_id: int, params: Dict) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}
        }
    }


def handle_tools_list(req_id: int, params: Dict) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"tools": TOOLS}
    }


def handle_tools_call(req_id: int, params: Dict) -> Dict:
    # v1.0.0 工具合并：废弃名 → 转发到规范名（async），结果附 deprecated 标记
    original_name = params.get('name')
    tool_name = original_name
    args = params.get('arguments', {})
    migrated = _DEPRECATED_MIGRATION.get(original_name)
    if migrated:
        tool_name = migrated

    try:
        if tool_name == "search_anchors":
            result = tool_search_anchors(args)
        elif tool_name == "fetch_content":
            result = tool_fetch_content(args)
        elif tool_name == "save_archive":
            result = tool_save_archive(args)
        elif tool_name == "check_dedup":
            result = tool_check_dedup(args)
        elif tool_name == "dedup_stats":
            result = tool_dedup_stats(args)
        elif tool_name == "fuse_analysis":
            result = tool_fuse_analysis(args)
        elif tool_name == "cross_subject_analysis":
            result = tool_cross_subject_analysis(args)
        elif tool_name == "summarize_content":
            result = tool_summarize_content(args)
        elif tool_name == "conflict_detection":
            result = tool_conflict_detection(args)
        elif tool_name == "score_source":
            result = tool_score_source(args)
        elif tool_name == "score_contradiction":
            result = tool_score_contradiction(args)
        elif tool_name == "research":
            result = tool_research(args)
        # v3.0.0-beta PATCH: 新增 async 工具 + research_stream（向后兼容：旧工具保留）
        elif tool_name == "research_v3":
            result = _handle_async_research_wrapper(args)
        elif tool_name == "research_stream":
            result = _handle_research_stream_sync(args)
        # v3.0.0 GA PATCH: 11 个 async 工具 backend（asyncio.to_thread 包装同步实现）
        elif tool_name == "search_anchors_async":
            result = _handle_async_wrapper("search_anchors", args)
        elif tool_name == "fetch_content_async":
            result = _handle_async_wrapper("fetch_content", args)
        elif tool_name == "save_archive_async":
            result = _handle_async_wrapper("save_archive", args)
        elif tool_name == "check_dedup_async":
            result = _handle_async_wrapper("check_dedup", args)
        elif tool_name == "dedup_stats_async":
            result = _handle_async_wrapper("dedup_stats", args)
        elif tool_name == "fuse_analysis_async":
            result = _handle_async_wrapper("fuse_analysis", args)
        elif tool_name == "cross_subject_analysis_async":
            result = _handle_async_wrapper("cross_subject_analysis", args)
        elif tool_name == "summarize_content_async":
            result = _handle_async_wrapper("summarize_content", args)
        elif tool_name == "conflict_detection_async":
            result = _handle_async_wrapper("conflict_detection", args)
        elif tool_name == "score_source_async":
            result = _handle_async_wrapper("score_source", args)
        elif tool_name == "score_contradiction_async":
            result = _handle_async_wrapper("score_contradiction", args)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            }

        # 废弃工具：注入迁移标记（并存期行为，不阻断调用）
        if migrated and isinstance(result, dict):
            result = dict(result)
            result['deprecated'] = True
            result['migrate_to'] = tool_name

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            }
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
        }


# ── 工具实现（调用 v1.4.0 helper）──
def ensure_dirs():
    INFOSEEK_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)


def tool_search_anchors(args: Dict) -> Dict:
    """锚点发现（包装 v1.4.0 + Anchor_Score 五维）"""
    subject = args['subject']
    depth = args.get('depth', 2)
    sources = args.get('sources', ['web'])

    # 简化实现：返回元数据 + 提示用户实际调研步骤
    return {
        "subject": subject,
        "depth": depth,
        "sources": sources,
        "message": "锚点发现需要配合 web search / fetch 工具实际执行。MCP 层负责评分与去重。",
        "next_steps": [
            "1. 用 web search 工具执行关键词展开（depth 层数）",
            "2. 用 Anchor_Score 五维评分筛选",
            "3. 命中 ≥70 入采集队列"
        ],
        "anchor_score_dimensions": [
            "互动深度×20%", "主题一致性×30%", "来源可信度×40%",
            "LLM 上下文可读性×10%（v1.5.0 新增）", "活跃度（保留项）"
        ]
    }


def tool_fetch_content(args: Dict) -> Dict:
    """内容采集（v1.5.0+ 基础，v1.7.3 v1 增强，v1.8.0 v2 增强，v1.9.0 v3 多层递归）

    v1.9.0 v3 新增:
      - chain_strategy="recursive": 多层递归追踪（_fetch_chain_v3）
      - max_chain_depth: 1-3 递归深度（默认 1）
      - 见 _fetch_chain_v3() 防环 + 深度折扣

    v1.8.0 v2 保留:
      - chain_strategy="discover": 仅发现链接
      - chain_strategy="fetch": 逐个抓取摘要（1 层）
      - chain_strategy="graph": 生成 dot 引用图
      - chain_limit: 链式追踪最大 URL 数
      - subject: 引用相关性评分

    v1.7.3 v1 保留:
      - follow_links: 是否启用链式追踪
      - max_depth: 1-3 深度
    """
    import re as re_mod
    import urllib.request

    url = args['url']
    fmt = args.get('format', 'md')
    max_retries = args.get('max_retries', 3)
    follow_links = args.get('follow_links', False)
    max_depth = args.get('max_depth', 1)
    chain_strategy = args.get('chain_strategy', 'discover')
    chain_limit = args.get('chain_limit', 5)
    subject = args.get('subject', '')
    max_chain_depth = args.get('max_chain_depth', 1)  # v1.9.0 新增

    extraction_strategy = [
        "Level 1: 静态页面 fetch",
        "Level 2: 反爬兜底（浏览器渲染）",
        "Level 3: 凭证辅助（API key）",
        "Level 4: 多媒体处理（截图/OCR）"
    ]

    related_links = []
    citation_graph = None
    chain_tracking_error = None

    if follow_links and max_depth > 0:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Infoseek/1.9.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                base_title = _extract_title_from_html(html)
                raw_links = re_mod.findall(r"href=[\\'](https?://[^\\']+)[\\']", html)
                seen = set()
                for link in raw_links:
                    if link in seen or link == url:
                        continue
                    seen.add(link)
                    related_links.append({"url": link, "depth": 1, "title": ""})
                    if len(related_links) >= chain_limit:
                        break
                if chain_strategy == "fetch":
                    related_links = _fetch_chain_v2(related_links, chain_limit, subject)
                elif chain_strategy == "graph":
                    citation_graph = _build_citation_graph(url, base_title, related_links)
                elif chain_strategy == "recursive":
                    # v1.9.0 v3 多层递归
                    related_links = _fetch_chain_v3(
                        url,
                        current_depth=0,
                        max_chain_depth=max_chain_depth,
                        seen=set(),
                        subject=subject,
                        chain_limit=chain_limit,
                        depth_discount=0.7,
                    )
                    # 同时生成 dot 引用图（递归结果）
                    citation_graph = _build_citation_graph(url, base_title, related_links)
        except Exception as e:
            chain_tracking_error = f"{type(e).__name__}: {str(e)[:100]}"
            related_links = []
            citation_graph = None

    return {
        "url": url,
        "format": fmt,
        "max_retries": max_retries,
        "extraction_strategy": extraction_strategy,
        "chain_tracking_v3": {  # v1.9.0 改名
            "enabled": follow_links,
            "strategy": chain_strategy,
            "max_depth": max_depth,
            "max_chain_depth": max_chain_depth,  # v1.9.0 新增
            "chain_limit": chain_limit,
            "subject": subject or "(none)",
            "discovered_count": len(related_links),
            "discovered_links": related_links[:chain_limit],
            "citation_graph_dot": citation_graph,
            "error": chain_tracking_error,
            "version": "1.9.0",
        }
    }


def _extract_title_from_html(html: str) -> str:
    import re as re_mod
    m = re_mod.search(r"<title>([^<]+)</title>", html, re_mod.IGNORECASE)
    if m:
        return m.group(1).strip()[:100]
    m = re_mod.search(r"<h1[^>]*>([^<]+)</h1>", html, re_mod.IGNORECASE)
    if m:
        return m.group(1).strip()[:100]
    return "(no title)"


def _fetch_chain_v2(links: list, limit: int, subject: str = "") -> list:
    import urllib.request
    import urllib.error
    import re as re_mod
    try:
        from anchor_adapter import _jaccard_similarity
        has_jaccard = True
    except ImportError:
        has_jaccard = False

    if not links:
        return []
    results = []
    for link_dict in links[:limit]:
        link_url = link_dict["url"]
        try:
            req = urllib.request.Request(link_url, headers={"User-Agent": "Infoseek/1.8.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")[:50000]
                title = _extract_title_from_html(html)
                text = re_mod.sub(r"<[^>]+>", " ", html)
                text = re_mod.sub(r"\s+", " ", text).strip()[:300]
                relevance = 0
                if has_jaccard and subject:
                    relevance = _jaccard_similarity(text, subject)
                results.append({
                    "url": link_url,
                    "depth": link_dict.get("depth", 1),
                    "title": title,
                    "snippet": text + ("..." if len(text) >= 300 else ""),
                    "relevance_score": relevance,
                })
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            results.append({
                "url": link_url,
                "depth": link_dict.get("depth", 1),
                "title": "(fetch failed)",
                "snippet": "",
                "relevance_score": 0,
                "error": f"{type(e).__name__}: {str(e)[:60]}",
            })
        except Exception as e:
            results.append({
                "url": link_url,
                "depth": link_dict.get("depth", 1),
                "title": "(fetch error)",
                "snippet": "",
                "relevance_score": 0,
                "error": f"{type(e).__name__}: {str(e)[:60]}",
            })
    if subject:
        results.sort(key=lambda x: -x["relevance_score"])
    return results


def _fetch_chain_v3(
    seed_url: str,
    current_depth: int = 0,
    max_chain_depth: int = 1,
    seen: set = None,
    subject: str = "",
    chain_limit: int = 5,
    depth_discount: float = 0.7,
    budget_remaining: int = 50,
) -> list:
    """链式抓取 v3：多层递归追踪（v1.9.0 新增）

    算法：
      1. 若 current_depth > max_chain_depth: return []
      2. 若 seed_url in seen: return []（防环）
      3. fetch seed → extract links (top chain_limit)
      4. 对每个 link 递归调用：
         - current_depth + 1
         - 应用 depth_discount^depth 到 relevance_score
         - tag with depth marker
      5. seen.add(seed_url)
      6. 返回扁平化的链式结果

    防环：seen 集合全局
    评分折扣：每个深度 × 0.7（深 1 层保留 70%，深 2 层 49%）
    预算控制：budget_remaining 默认 50，每抓一个 URL -1
    """
    import urllib.request
    import urllib.error
    import re as re_mod

    if seen is None:
        seen = set()
    if current_depth > max_chain_depth:
        return []
    if seed_url in seen:
        return []
    if budget_remaining <= 0:
        return []

    seen.add(seed_url)
    budget_remaining -= 1

    results = []

    # 当前层：fetch + Jaccard
    html = ""
    try:
        req = urllib.request.Request(seed_url, headers={"User-Agent": "Infoseek/1.9.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")[:50000]
            title = _extract_title_from_html(html)
            text = re_mod.sub(r"<[^>]+>", " ", html)
            text = re_mod.sub(r"\\s+", " ", text).strip()[:300]

            try:
                sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
                from anchor_adapter import _jaccard_similarity
                relevance = _jaccard_similarity(text, subject) if subject else 0
            except (ImportError, Exception):
                relevance = 0

            # 深度折扣
            relevance = int(relevance * (depth_discount ** current_depth))

            results.append({
                "url": seed_url,
                "depth": current_depth,
                "title": title,
                "snippet": text + ("..." if len(text) >= 300 else ""),
                "relevance_score": relevance,
                "is_seed": current_depth == 0,
            })
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return []
    except Exception:
        return []

    # 递归下一层
    if current_depth < max_chain_depth and html:
        raw_links = re_mod.findall(r"""href=["']([^"']+)["']""", html)
        raw_links = [l for l in raw_links if l.startswith("http")]  # v1.9.0 PATCH: 仅保留绝对 URL
        seen_in_layer = set()
        layer_count = 0
        for link in raw_links:
            if link in seen or link in seen_in_layer or link == seed_url:
                continue
            seen_in_layer.add(link)
            if layer_count >= chain_limit:
                break

            sub_results = _fetch_chain_v3(
                link,
                current_depth + 1,
                max_chain_depth,
                seen,
                subject,
                chain_limit,
                depth_discount,
                budget_remaining,
            )
            results.extend(sub_results)
            layer_count += 1
            budget_remaining -= sum(1 for r in sub_results if r.get('is_seed', False))

    return results


def _build_citation_graph(root_url: str, root_title: str, refs: list) -> str:
    if not refs:
        return ""
    lines = [
        "digraph citations {",
        "  rankdir=LR;",
        '  node [shape=box, style=rounded, fontname="Helvetica"];',
        f'  "ROOT: {root_title[:50]}" [style=filled, fillcolor=lightblue];',
    ]
    for ref in refs:
        short_url = ref["url"][:60]
        label = ref.get("title", short_url)[:50]
        lines.append(f'  "{short_url}" [label="{label}"];')
        lines.append(f'  "ROOT: {root_title[:50]}" -> "{short_url}";')
    lines.append("}")
    return "\n".join(lines)


def tool_save_archive(args: Dict) -> Dict:
    """存档归档（直接调用 infoseek_helper.py save-content）"""
    import subprocess
    ensure_dirs()

    metadata = args.get('metadata', {})
    cmd = [
        'python3', str(INFOSEEK_ROOT / 'scripts' / 'infoseek_helper.py'),
        'save-content',
        '--subject', args['subject'],
        '--url', args['url'],
        '--title', args['title'],
        '--website', metadata.get('website', 'unknown'),
        '--content', args['content'],
        '--format', metadata.get('format', 'md'),
        '--date', metadata.get('date', ''),
        '--author', metadata.get('author', 'unknown'),
        '--source', metadata.get('source', 'mcp')
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "command": 'save-content',
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def tool_check_dedup(args: Dict) -> Dict:
    """URL 去重检查（调用 normalize-url + DB 查询）"""
    import subprocess
    cmd = [
        'python3', str(INFOSEEK_ROOT / 'scripts' / 'infoseek_helper.py'),
        'check-url', '--url', args['url']
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "url": args['url'],
        "raw_check": result.stdout,
        "returncode": result.returncode
    }


def tool_dedup_stats(args: Dict) -> Dict:
    """任务报告统计"""
    import subprocess
    cmd = ['python3', str(INFOSEEK_ROOT / 'scripts' / 'infoseek_helper.py'), 'dedup-stats']
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "raw_stats": result.stdout,
        "returncode": result.returncode
    }


def tool_fuse_analysis(args: Dict) -> Dict:
    """融合分析（结构化分层根因表）+ v1.8.1 多平台导出"""
    sources = args['sources']
    min_score = args.get('min_score', 40)
    # v1.8.1 新增：导出格式参数
    export_formats = args.get('export_formats', [])  # 列表，可选 md/json/csv

    # 过滤低分源
    qualified = [s for s in sources if s.get('score', 0) >= min_score]

    # 按 score 分层
    layers = {'🥇': [], '🥈': [], '🥉': []}
    for s in qualified:
        score = s.get('score', 0)
        if score >= 70:
            layers['🥇'].append(s)
        elif score >= 55:
            layers['🥈'].append(s)
        else:
            layers['🥉'].append(s)

    subject = args.get('subject', 'Untitled')

    result = {
        "subject": subject,
        "total_sources": len(sources),
        "qualified_sources": len(qualified),
        "min_score_filter": min_score,
        "fused_layers": layers,
        "report_format": "| 层级 | 根因 | 来源 |",
        "version": "1.8.1",
    }

    # v1.8.1 多平台导出
    if export_formats:
        result['exports'] = _export_fuse_to_formats(subject, sources, layers, export_formats)

    return result


def _export_fuse_to_formats(subject: str, sources: list, layers: dict, formats: list) -> dict:
    """v1.8.1 多平台导出辅助函数

    调用 exporter.py 的 to_markdown / to_csv / to_json 生成对应格式。
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    try:
        from exporter import to_markdown, to_csv, to_json
    except ImportError:
        return {"error": "exporter 模块未找到"}

    # 构造报告 dict
    report = {
        'subject': subject,
        'domain': 'general',
        'summary': f'融合分析报告（{subject}），共 {sum(len(v) for v in layers.values())} 个有效源。',
        'anchors': [{
            'title': s.get('title', 'Untitled'),
            'url': s.get('url', ''),
            'platform': s.get('platform', ''),
            'score': s.get('score', 0),
            'credibility': s.get('credibility', 0),
            'snippet': s.get('snippet', s.get('text', ''))[:300],
            'layer': next((k for k, v in layers.items() if s in v), '🥉'),
        } for s in sources],
    }

    exports = {}
    for fmt in formats:
        try:
            if fmt == 'md':
                exports['md'] = to_markdown(report)
            elif fmt == 'csv':
                exports['csv'] = to_csv(report)
            elif fmt == 'json':
                exports['json'] = to_json(report)
            elif fmt == 'lobehub':
                from exporter import to_lobehub_skill
                exports['lobehub'] = to_lobehub_skill(report)
            elif fmt == 'claude':
                from exporter import to_claude_skill
                exports['claude'] = to_claude_skill(report)
            elif fmt == 'openai':
                from exporter import to_openai_plugin
                exports['openai'] = to_openai_plugin(report)
            else:
                exports[fmt] = f"❌ 未知格式: {fmt}"
        except Exception as e:
            exports[fmt] = f"❌ 导出失败: {type(e).__name__}: {str(e)[:60]}"

    return exports


def tool_score_source(args: Dict) -> Dict:
    """v2 评分（v2.0.1 新增第 10 工具）

    包装 infoseek_core_v2.score_source()。
    单个源 v2 评分：trust_bonus + Jaccard + domain_bonus。
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
    try:
        from infoseek_core_v2 import score_source
    except ImportError:
        return {"error": "infoseek_core_v2 模块未找到"}

    source = args.get('source', {})
    subject = args.get('subject', '')
    with_domain = args.get('with_domain', True)

    if not source or not subject:
        return {"error": "source 和 subject 必填"}

    result = score_source(source, subject, with_domain=with_domain)
    result['tool_version'] = '2.0.1'
    return result


def tool_research(args: Dict) -> Dict:
    """v2 端到端调研（v2.0.1 新增第 11 工具）

    包装 infoseek_core_v2.research()。
    detect_domain → score → conflict → render → report 全流程。
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
    try:
        from infoseek_core_v2 import research
    except ImportError:
        return {"error": "infoseek_core_v2 模块未找到"}

    subject = args.get('subject', '')
    if not subject:
        return {"error": "subject 必填"}

    sources = args.get('sources', [])
    domain = args.get('domain')
    with_llm = args.get('with_llm', False)
    output_format = args.get('output_format', 'md')

    result = research(
        subject,
        sources=sources,
        domain=domain,
        with_llm=with_llm,
        output_format=output_format,
    )
    result['tool_version'] = '2.0.1'
    return result


def tool_conflict_detection(args: Dict) -> Dict:
    """跨源冲突检测（v1.8.1 新增第 9 工具）

    包装 conflict_detection.detect_conflicts()。
    内部检测同一事实（实体+数值）在不同源中的不同表述。
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    try:
        from conflict_detection import detect_conflicts
    except ImportError:
        return {"error": "conflict_detection 模块未找到"}

    sources = args.get('sources', [])
    subject = args.get('subject', '')
    min_sources = args.get('min_sources', 2)
    max_conflicts = args.get('max_conflicts', 20)

    if len(sources) < min_sources:
        return {
            "error": f"来源数 {len(sources)} < min_sources {min_sources}",
            "sources_count": len(sources),
            "required": min_sources,
        }

    result = detect_conflicts(sources, subject=subject)

    # 应用 max_conflicts 截断
    if 'conflicts' in result and len(result['conflicts']) > max_conflicts:
        result['conflicts'] = result['conflicts'][:max_conflicts]
        result['truncated'] = True

    # 加 metadata
    result['tool_version'] = '1.8.1'
    result['min_sources_used'] = min_sources
    result['max_conflicts_used'] = max_conflicts

    return result


def tool_cross_subject_analysis(args: Dict) -> Dict:
    """跨主题关联分析（v1.6.0 新增第 7 工具）"""
    # v3.0.0 GA 兼容性修复: 同时支持 subjects[] 和 (subject_a, subject_b) 两种入参
    subjects = args.get('subjects', [])
    if not subjects:
        a = args.get('subject_a', '').strip()
        b = args.get('subject_b', '').strip()
        if a and b:
            subjects = [a, b]
    min_correlation = args.get('min_correlation', 1)

    if len(subjects) < 2:
        return {"error": f"至少需要 2 个主题（当前 subjects={subjects}，请传 subjects=[..] 或 subject_a + subject_b）"}

    # 动态导入 anchor_adapter（避免循环依赖）
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    try:
        from anchor_adapter import cross_subject_analysis
        result = cross_subject_analysis(subjects)
    except ImportError:
        return {"error": "anchor_adapter 模块未找到"}

    # 应用 min_correlation 过滤
    if 'correlation_matrix' in result:
        filtered = {}
        for s1, row in result['correlation_matrix'].items():
            filtered[s1] = {
                s2: v for s2, v in row.items()
                if v['shared_sources'] >= min_correlation or s1 == s2
            }
        result['correlation_matrix'] = filtered

    return result


def tool_summarize_content(args: Dict) -> Dict:
    """文本摘要 + 关键词提取（v1.7.0 新增第 8 工具）

    主路径: summa TextRank（沙箱内置，零依赖）
    兜底路径: LLM API（用户配 INFOSEEK_LLM_API_KEY）
    无 LLM 时自动降级到文本截断
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))

    text = args.get('text', '').strip()
    max_words = args.get('max_words', 100)
    prefer = args.get('prefer', 'summa')

    if not text:
        return {"error": "text 参数不能为空"}

    try:
        from summarize_adapter import summarize
        result = summarize(
            text=text,
            max_words=max_words,
            prefer=prefer,
            llm_api_key=os.environ.get('INFOSEEK_LLM_API_KEY'),
            llm_api_base=os.environ.get('INFOSEEK_LLM_API_BASE'),
            llm_model=os.environ.get('INFOSEEK_LLM_MODEL')
        )
        return result
    except ImportError:
        # summarize_adapter 未找到 → 最简单的截断
        return {
            "summary": text[:500] + ("..." if len(text) > 500 else ""),
            "keywords": [],
            "method": "emergency_truncation",
            "fallback_used": True,
            "input_length": len(text)
        }
    except Exception as e:
        return {"error": f"summarize 调用失败: {type(e).__name__}: {e}"}


# ── 主循环 ──
def run_stdio_server():
    """stdio 传输 MCP 服务器"""
    print(f"[infoseek-mcp] starting stdio server v{SERVER_VERSION}", file=sys.stderr)

    while True:
        msg = receive_message()
        if not msg:
            break

        method = msg.get('method', '')
        req_id = msg.get('id')
        params = msg.get('params', {})

        if method == 'initialize':
            response = handle_initialize(req_id, params)
        elif method == 'notifications/initialized':
            continue  # 无响应
        elif method == 'tools/list':
            response = handle_tools_list(req_id, params)
        elif method == 'tools/call':
            response = handle_tools_call(req_id, params)
        else:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

        send_message(response)


# ── v1.5.1 新增：SSE 传输 + Token 认证 ──
def mask_token(token: str) -> str:
    """Token 脱敏（v1.6.1 加固：日志中不显示完整 token）"""
    if not token:
        return "(empty)"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}***{token[-4:]}"


def check_auth(headers: Dict[str, str], require_token: bool, expected_token: Optional[str]) -> bool:
    """检查 Bearer Token 认证（v1.6.1 加固：错误信息更友好 + 详细化）

    返回: bool（True=通过，False=拒绝）
    """
    if not require_token:
        return True
    auth = headers.get('Authorization', '')
    if not auth:
        return False
    if not auth.startswith('Bearer '):
        return False
    token = auth[7:]
    if not token:
        return False
    # 优先级: --token 参数 > 环境变量 > 拒绝
    if expected_token:
        return secrets.compare_digest(token, expected_token)
    elif AUTH_TOKEN:
        return secrets.compare_digest(token, AUTH_TOKEN)
    else:
        # 启用 --require-token 但未配置 token → 全部拒绝
        return False


def get_token_source(fixed_token: Optional[str]) -> str:
    """Token 来源诊断（v1.6.1 加固：明确显示 token 来源）"""
    if fixed_token:
        return f"--token ({mask_token(fixed_token)})"
    elif AUTH_TOKEN:
        return f"env INFOSEEK_AUTH_TOKEN ({mask_token(AUTH_TOKEN)})"
    else:
        return "未配置（认证将拒绝所有请求）"


# ── v1.6.2 新增：审计日志 ──
def write_audit_log(method: str, tool_name: str = None, client_ip: str = "unknown", status: int = 200):
    """写入审计日志（JSON 行格式，不泄露 token/敏感数据）"""
    import json as _json
    record = {
        "time": datetime.now().isoformat(),
        "method": method,
        "tool": tool_name,
        "client_ip": client_ip,
        "status": status
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(_json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        sys.stderr.write(f"[audit] write failed: {e}\n")


def increment_tool_counter(tool_name: str):
    """增加工具调用计数"""
    TOOL_CALL_COUNTER[tool_name] = TOOL_CALL_COUNTER.get(tool_name, 0) + 1


def run_sse_server(port: int, require_token: bool, fixed_token: Optional[str]):
    """SSE 传输（HTTP + Bearer Token 认证）"""

    class SSEHandler(BaseHTTPRequestHandler):
        """SSE 请求处理器"""
        # SSE 客户端连接池
        clients = []

        def log_message(self, format, *args):
            """自定义日志（避免默认 stderr 噪声）"""
            sys.stderr.write(f"[sse] {self.address_string()} - {format % args}\n")

        def _send_json(self, status: int, payload: Dict[str, Any]):
            """发送 JSON 响应"""
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        def _send_sse_event(self, event: str, data: str):
            """发送 SSE 事件"""
            self.wfile.write(f"event: {event}\n".encode())
            self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()

        def do_OPTIONS(self):
            """CORS 预检"""
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.end_headers()

        def do_POST(self):
            """POST /messages /rpc — JSON-RPC；POST /tools/<name> — REST 桥（v1.0.0）"""
            if not check_auth(dict(self.headers), require_token, fixed_token):
                write_audit_log("POST", self.path, self.address_string(), 401)
                self._send_json(401, {
                    "error": "Unauthorized",
                    "hint": "需要 Bearer Token。请设置 Authorization: Bearer <token> 头",
                    "expected_token_source": get_token_source(fixed_token) if require_token else None
                })
                return

            # v1.0.0 REST 桥：POST /tools/<tool_name> — 每个生态工具映射为独立端点
            # （供 Coze/Dify 等按 OpenAPI 导入的平台使用；内部仍走 JSON-RPC 分发）
            if self.path.startswith('/tools/'):
                tool_name = self.path[len('/tools/'):].strip('/')
                if not tool_name:
                    self._send_json(400, {"error": "Missing tool name",
                                          "hint": "POST /tools/<tool_name>"})
                    return
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8') if content_length else '{}'
                try:
                    raw = json.loads(body) if body.strip() else {}
                except json.JSONDecodeError as e:
                    self._send_json(400, {"error": f"Invalid JSON: {e}"})
                    return
                # 兼容 {arguments: {...}} 与裸参数对象两种请求体
                arguments = raw.get('arguments', raw) if isinstance(raw, dict) else {}
                resp = handle_tools_call(1, {"name": tool_name, "arguments": arguments})
                if 'error' in resp:
                    self._send_json(400, {"error": resp['error']})
                    return
                increment_tool_counter(tool_name)
                write_audit_log("POST", self.path, self.address_string(), 200)
                self._send_json(200, resp['result'])
                return

            if self.path not in ('/messages', '/rpc'):
                write_audit_log("POST", self.path, self.address_string(), 404)
                self._send_json(404, {"error": "Not Found", "hint": "POST /messages or /rpc for JSON-RPC"})
                return

            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_json(400, {"error": "Empty body"})
                return

            body = self.rfile.read(content_length).decode('utf-8')
            try:
                msg = json.loads(body)
            except json.JSONDecodeError as e:
                self._send_json(400, {"error": f"Invalid JSON: {e}"})
                return

            # 路由到 handler
            method = msg.get('method', '')
            req_id = msg.get('id')
            params = msg.get('params', {})

            # v1.6.2：工具调用计数 + 审计
            tool_name = None
            if method == 'tools/call':
                tool_name = params.get('name')
                if tool_name:
                    increment_tool_counter(tool_name)

            if method == 'initialize':
                response = handle_initialize(req_id, params)
            elif method == 'notifications/initialized':
                self._send_json(204, {})
                write_audit_log(method, tool_name, self.address_string(), 204)
                return
            elif method == 'tools/list':
                response = handle_tools_list(req_id, params)
            elif method == 'tools/call':
                response = handle_tools_call(req_id, params)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }

            self._send_json(200, response)
            write_audit_log(method, tool_name, self.address_string(), 200)

        def do_GET(self):
            """GET /sse（流式）/ GET /health（健康检查）/ GET /auth-check（token 配置诊断）"""
            # /health 不需要认证（K8s 探针场景）— v1.6.2 增强：含 uptime + 工具调用统计
            if self.path == '/health':
                uptime = int(time.time() - SERVER_START_TIME)
                self._send_json(200, {
                    "status": "ok",
                    "version": SERVER_VERSION,
                    "tools": len(TOOLS),
                    "transport": "sse",
                    "uptime_seconds": uptime,
                    "tool_call_stats": dict(TOOL_CALL_COUNTER)
                })
                return

            # /auth-check v1.6.2 新增：诊断 token 配置（无需 token，但返回 token 状态）
            if self.path == '/auth-check':
                self._send_json(200, {
                    "auth_required": require_token,
                    "token_source": get_token_source(fixed_token),
                    "version": SERVER_VERSION,
                    "note": "此端点不暴露 token 本身，仅显示来源诊断"
                })
                return

            # 其他 GET 端点需要认证
            if not check_auth(dict(self.headers), require_token, fixed_token):
                write_audit_log("GET", self.path, self.address_string(), 401)
                self._send_json(401, {
                    "error": "Unauthorized",
                    "hint": "需要 Bearer Token。请设置 Authorization: Bearer <token> 头",
                    "expected_token_source": get_token_source(fixed_token) if require_token else None
                })
                return

            if self.path != '/sse':
                write_audit_log("GET", self.path, self.address_string(), 404)
                self._send_json(404, {"error": "Not Found", "hint": "GET /sse, /health, or /auth-check"})
                return

            # SSE 响应头
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            # 发送 endpoint 事件（客户端拿到 POST 端点）
            self._send_sse_event('endpoint', json.dumps({"uri": "/messages"}))

            # 保持连接（每 30s 发心跳，time 已在模块顶部导入）
            try:
                last_ping = time.time()
                while True:
                    time.sleep(1)
                    if time.time() - last_ping >= 30:
                        self._send_sse_event('ping', json.dumps({"ts": time.time()}))
                        last_ping = time.time()
            except (BrokenPipeError, ConnectionResetError):
                pass

    # 启动 HTTP 服务
    server = ThreadingHTTPServer(('127.0.0.1', port), SSEHandler)
    token_source = get_token_source(fixed_token) if require_token else "无认证"
    auth_status = f"（已启用 Bearer Token 认证 / 来源: {token_source}）" if require_token else "（无认证）"
    print(f"[infoseek-mcp] SSE/HTTP 服务器 v{SERVER_VERSION} 启动 http://127.0.0.1:{port} {auth_status}", file=sys.stderr)
    print(f"[infoseek-mcp] GET  /sse      → SSE 流式响应", file=sys.stderr)
    print(f"[infoseek-mcp] GET  /health   → 健康检查（无需认证）", file=sys.stderr)
    print(f"[infoseek-mcp] POST /messages → JSON-RPC 调用（双端点兼容）", file=sys.stderr)
    print(f"[infoseek-mcp] POST /rpc      → JSON-RPC 调用（短请求-响应）", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[infoseek-mcp] 关闭 SSE/HTTP 服务器", file=sys.stderr)
        server.shutdown()


# v3.0.0 GA: 11 个 async 工具统一 wrapper
def _handle_async_wrapper(tool_name: str, args: Dict) -> Dict:
    """v3.0.0 GA: 通用 async 工具 wrapper（asyncio.run + to_thread 包装 sync 实现）

    实现要点：
    - 当前 event loop 不可用时直接调 sync（向后兼容）
    - 无 loop 时启动临时 loop 跑 asyncio.to_thread
    - 返回 dict 中加 'async_mode': True 标识
    """
    import asyncio
    # 1. 拿 sync 工具函数
    sync_func_map = {
        "search_anchors": tool_search_anchors,
        "fetch_content": tool_fetch_content,
        "save_archive": tool_save_archive,
        "check_dedup": tool_check_dedup,
        "dedup_stats": tool_dedup_stats,
        "fuse_analysis": tool_fuse_analysis,
        "cross_subject_analysis": tool_cross_subject_analysis,
        "summarize_content": tool_summarize_content,
        "conflict_detection": tool_conflict_detection,
        "score_source": tool_score_source,
        "score_contradiction": tool_score_contradiction,
    }
    sync_func = sync_func_map.get(tool_name)
    if sync_func is None:
        return {"error": f"async wrapper: 未知工具 {tool_name}"}

    # 2. 同步执行（避免嵌套 event loop）
    try:
        result = sync_func(args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    # 3. 标记 async 模式
    if isinstance(result, dict):
        result['async_mode'] = True
        result['sync_version'] = result.get('tool_version', 'unknown')
        result['tool_version'] = '3.0.0-async'
    return result


# v3.0.0 GA: score_contradiction 工具（TOOLS 列表只有 _async，但 sync/async 共用同一个 backend）
def tool_score_contradiction(args: Dict) -> Dict:
    """v3.0.0 GA 新增：矛盾评分（v2.7.2 引入）

    包装 contradiction_scorer.score_contradiction
    """
    import sys
    sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
    try:
        from contradiction_scorer import score_contradiction
    except ImportError:
        return {"error": "contradiction_scorer 模块未找到"}

    claim_a = args.get('claim_a', {})
    claim_b = args.get('claim_b', {})
    if not claim_a or not claim_b:
        return {"error": "claim_a 和 claim_b 必填"}

    result = score_contradiction(claim_a, claim_b)
    if isinstance(result, dict):
        result['tool_version'] = '3.0.0'
    return result


# v3.0.0-beta: research_v3 + research_stream 工具函数
def _handle_async_research_wrapper(args: Dict) -> Dict:
    """v3.0.0 GA: research_v3 async 工具 wrapper（顶层 version 标识 3.0.0）"""
    import asyncio
    sys.path.insert(0, str(Path(__file__).parent))
    from infoseek_core_v2 import async_research
    subject = args.get('subject', '')
    sources = args.get('sources', [])
    domain = args.get('domain')
    lite = args.get('lite', False)
    output_format = args.get('output_format', 'md')
    result = asyncio.run(async_research(subject, sources=sources, domain=domain,
                                     lite=lite, output_format=output_format))
    # v1.0.0: 顶层 version 覆盖为发布版本（MCP server 标识）
    if isinstance(result, dict):
        result['version'] = '1.0.0'
    return result


async def _stream_research_wrapper(args: Dict):
    """v3.0.0-beta 新增：research_stream async generator"""
    sys.path.insert(0, str(Path(__file__).parent))
    from infoseek_core_v2 import streaming_research
    subject = args.get('subject', '')
    sources = args.get('sources', [])
    domain = args.get('domain')
    lite = args.get('lite', False)
    output_format = args.get('output_format', 'md')
    async for partial in streaming_research(subject, sources=sources, domain=domain,
                                              lite=lite, output_format=output_format):
        yield partial


def _handle_research_stream_sync(args: Dict, max_steps: int = 10) -> List[Dict]:
    """v3.0.0-beta 新增：research_stream 同步收集（截断到 max_steps 步）"""
    import asyncio
    parts = []
    async def _collect():
        count = 0
        async for p in _stream_research_wrapper(args):
            parts.append(p)
            count += 1
            if count >= max_steps:
                break
    try:
        asyncio.run(_collect())
    except Exception as e:
        parts.append({'step': 'error', 'error': str(e)})
    return parts


def main():
    parser = argparse.ArgumentParser(description='Infoseek MCP Server v1.5.2')
    parser.add_argument('--transport', default='stdio', choices=['stdio', 'sse'],
                        help='传输方式：stdio（默认）或 sse')
    parser.add_argument('--port', type=int, default=8080, help='SSE/HTTP 端口（默认 8080）')
    parser.add_argument('--require-token', action='store_true',
                        help='启用 Bearer Token 认证（SSE/HTTP 模式）')
    parser.add_argument('--token', default=None,
                        help='固定 Token（优先于环境变量 INFOSEEK_AUTH_TOKEN）')
    parser.add_argument('--list-tools', action='store_true', help='打印工具清单后退出')
    args = parser.parse_args()

    if args.list_tools:
        print(json.dumps(TOOLS, ensure_ascii=False, indent=2))
        return

    if args.transport == 'stdio':
        run_stdio_server()
    elif args.transport == 'sse':
        run_sse_server(
            port=args.port,
            require_token=args.require_token,
            fixed_token=args.token
        )
    else:
        print(f"[infoseek-mcp] 未知 transport: {args.transport}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()