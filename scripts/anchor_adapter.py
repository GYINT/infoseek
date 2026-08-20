#!/usr/bin/env python3
"""
anchor_adapter.py — v1 Anchor_Score 实现（v2.0.2 标记 DEPRECATED）

v2.0.2 状态：
- 本文件保留为 v1 实现（向后兼容）
- 新代码请用 core/anchor_score_v2.compute_final_score_v2()
- v2.0.2 重构：纯函数式 + 模块化
- 旧 calculate_score() 仍可用，但内部调用 v2 + 发出 DeprecationWarning

演进史：
- v1.2.0: 四轴加权 + 双层复活
- v1.5.0: 五维 + 时间衰减
- v1.6.0: 第 6 维（跨平台）+ 跨主题分析
- v1.7.0: 第 8 维（语义相似度）
- v1.7.4: TF-IDF 退化修复 → Jaccard
- v1.8.1: domain_router 联动
- v1.9.0: 特殊 subject 字典展开
- v2.0.2: 重构为 core/anchor_score_v2.py（纯函数）
"""

from typing import List, Optional
import os
import re
import sys
import warnings
from pathlib import Path

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE', str(Path.home())))
# v1.0.0 状态层中立：归档目录解析统一走 state_dir（env INFOSEEK_ARCHIVE → ~/infoseek-archives）
CORE_DIR = Path(__file__).parent.parent / 'core'
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
from state_dir import get_archives_dir


def infos_to_seek(anchor: dict) -> Optional[dict]:
    """
    将 infos 锚点转换为 seek 意图卡片

    输入: {name, platform, type?, score, entry, entry_type}
    输出: {platform, type, quantity, tech, entry, entry_type}
    返回 None 表示过滤（噪声锚点 score < 40）
    """
    # 噪声过滤
    if anchor.get("score", 0) < 40:
        return None

    platform_lower = (anchor.get("platform") or "").lower()
    entry_type = anchor.get("entry_type", "URL")
    entry = anchor.get("entry", "")

    # ─── 规则 1: URL 类 ───
    if entry_type == "URL":
        if any(vp in platform_lower for vp in ["b站", "bilibili", "youtube", "哔哩哔哩"]):
            return {"platform": anchor["platform"], "type": "video", "quantity": "single",
                    "tech": "auto", "entry": entry, "entry_type": "URL"}
        if any(pp in platform_lower for pp in ["知乎", "zhihu"]):
            return {"platform": anchor["platform"], "type": "article", "quantity": "single",
                    "tech": "可能需要登录", "entry": entry, "entry_type": "URL"}
        return {"platform": "web", "type": "article", "quantity": "single",
                "tech": "auto", "entry": entry, "entry_type": "URL"}

    # ─── 规则 2: 名称类（需搜索发现）───
    if entry_type in ("名称", "频道名"):
        if any(bp in platform_lower for bp in ["b站", "bilibili", "哔哩哔哩"]):
            return {"platform": "B站", "type": "author_content", "quantity": "recent_10",
                    "tech": "bilibili-api", "entry": entry, "entry_type": "频道名"}
        if any(wp in platform_lower for wp in ["公众号", "wechat", "微信"]):
            return {"platform": "公众号", "type": "content", "quantity": "unknown",
                    "tech": "需用户提供分享链接", "entry": entry, "entry_type": "名称"}
        if "视频号" in platform_lower:
            return {"platform": "视频号", "type": "content", "quantity": "unknown",
                    "tech": "需用户提供分享链接", "entry": entry, "entry_type": "名称"}
        if any(zp in platform_lower for zp in ["知识星球", "zsxq", "星球"]):
            return {"platform": "知识星球", "type": "content", "quantity": "unknown",
                    "tech": "需Cookie", "entry": entry, "entry_type": "名称"}
        return {"platform": "综合", "type": "author_content", "quantity": "recent_10",
                "tech": "需搜索", "entry": entry, "entry_type": "名称"}

    # 兜底
    return {"platform": "web", "type": "unknown", "quantity": "single",
            "tech": "需确认", "entry": entry, "entry_type": "URL"}


# ═══════════════════════════════════════════════════════════════
# v1.2 新增: 四轴加权评分 + 双层复活机制
# ═══════════════════════════════════════════════════════════════

# 权重配置
WEIGHTS = {"interaction": 0.20, "topic_match": 0.30, "credibility": 0.40, "activity": 0.10}

# Ⅰ级阈值（满分档）
TIER1_THRESHOLD = {
    "interaction": 100,
    "topic_match": 100,
    "credibility": 90,  # 来源可信度Ⅰ级从90起（因最高1级含90/95/100三档）
    "activity": 100,
}


