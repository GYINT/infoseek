#!/usr/bin/env python3
"""
text_tokenizer.py — 全仓唯一分词真源（GA5 分词单源化 / v1.8.4）

治理动机
--------
v1.7.8 起同一套「jieba 优先 → 缺失回退纯 Python」分词逻辑存在**两份独立实现**：
  - `anchor_adapter._tokenize_subject`   —— 词级命中率口径（P1#4）
  - `infoseek_pipeline._tokenize_query`  —— 相关性门控多字词硬门槛（P0#2）
v1.8.2 仅做「回退算法对齐」（split → findall，消除中英混合串跨边界 2-gram 噪声），
两份代码仍并存 → ROADMAP **GA5「分词单源化」保持开放**，任一侧口径调整都可能再次漂移
（审计 P1-1「同算法」声明曾被实测证伪：6 样本 3 分叉）。

本模块把该算法收敛为唯一实现 `tokenize_text()`，两侧退化为薄封装委托：
  - `_tokenize_subject(s)`  → `tokenize_text(s)`
  - `_tokenize_query(q)`    → `tokenize_text(q, require_chinese=True)`

保留的唯一差异（有意设计，非漂移）
------------------------------------
`require_chinese=True` 时，纯英文/数字输入返回空集 —— 服务于 `_filter_relevant` 的
「中文多字词硬门槛」语义（纯英文 query 不应触发中文门槛）。subject 侧无门控
（服务通用词级命中率，需处理任意语言 subject）。除此之外**算法完全单源**。

设计约束
--------
- 零第三方硬依赖：jieba 可选（装了更准），缺失自动回退纯 Python；探测结果进程内缓存
  （避免每次调用 try-import —— 见 GA8 教训：import find_spec 在热路径可占 89% 耗时）。
- 无副作用、无模块级可变业务状态（仅探测/告警 flag），可安全被 anchor_adapter 与
  pipeline 双向导入（本模块不 import 任何 infoseek 内部模块 → 天然无循环依赖）。
- 版本维度：mod-v1.0.0（模块内部版本），非 skill 对外版本
  （对外唯一真源见 `mcp_tools_common.SKILL_VERSION`）。
"""

from __future__ import annotations

import logging
import re

__all__ = ['tokenize_text', 'has_chinese', 'MOD_VERSION']

MOD_VERSION = '1.0.0'

log = logging.getLogger(__name__)

# ── 口径常量（单源；调整此处即全局生效） ────────────────────────────
_CHN = r'[\u4e00-\u9fff]'
# 分段正则：中文连续段 / 英文数字 ≥2 字符整词（v1.8.2 统一口径，禁用 split 防跨边界噪声）
_SEG_RE = re.compile(_CHN + r'+|[A-Za-z0-9]{2,}')
_CHN_RE = re.compile(_CHN)
_MIN_WORD_LEN = 2        # 最短词长（杜绝单字噪音，如「新（汉语汉字）」）
_CHN_WHOLE_SEG_MAX = 4   # 中文连续段 ≤ 此长度整段成词，否则切 2-gram

# ── 进程内探测/告警状态 ────────────────────────────────────────────
_JIEBA = None            # 可用时为 jieba 模块对象
_JIEBA_PROBED = False    # 是否已探测（避免重复 try-import）
_FALLBACK_WARNED = False # 回退告警是否已发（一次性）


def has_chinese(text: str) -> bool:
    """是否含中文字符（require_chinese 门控判据）。"""
    return bool(text) and bool(_CHN_RE.search(text))


def _probe_jieba():
    """探测 jieba 可用性（进程内一次；不可用返回 None）。"""
    global _JIEBA, _JIEBA_PROBED
    if not _JIEBA_PROBED:
        try:
            import jieba
            _JIEBA = jieba
        except Exception:
            _JIEBA = None
        _JIEBA_PROBED = True
    return _JIEBA


def _warn_fallback_once() -> None:
    """jieba 缺失一次性告警（原 pipeline._RELEVANCE_WARNED 语义迁移至此）。"""
    global _FALLBACK_WARNED
    if not _FALLBACK_WARNED:
        log.warning("[tokenizer] jieba 未安装 → 回退纯 Python 分词"
                    "（多字词精度下降；建议 pip install jieba）")
        _FALLBACK_WARNED = True


def _fallback_tokens(text: str) -> set:
    """纯 Python 回退分词（jieba 缺失/异常时）。

    中文连续段：≤4 字整段成词；>4 字滑窗 2-gram。
    英文/数字：≥2 字符整词（不跨中英边界切分）。
    """
    words = set()
    for seg in _SEG_RE.findall(text):
        if _CHN_RE.search(seg):
            if len(seg) <= _CHN_WHOLE_SEG_MAX:
                words.add(seg.lower())
            else:
                for i in range(len(seg) - 1):
                    words.add(seg[i:i + 2].lower())
        else:
            words.add(seg.lower())
    return {w for w in words if len(w) >= _MIN_WORD_LEN}


def tokenize_text(text: str, require_chinese: bool = False,
                  warn_on_fallback: bool = False) -> set:
    """唯一分词入口：提取 ≥2 字符的主体词集合（小写归一）。

    Args:
        text: 待分词文本（query / subject / 任意文本）。
        require_chinese: True 时纯英文/数字输入直接返回空集（中文硬门槛语义）。
        warn_on_fallback: True 时 jieba 缺失发一次性告警（相关性门控侧启用）。

    Returns:
        set[str] —— 词集合；空输入 / 门控不通过 → set()。
    """
    if not text:
        return set()
    if require_chinese and not has_chinese(text):
        return set()

    jieba = _probe_jieba()
    if jieba is not None:
        try:
            return {w.strip().lower() for w in jieba.lcut(text)
                    if len(w.strip()) >= _MIN_WORD_LEN}
        except Exception:
            pass  # jieba 运行期异常（词典损坏等）→ 回退纯 Python

    if warn_on_fallback:
        _warn_fallback_once()
    return _fallback_tokens(text)