def compute_anchor_score(
    interaction: int = 0,
    topic_match: int = 0,
    credibility: int = 0,
    activity: int = 0,
) -> dict:
    """
    四轴加权评分 + 双层复活机制 (v1.2)

    参数: 各维度原始评分 (0-100)
    返回: {
        raw_score: float,          # 原始加权分
        after_whitelist: float,     # 白名单复活后
        after_top3: float,         # TOP 1~3 复活后
        classification: str,       # 🟢核心/🟡潜力/❌噪声
        dimensions: dict,          # 四维原始值
        whitelist_triggered: bool,  # 白名单是否触发
        top3_triggered: bool,       # TOP复活是否触发
        peak_gate_blocked: bool,    # 峰值门控是否拦截
        peak_dimension: int,        # 最高维分值
    }
    """
    dims = {
        "interaction": interaction,
        "topic_match": topic_match,
        "credibility": credibility,
        "activity": activity,
    }

    # 1. 原始加权分
    raw = (
        interaction * WEIGHTS["interaction"]
        + topic_match * WEIGHTS["topic_match"]
        + credibility * WEIGHTS["credibility"]
        + activity * WEIGHTS["activity"]
    )
    score = raw

    # 2. 第一层: 白名单复活
    whitelist_triggered = False
    peak_dim = max(dims.values())

    # 判断任一维度达Ⅰ级
    has_tier1 = False
    tier1_dim = None
    for key, val in dims.items():
        if val >= TIER1_THRESHOLD.get(key, 100):
            has_tier1 = True
            tier1_dim = key
            break

    if has_tier1:
        avg = sum(dims.values()) / 4.0
        # 检查是否有两维(除触发维外) ≥ avg
        other_dims = {k: v for k, v in dims.items() if k != tier1_dim}
        if sum(1 for v in other_dims.values() if v >= avg) >= 2:
            whitelist_triggered = True
            if score < 70:
                score = 70

    # 3. 第二层: TOP 1~3 复活（此处仅计算单锚点是否具备复活资格）
    #    实际 TOP 排序需外部处理；此函数返回资格标记
    top3_triggered = False
    peak_gate_blocked = False

    if 40 <= score < 70 and credibility >= 40:
        if peak_dim > 65:
            top3_triggered = True  # 有资格参与TOP排序
            # 注意：实际是否复活取决于TOP排名，此处标记资格
        else:
            peak_gate_blocked = True

    # 4. 分类
    final_score = score
    if final_score >= 70:
        classification = "🟢核心"
    elif final_score >= 40:
        classification = "🟡潜力"
    else:
        classification = "❌噪声"

    return {
        "raw_score": round(raw, 1),
        "after_whitelist": round(score, 1),
        "after_top3": round(final_score, 1),
        "classification": classification,
        "dimensions": dims,
        "whitelist_triggered": whitelist_triggered,
        "top3_triggered": top3_triggered,
        "peak_gate_blocked": peak_gate_blocked,
        "peak_dimension": peak_dim,
    }


def apply_resurrection_batch(anchors: list) -> list:
    """
    批量执行双层复活（含 TOP 1~3 排序）。

    输入: [{interaction, topic_match, credibility, activity, ...}, ...]
    输出: 每个锚点附加复活结果字段
    """
    # 第一轮: 计算原始分 + 白名单复活
    for a in anchors:
        result = compute_anchor_score(
            interaction=a.get("interaction", 0),
            topic_match=a.get("topic_match", 0),
            credibility=a.get("credibility", 0),
            activity=a.get("activity", 0),
        )
        a["_score_result"] = result
        a["score"] = result["after_whitelist"]  # 先应用白名单

    # 第二轮: TOP 1~3 复活（只在潜力区中选）
    candidates = [
        a for a in anchors
        if 40 <= a["_score_result"]["after_whitelist"] < 70
        and a.get("credibility", 0) >= 40
        and a["_score_result"]["peak_dimension"] > 65  # 峰值门控
        and a["_score_result"]["top3_triggered"]  # 确认有资格
    ]
    candidates.sort(key=lambda a: a["_score_result"]["after_whitelist"], reverse=True)

    for i, a in enumerate(candidates[:3]):
        a["score"] = 70  # 复活至 70
        a["_score_result"]["after_top3"] = 70
        a["_score_result"]["top3_triggered"] = True
        a["_score_result"]["classification"] = "🟢核心"
        a["_score_result"]["peak_gate_blocked"] = False

    # 未被TOP复活的潜力锚点，保持白名单后分数
    for a in anchors:
        if a["_score_result"]["classification"] == "🟡潜力":
            a["_score_result"]["after_top3"] = a["_score_result"]["after_whitelist"]

    return anchors


# ═══════════════════════════════════════════════════════════════
# v1.5.0 新增: 五维评分 + 时间衰减（向后兼容，v1.4.0 默认行为不变）
# ═══════════════════════════════════════════════════════════════

# v1.5.0 五维权重（活跃度移出总分，改为独立时间衰减）
WEIGHTS_V15 = {
    "interaction": 0.20,
    "topic_match": 0.30,
    "credibility": 0.40,
    "llm_readability": 0.10,  # 🆕 LLM 上下文可读性
}

# LLM 上下文可读性四级阈值
TIER1_THRESHOLD_V15 = {
    "interaction": 100,
    "topic_match": 100,
    "credibility": 90,
    "llm_readability": 100,
}

# 时间衰减系数
TIME_DECAY = {
    "fresh": 1.0,        # < 30 天
    "recent": 0.9,       # 30-90 天
    "aging": 0.7,        # 90-180 天
    "old": 0.5,          # 180-365 天
    "ancient": 0.3,      # > 365 天
}


def compute_llm_readability(
    structure_score: int = 50,
    noise_score: int = 50,
    metadata_score: int = 50,
    length_score: int = 50,
) -> int:
    """
    LLM 上下文可读性维度计算 (v1.5.0)

    参数: 4 个子维度（0-100）
    返回: 加权总分 (0-100)
    """
    weights = {"structure": 0.30, "noise": 0.30, "metadata": 0.20, "length": 0.20}
    total = (
        structure_score * weights["structure"]
        + noise_score * weights["noise"]
        + metadata_score * weights["metadata"]
        + length_score * weights["length"]
    )
    return int(round(total))


def get_time_decay_factor(days_since_published: int) -> float:
    """
    时间衰减因子 (v1.5.0)

    输入: 自发布以来的天数
    返回: 衰减系数
    """
    if days_since_published < 30:
        return TIME_DECAY["fresh"]
    elif days_since_published < 90:
        return TIME_DECAY["recent"]
    elif days_since_published < 180:
        return TIME_DECAY["aging"]
    elif days_since_published < 365:
        return TIME_DECAY["old"]
    else:
        return TIME_DECAY["ancient"]


def compute_anchor_score_v15(
    interaction: int = 0,
    topic_match: int = 0,
    credibility: int = 0,
    llm_readability: int = 0,
    days_since_published: int = 0,
) -> dict:
    """
    v1.5.0 五维评分 + 时间衰减

    与 v1.4.0 的核心差异:
      - 活跃度从总分维度移出（避免与可信度重复加权）
      - 新增 LLM 上下文可读性维度（10%）
      - 时间衰减独立计算（乘在总分上）

    参数: 4 维原始评分 (0-100) + 发布天数
    返回: {
        raw_score: float,            # 五维加权原始分
        after_decay: float,          # 时间衰减后
        after_whitelist: float,      # 白名单复活后
        classification: str,         # 🟢核心/🟡潜力/❌噪声
        dimensions: dict,            # 5 维原始值
        decay_factor: float,         # 时间衰减系数
        whitelist_triggered: bool,
        version: "v1.5.0",
    }
    """
    dims = {
        "interaction": interaction,
        "topic_match": topic_match,
        "credibility": credibility,
        "llm_readability": llm_readability,
    }

    # 1. 五维原始加权分
    raw = sum(dims[k] * WEIGHTS_V15[k] for k in dims)

    # 2. 时间衰减
    decay = get_time_decay_factor(days_since_published)
    after_decay = raw * decay

    score = after_decay

    # 3. 白名单复活（与 v1.4.0 一致）
    whitelist_triggered = False
    peak_dim = max(dims.values())

    has_tier1 = False
    tier1_dim = None
    for key, val in dims.items():
        if val >= TIER1_THRESHOLD_V15.get(key, 100):
            has_tier1 = True
            tier1_dim = key
            break

    if has_tier1:
        avg = sum(dims.values()) / 4.0
        other_dims = {k: v for k, v in dims.items() if k != tier1_dim}
        if sum(1 for v in other_dims.values() if v >= avg) >= 2:
            whitelist_triggered = True
            if score < 70:
                score = 70

    # 4. 分类
    if score >= 70:
        classification = "🟢核心"
    elif score >= 40:
        classification = "🟡潜力"
    else:
        classification = "❌噪声"

    return {
        "raw_score": round(raw, 1),
        "decay_factor": decay,
        "after_decay": round(after_decay, 1),
        "after_whitelist": round(score, 1),
        "classification": classification,
        "dimensions": dims,
        "whitelist_triggered": whitelist_triggered,
        "peak_dimension": peak_dim,
        "version": "v1.5.0",
    }


# ═══════════════════════════════════════════════════════════════
# v1.7.0 新增: 第 8 维（语义相似度）
# ═══════════════════════════════════════════════════════════════

def compute_semantic_similarity(text: str, subject: str, method: str = "jaccard") -> int:
    """
    语义相似度评分 (v1.7.0 第 8 维)

    v1.7.4 默认算法: 关键词集合 Jaccard 相似度（三跑择优 summa+jieba+regex）
    v1.7.3 实验版: TF-IDF（公式退化，已废弃，不推荐使用）
    v1.7.2 baseline: TF-IDF 加权（中英混合友好但公式复杂）
    v1.7.1 baseline: summa 关键词 + Jaccard / 字符串包含

    参数:
        text: 待评估的源文本
        subject: 调研主题
        method: 算法 ("jaccard" / "tfidf" / "summa" / "string")
    返回: 0-100 分
    """
    if not text or not subject:
        return 0

    try:
        if method == "jaccard":
            return _jaccard_similarity(text, subject)

        elif method == "tfidf":
            # v1.7.4 兼容路径：仍可用，但内部已重定向到 jaccard
            # （v1.7.3 退化的 TF-IDF 已废弃，保留入口仅作向后兼容）
            import warnings
            warnings.warn(
                "method='tfidf' is deprecated since v1.7.4 due to v1.7.3 formula "
                "degradation; falling back to Jaccard similarity. "
                "Use method='jaccard' explicitly.",
                DeprecationWarning,
                stacklevel=2
            )
            return _jaccard_similarity(text, subject)

        elif method == "summa":
            from summa.keywords import keywords as summa_keywords

            text_kw_text = summa_keywords(text, words=20)
            subject_kw_text = summa_keywords(subject, words=10)

            text_kw = set(k.strip() for k in text_kw_text.split('\n') if k.strip())
            subject_kw = set(k.strip() for k in subject_kw_text.split('\n') if k.strip())

            # summa 对中文支持差（subject 关键词为空）→ 降级到 jaccard
            if not text_kw or not subject_kw:
                return _jaccard_similarity(text, subject)

            # Jaccard 相似度 = 交集 / 并集
            intersection = text_kw & subject_kw
            union = text_kw | subject_kw
            return int(len(intersection) / len(union) * 100)

        else:
            return _string_containment_similarity(text, subject)

    except Exception:
        # summa/jaccard 不可用 → 最低级降级：字符串包含
        return _string_containment_similarity(text, subject)


def _string_containment_similarity(text: str, subject: str) -> int:
    """字符串包含相似度（v1.7.1 baseline，summa 不可用或中文场景的降级）

    返回: 0-100（基于 subject 关键词在 text 中出现的比例）
    """
    if not text or not subject:
        return 0

    # 提取 subject 的关键词（按空格切分 + 中文逐字）
    subject_words = set()

    # 英文/数字词（按空格切分）
    for word in re.findall(r'[a-zA-Z0-9]{2,}', subject):
        subject_words.add(word.lower())

    # 中文字符（每个汉字作为关键词）
    for char in re.findall(r'[\u4e00-\u9fff]', subject):
        subject_words.add(char)

    if not subject_words:
        return 0

    text_lower = text.lower()
    matched = sum(1 for w in subject_words if w in text_lower)
    return int(matched / len(subject_words) * 100)


# v1.9.0 修补④ 特殊 subject 字典展开
SPECIAL_SUBJECTS = {
    'last30days': 'last30days recent community discussions hacker news reddit x.com emerging ai agent techniques trends',
    'arxiv': 'arxiv preprints academic papers research latest papers machine learning',
    'github-trending': 'github trending repositories popular projects recent activity',
    'wechat-mp': '微信公众号文章 公众号 微信文章 最新',
    'zhihu': '知乎 问答 讨论 热门话题 最新',
}


def _expand_special_subject(subject: str) -> str:
    """短 subject 字典后缀展开（v1.9.0 修补）

    针对专有名词 subject（Last30days/Arxiv 等）只有 1-2 个词，导致 Jaccard 永远 0 分的问题，
    在 Jaccard 计算前先用字典展开为长串查询词组。
    """
    subj_lower = subject.lower().strip()
    for canonical, expansion in SPECIAL_SUBJECTS.items():
        if subj_lower == canonical or subj_lower == expansion.lower():
            return expansion
    return subject


def _jaccard_similarity(text: str, subject: str) -> int:
    """关键词集合 Jaccard 相似度

    v1.7.4 替代 v1.7.3 退化的 TF-IDF（公式退化为常数 stub）。
    v1.9.0 修补特殊 subject（短专有名词字典展开）。

    公式:
      sim = |A ∩ B| / |A ∪ B| × 100（×自适应系数 1.2-1.8）

    思路:
      1. 字典展开 subject（v1.9.0）
      2. 三跑流水线提取 text 关键词（summa + jieba + regex fallback）
      3. 同样三跑流水线提取 subject 关键词
      4. 计算 Jaccard
      5. 按文本长度自适应加权（短 1.8 / 中 1.5 / 长 1.2）

    返回: 0-100 分
    """
    # v1.9.0 特殊 subject 字典展开
    subject = _expand_special_subject(subject)

    if not text or not subject:
        return 0

    try:
        # 1. 提取 text 关键词集合
        text_kw = _extract_keywords_three_run(text, max_keywords=20)
        # 2. 提取 subject 关键词集合
        subject_kw = _extract_keywords_three_run(subject, max_keywords=10)

        if not text_kw or not subject_kw:
            return _string_containment_similarity(text, subject)

        # 3. Jaccard 相似度
        intersection = text_kw & subject_kw
        union = text_kw | subject_kw
        if not union:
            return 0
        sim = int(len(intersection) / len(union) * 100)
        # v1.8.1 强化：自适应加权（短文本高系数，长文本低系数）
        # 短文本（<50 词）更容易匹配 → 系数 1.8（确保覆盖 15-30）
        # 中等文本（50-200 词）→ 系数 1.5（保持 15-35）
        # 长文本（>200 词）→ 系数 1.2（避免 100 分堆积）
        text_word_count = len(text.split()) if text else len(text_kw) * 3
        if text_word_count < 50:
            factor = 1.8
        elif text_word_count < 200:
            factor = 1.5
        else:
            factor = 1.2
        sim = min(int(sim * factor), 100)
        return min(max(sim, 0), 100)

    except Exception:
        return _string_containment_similarity(text, subject)


def _extract_keywords_three_run(text: str, max_keywords: int = 20) -> set:
    """三跑提取关键词集合（v1.7.4 新增，独立于 summarize_adapter）

    summa + jieba + regex fallback，取关键词数量最多者获胜。
    用于 _jaccard_similarity() 计算语义相似度。

    v1.0.0 增强：新增第 4 跑「零依赖共识兜底」——仅当 summa/jieba/regex
    全部无结果时启用（零依赖分词为最终兜底，不参与与外部分词竞争）。

    返回: set of 关键词字符串（小写）
    """
    candidates = []

    # 1. summa 路径（英文友好）
    try:
        from summa.keywords import keywords as summa_keywords
        kw_text = summa_keywords(text, words=max_keywords)
        kw_set = set(
            k.strip().lower()
            for k in kw_text.split('\n')
            if k.strip() and len(k.strip()) >= 2
        )
        if kw_set:
            candidates.append(("summa", kw_set))
    except Exception:
        pass

    # 2. jieba 路径（中文友好）
    try:
        import jieba.analyse
        kw_list = jieba.analyse.textrank(text, topK=max_keywords, withWeight=False)
        kw_set = set(k.strip() for k in kw_list if k.strip() and len(k.strip()) >= 2)
        if kw_set:
            candidates.append(("jieba", kw_set))
    except Exception:
        pass

    # 3. regex fallback（零依赖，纯英文）
    try:
        kw_set = _regex_extract_keywords_en(text, top_n=max_keywords)
        if kw_set:
            candidates.append(("regex", kw_set))
    except Exception:
        pass

    # 4. 零依赖共识兜底（v1.0.0，最终防线，纯标准库，中英文皆可）
    if not candidates:
        try:
            from infoseek_zerodep_nlp import extract_keywords as zd_extract_keywords
            kw_set = set(
                w.lower()
                for w, _ in zd_extract_keywords(text, max_kw=max_keywords)
                if w.strip() and len(w.strip()) >= 2
            )
            if kw_set:
                candidates.append(("zerodep", kw_set))
        except Exception:
            pass

    if not candidates:
        return set()

    # 取关键词数量最多的获胜
    chosen = max(candidates, key=lambda x: len(x[1]))
    return chosen[1]


def _regex_extract_keywords_en(text: str, top_n: int = 20) -> set:
    """纯正则+词频英文关键词提取（v1.7.4 独立版本）

    与 summarize_adapter._regex_summarize() 类似但只返回 set。
    用于 _extract_keywords_three_run() 的 fallback 路径。
    """
    import re as re_mod
    from collections import Counter

    # 1. token 提取
    tokens = re_mod.findall(r'[a-zA-Z][a-zA-Z0-9]{2,}', text)
    tokens_lower = [t.lower() for t in tokens]

    # 2. 停用词过滤
    stop_words = set([
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had',
        'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his',
        'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'way', 'who',
        'boy', 'did', 'use', 'than', 'this', 'that', 'with', 'from',
        'have', 'will', 'they', 'been', 'more', 'what', 'when', 'make', 'like',
        'over', 'such', 'also', 'into', 'then', 'them', 'very', 'just',
        'about', 'where', 'would', 'there', 'their', 'these', 'which', 'should'
    ])

    filtered = [t for t in tokens_lower if t not in stop_words and len(t) >= 3]
    counter = Counter(filtered)
    # 只返回 set，不做摘要
    top_keywords = set(w for w, _ in counter.most_common(top_n))
    return top_keywords


# v1.7.4 向后兼容 alias
def _tfidf_similarity(text: str, subject: str) -> int:
    """v1.7.4 alias: 保留向后兼容，重定向到 _jaccard_similarity

    v1.7.3 退化的 TF-IDF 公式已废弃。函数名保留以便调用方代码无须修改。
    推荐使用 _jaccard_similarity() 或 method="jaccard"。
    """
    return _jaccard_similarity(text, subject)


def calculate_score(
    source: dict,
    subject: str = "",
    with_llm_readability: bool = False,
    days_since_published: int = 0,
    with_cross_platform: bool = False,
    platforms: int = 1,
    with_semantic: bool = False,
    semantic_text: str = None,
    with_domain: bool = False,
    domain_profile: dict = None,
) -> dict:
    """
    统一评分入口 (v1.5.0/v1.6.0/v1.7.0/v1.8.1)

    v1.5.0 默认：四维评分 + 可选 LLM 维度
    v1.6.0 扩展：可选第 6 维（跨平台分布度）
    v1.7.0 扩展：可选第 8 维（语义相似度）
    v1.8.1 扩展：可选 domain_profile（领域路由权重微调）
      - with_domain: 是否启用 v1.8.1 领域加权
      - domain_profile: dict, 含 raw YAML 文本（用于信任源加权），如 None 则自动调用 domain_router.detect_domain(subject)
    v1.7.0 扩展：可选第 8 维（语义相似度，基于 summa 关键词）

    参数:
        source: {interaction, topic_match, credibility, llm_readability?, ...}
        subject: 调研主题
        with_llm_readability: 是否启用 v1.5.0 五维
        days_since_published: 自发布以来的天数
        with_cross_platform: 是否启用 v1.6.0 第 6 维
        platforms: 同一主题在不同平台/来源的出现数
        with_semantic: 是否启用 v1.7.0 第 8 维
        semantic_text: 用于提取关键词的文本（启用语义相似度时必填）

    返回:
        v1.4.0 格式（默认）或 v1.5.0 格式（启用 LLM）或 v1.6.0 格式（启用 cross_platform）或 v1.7.0 格式（启用 semantic）
    """
    if not with_llm_readability:
        # 向后兼容 v1.4.0
        return compute_anchor_score(
            interaction=source.get("interaction", 0),
            topic_match=source.get("topic_match", 0),
            credibility=source.get("credibility", 0),
            activity=source.get("activity", 0),
        )

    # v1.5.0 五维
    llm_read = source.get("llm_readability")
    if llm_read is None:
        llm_read = compute_llm_readability(
            structure_score=source.get("structure_score", 50),
            noise_score=source.get("noise_score", 50),
            metadata_score=source.get("metadata_score", 50),
            length_score=source.get("length_score", 50),
        )

    base_result = compute_anchor_score_v15(
        interaction=source.get("interaction", 0),
        topic_match=source.get("topic_match", 0),
        credibility=source.get("credibility", 0),
        llm_readability=llm_read,
        days_since_published=days_since_published,
    )

    # v1.6.0 第 6 维（可选）
    if with_cross_platform:
        cp_score = compute_cross_platform_score(platforms)
        base_result["cross_platform_score"] = cp_score
        base_result["cross_platform_platforms"] = platforms
        base_result["version"] = "v1.6.0"
        recomputed = (
            base_result["after_whitelist"] * 0.95 +
            cp_score * 0.05
        )
        base_result["after_whitelist"] = round(recomputed, 1)
        if recomputed >= 70:
            base_result["classification"] = "🟢核心"
        elif recomputed >= 40:
            base_result["classification"] = "🟡潜力"
        else:
            base_result["classification"] = "❌噪声"

    # v1.7.0 第 8 维（可选）
    if with_semantic:
        sem_text = semantic_text or source.get("text", "")
        sem_score = compute_semantic_similarity(sem_text, subject)
        base_result["semantic_similarity"] = sem_score
        base_result["version"] = "v1.7.0"
        # 第 8 维占 5%（同第 6 维权重）
        # 五维（含第 6 维）= 95%，第 8 维 = 5%
        recomputed = base_result["after_whitelist"] * 0.95 + sem_score * 0.05
        base_result["after_whitelist"] = round(recomputed, 1)
        if recomputed >= 70:
            base_result["classification"] = "🟢核心"
        elif recomputed >= 40:
            base_result["classification"] = "🟡潜力"
        else:
            base_result["classification"] = "❌噪声"

    # v1.8.1 领域加权（可选）
    if with_domain:
        if domain_profile is None:
            # 自动调用 domain_router
            try:
                from domain_router import detect_domain
                routing = detect_domain(subject)
                domain_profile = routing.get('profile_path') and {'name': routing['domain'], 'raw': open(routing['profile_path']).read()}
            except (ImportError, Exception):
                domain_profile = None

        if domain_profile:
            bonus = _compute_domain_bonus(source, domain_profile)
            base_result["domain_profile"] = domain_profile.get('name', '')
            base_result["domain_bonus"] = bonus
            base_result["version"] = "v1.8.1"
            # 领域加权 5%（叠加在第 8 维之后）
            recomputed = base_result["after_whitelist"] * 0.95 + min(100, base_result["after_whitelist"] + bonus) * 0.05
            base_result["after_whitelist"] = round(recomputed, 1)

    return base_result


def _compute_domain_bonus(source: dict, profile: dict) -> int:
    """计算领域 profile 的信任源加权（v1.8.1）

    参数:
        source: 源 dict（含 url/platform）
        profile: 领域 profile dict（含 raw YAML 文本）

    返回:
        0-20 分的加分
    """
    if not profile:
        return 0

    raw = profile.get('raw', '')
    url_lower = source.get('url', '').lower()
    platform = source.get('platform', '').lower()

    # 从 profile raw 文本提取信任源关键词（中文 2-4 字 + 英文专有名词）
    trust_keywords = set()
    for line in raw.split('\n'):
        # 中文 2-4 字关键词
        for kw in re.findall(r'[\u4e00-\u9fff]{2,4}', line):
            if kw not in ['权重', 'Tier', '类型', '来源', 'Tier 1', '适用场景']:
                trust_keywords.add(kw)
        # 英文专有名词（首字母大写）
        for kw in re.findall(r'\b[A-Z][a-zA-Z]{2,}', line):
            trust_keywords.add(kw)

    bonus = 0
    for kw in trust_keywords:
        if kw.lower() in url_lower:
            bonus += 4
        if kw in platform:
            bonus += 3

    return min(bonus, 20)


# ═══════════════════════════════════════════════════════════════
# v1.6.0 新增: 第 6 维（跨平台分布度）+ 跨主题关联分析
# ═══════════════════════════════════════════════════════════════

# 跨平台分布度等级
CROSS_PLATFORM_TIERS = {
    "single": 10,        # 单一来源/平台
    "narrow": 40,        # 2-3 个平台
    "moderate": 70,      # 4-6 个平台
    "broad": 90,         # 7-10 个平台
    "ubiquitous": 100,   # >10 个平台
}


def compute_cross_platform_score(platforms: int) -> int:
    """
    跨平台分布度评分 (v1.6.0 第 6 维)

    参数: 同一主题在不同平台/来源的出现数
    返回: 0-100 分
    """
    if platforms <= 0:
        return 0
    elif platforms == 1:
        return CROSS_PLATFORM_TIERS["single"]
    elif platforms <= 3:
        return CROSS_PLATFORM_TIERS["narrow"]
    elif platforms <= 6:
        return CROSS_PLATFORM_TIERS["moderate"]
    elif platforms <= 10:
        return CROSS_PLATFORM_TIERS["broad"]
    else:
        return CROSS_PLATFORM_TIERS["ubiquitous"]


def _extract_concepts(text: str, top_k: int = 20) -> set:
    """v1.7.2 新增：提取文本的概念集（关键词）

    策略：jieba 关键词（中文友好）+ 英文 token（summa 词频）
    """
    import re as re_mod
    concepts = set()

    # 1. jieba 关键词（中文）
    try:
        import jieba.analyse
        kw_list = jieba.analyse.textrank(text, topK=top_k, withWeight=False)
        for kw in kw_list:
            if len(kw) >= 2:
                concepts.add(kw)
    except Exception:
        pass

    # 2. 英文 token（≥3 字符）
    for t in re_mod.findall(r'[a-zA-Z][a-zA-Z0-9]{2,}', text):
        concepts.add(t.lower())

    # 3. 中文 2-4 字组合（补充 jieba 未覆盖）
    for segment in re_mod.findall(r'[\u4e00-\u9fff]{2,4}', text):
        concepts.add(segment)

    return concepts


# v2.0.2 shim: 旧 calculate_score() → 新 v2 计算（带 DeprecationWarning）
def _deprecation_warning_v202():
    warnings.warn(
        "anchor_adapter.calculate_score() is deprecated since v2.0.2; "
        "use core.anchor_score_v2.compute_final_score_v2() instead.",
        DeprecationWarning,
        stacklevel=3,
    )


# v2.0.2 重构：calculate_score() 内部转调 v2（保持 v1 行为兼容）
def calculate_score(
    source: dict,
    subject: str = "",
    with_llm_readability: bool = False,
    days_since_published: int = 0,
    with_cross_platform: bool = False,
    platforms: int = 1,
    with_semantic: bool = False,
    semantic_text: str = None,
    with_domain: bool = False,
    domain_profile: dict = None,
) -> dict:
    """v1 calculate_score() — v2.0.2 起标记 DEPRECATED

    内部转调 core/anchor_score_v2.compute_final_score_v2()。
    旧调用方代码无须修改（行为兼容）。
    """
    _deprecation_warning_v202()

    # v2.0.2: 转调纯函数 v2 评分
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / 'core'))
        from anchor_score_v2 import compute_final_score_v2
        return compute_final_score_v2(
            source, subject,
            with_llm_readability=with_llm_readability,
            with_cross_platform=with_cross_platform,
            platforms=platforms,
            with_semantic=with_semantic,
            semantic_text=semantic_text,
            days_since_published=days_since_published,
            with_domain=with_domain,
            domain_profile=domain_profile,
        )
    except ImportError:
        # fallback：返回基础评分（不修改 source）
        return {
            'raw_score': source.get('score', 50),
            'after_whitelist': source.get('score', 50),
            'classification': '🟡潜力',
            'version': '2.0.2-fallback',
        }


def cross_subject_analysis(subjects: List[str], archives_dir: str = None,
                          with_concept_overlap: bool = True) -> dict:
    """
    跨主题关联分析 (v1.6.0 第 7 工具，v1.7.2 升级)

    v1.7.2 新增:
      - 概念级共享（jieba 关键词 jaccard）
      - 共享关键词列表（不只 URL）

    输入: 多个调研主题
    输出: 共享源/共同作者/共有概念等关联信息
    """
    if archives_dir is None:
        archives_dir = str(get_archives_dir())

    archives_path = Path(archives_dir)
    if not archives_path.exists():
        return {"error": f"归档目录不存在: {archives_dir}"}

    # 收集每个主题的元数据
    subject_data = {}
    for subject in subjects:
        subject_dir = archives_path / subject
        if not subject_dir.exists():
            subject_data[subject] = {"exists": False, "files": [],
                                     "shared_sources": set(), "concepts": set()}
            continue

        files = list(subject_dir.glob('*.md')) + list(subject_dir.glob('*.json'))
        # 提取共享源（去重 URL）
        shared_sources = set()
        # v1.7.2: 提取概念集
        all_text = ""
        for f in files:
            content = f.read_text(encoding='utf-8', errors='ignore')
            all_text += content + "\n"
            import re
            urls = re.findall(r'https?://[^\s\)]+', content)
            for url in urls:
                # 标准化（去除尾部斜杠）
                shared_sources.add(url.rstrip('/'))

        # v1.7.2: 概念集（关键词）
        concepts = _extract_concepts(all_text) if with_concept_overlap else set()

        subject_data[subject] = {
            "exists": True,
            "files": [f.name for f in files],
            "file_count": len(files),
            "shared_sources": shared_sources,
            "concepts": concepts
        }

    # 计算两两共享
    correlation_matrix = {}
    for i, s1 in enumerate(subjects):
        correlation_matrix[s1] = {}
        for s2 in subjects:
            if s1 == s2:
                correlation_matrix[s1][s2] = {
                    "shared_sources": 0,
                    "shared_urls": [],
                    "shared_concepts": [],
                    "concept_jaccard": 0
                }
            else:
                # 防御：主题不存在时 shared_sources 缺失
                sources2 = subject_data.get(s2, {}).get("shared_sources", set())
                sources1 = subject_data.get(s1, {}).get("shared_sources", set())
                shared_url = sources1 & sources2

                # v1.7.2: 概念级共享
                concepts1 = subject_data.get(s1, {}).get("concepts", set())
                concepts2 = subject_data.get(s2, {}).get("concepts", set())
                shared_concepts = concepts1 & concepts2
                concept_jaccard = (
                    len(shared_concepts) / len(concepts1 | concepts2)
                    if (concepts1 | concepts2) else 0
                )

                correlation_matrix[s1][s2] = {
                    "shared_sources": len(shared_url),
                    "shared_urls": list(shared_url)[:5],
                    "shared_concepts": list(shared_concepts)[:10],  # 最多 10 个
                    "concept_jaccard": round(concept_jaccard, 3)
                }

    return {
        "subjects": subjects,
        "subject_data": {k: {"file_count": v.get("file_count", 0),
                              "files": v.get("files", []),
                              "concept_count": len(v.get("concepts", set()))}
                          for k, v in subject_data.items()},
        "correlation_matrix": correlation_matrix,
        "insights": _generate_cross_subject_insights_v172(subject_data, correlation_matrix)
    }


def _generate_cross_subject_insights_v172(subject_data: dict, correlation_matrix: dict) -> List[str]:
    """v1.7.2 跨主题洞察生成（含概念级）"""
    insights = []

    # 找共享源最多的主题对
    pairs = []
    subjects = list(subject_data.keys())
    for i, s1 in enumerate(subjects):
        for s2 in subjects[i+1:]:
            shared = correlation_matrix[s1][s2]["shared_sources"]
            if shared > 0:
                pairs.append((s1, s2, shared))

    pairs.sort(key=lambda x: -x[2])

    if pairs:
        top = pairs[0]
        insights.append(
            f"主题 '{top[0]}' 与 '{top[1]}' 共享 {top[2]} 个来源，可能存在强关联"
        )

    # v1.7.2: 找概念级 jaccard 最高的（即使没有共享源）
    concept_pairs = []
    for i, s1 in enumerate(subjects):
        for s2 in subjects[i+1:]:
            jaccard = correlation_matrix[s1][s2].get("concept_jaccard", 0)
            if jaccard > 0.05:  # >5% 概念重叠
                shared_kw = correlation_matrix[s1][s2].get("shared_concepts", [])
                concept_pairs.append((s1, s2, jaccard, shared_kw))

    concept_pairs.sort(key=lambda x: -x[2])

    if concept_pairs:
        top = concept_pairs[0]
        kw_sample = top[3][:5] if top[3] else []
        insights.append(
            f"概念级关联: '{top[0]}' 与 '{top[1]}' 共享概念 jaccard={top[2]:.2f}（{kw_sample}）"
        )

    # 找文件数最少的主题（可能数据不完整）
    file_counts = [(s, subject_data[s]["file_count"]) for s in subjects if subject_data[s]["exists"]]
    if file_counts:
        min_subject = min(file_counts, key=lambda x: x[1])
        if min_subject[1] < 3:
            insights.append(
                f"主题 '{min_subject[0]}' 仅有 {min_subject[1]} 个文件，建议补充调研"
            )

    return insights
