#!/usr/bin/env python3
"""
core/anchor_score_v2.py — Infoseek v2 评分（v2.0.2 重构）

v2.0.2 目标：脱离 v1 shim，纯函数式评分

设计要点：
1. 纯函数（无副作用，不修改 source dict）
2. 模块化（trust_bonus + jaccard + domain_bonus 各算各的）
3. 可测试（每个评分维度可独立测试）
4. 向后兼容（v1 shim 仍可用，但内部调 v2）

API：
- compute_score_v2(source, subject) → 评分 dict
- compute_trust_bonus_v2(url, platform, domain) → 0-30
- compute_jaccard_v2(text, subject) → 0-100
- compute_domain_bonus_v2(source, profile) → 0-20
- compute_final_score_v2(...) → 聚合
"""

import sys
import re
import datetime
from pathlib import Path
from typing import Optional, Dict, List

CORE_DIR = Path(__file__).parent
sys.path.insert(0, str(CORE_DIR))


def compute_trust_bonus_v2(url: str, platform: str = '',
                          domain: str = 'general') -> int:
    """v2.0.2 信任源加权（核心/trust_sources.compute_trust_bonus 包装）"""
    try:
        from trust_sources import compute_trust_bonus
        return compute_trust_bonus(url or '', domain, platform)
    except Exception:
        return 0


def compute_jaccard_v2(text: str, subject: str) -> int:
    """v2.0.2 关键词 Jaccard 相似度（v1.7.4 Jaccard 包装）"""
    if not text or not subject:
        return 0
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from anchor_adapter import _jaccard_similarity
        return _jaccard_similarity(text, subject)
    except Exception:
        return 0


def compute_decay_factor_v2(days_since_published: int) -> float:
    """v2.0.2 时间衰减因子（与 v1.5.0 一致）"""
    if days_since_published < 30:
        return 1.0
    elif days_since_published < 90:
        return 0.9
    elif days_since_published < 180:
        return 0.7
    elif days_since_published < 365:
        return 0.5
    else:
        return 0.3


def compute_cross_platform_v2(platforms: int) -> int:
    """v2.0.2 跨平台分布度（v1.6.0 第 6 维）"""
    if platforms <= 1:
        return 0
    return min(100, (platforms - 1) * 25)


def compute_domain_bonus_v2(source: dict, profile: Optional[dict] = None) -> int:
    """v2.0.2 领域加权（trust_sources + domain 自动检测）"""
    if not profile:
        subject = source.get('subject', '') or source.get('_subject', '')
        if not subject:
            return 0
        try:
            from domain_router import detect_domain
            routing = detect_domain(subject)
            if routing.get('profile_path'):
                profile = {
                    'name': routing['domain'],
                    'raw': open(routing['profile_path'], encoding='utf-8').read(),
                }
        except Exception:
            return 0

    if not profile:
        return 0

    raw = profile.get('raw', '')
    url_lower = (source.get('url') or '').lower()
    platform_lower = (source.get('platform') or '').lower()
    bonus = 0

    # 提取信任源关键词（v1.8.1 anchor_adapter.py 同样逻辑）
    trust_keywords = set()
    for line in raw.split('\n'):
        for kw in re.findall(r'[\u4e00-\u9fff]{2,4}', line):
            if kw not in {'权重', 'Tier', '类型', '来源', 'Tier 1', '适用场景'}:
                trust_keywords.add(kw)
        for kw in re.findall(r'\b[A-Z][a-zA-Z]{2,}', line):
            trust_keywords.add(kw)

    for kw in trust_keywords:
        if kw.lower() in url_lower:
            bonus += 4
        if kw in platform_lower:
            bonus += 3

    return min(bonus, 20)


def compute_base_score_v2(source: dict, subject: str = '') -> float:
    """v2.0.2 基础评分（v1.5.0 五维）

    计算 4 维：
    - interaction（互动深度）
    - topic_match（主题一致性）
    - credibility（来源可信度）
    - llm_readability（LLM 上下文可读性）
    """
    interaction = source.get('interaction', source.get('score', 50))
    topic_match = source.get('topic_match', 50)
    credibility = source.get('credibility', 50)
    llm_readability = source.get('llm_readability', 50)

    base = (
        interaction * 0.20 +
        topic_match * 0.30 +
        credibility * 0.40 +
        llm_readability * 0.10
    )
    return round(base, 2)


def compute_final_score_v2(source: dict, subject: str = '',
                            with_llm_readability: bool = True,
                            with_cross_platform: bool = False,
                            platforms: int = 1,
                            with_semantic: bool = False,
                            semantic_text: str = None,
                            days_since_published: int = 0,
                            with_domain: bool = False,
                            domain_profile: dict = None) -> Dict:
    """v2.0.2 终极评分入口（纯函数版）

    与 v1 calculate_score() 行为一致，但：
    - 不修改 source dict
    - 返回 dict 不含 'version'（调用方负责）
    - 各评分维度独立计算
    """
    # 1) 基础评分
    base_score = compute_base_score_v2(source, subject)

    # 2) 白名单复活（简化：v1 逻辑）
    final = base_score
    whitelist_triggered = False
    top3_triggered = False
    if base_score >= 90:
        whitelist_triggered = True
        final = max(final, 70)

    # 3) 时间衰减
    decay = compute_decay_factor_v2(days_since_published)
    after_decay = round(final * decay, 1)

    # 4) 跨平台（v1.6.0 第 6 维）
    cross_platform_score = 0
    if with_cross_platform:
        cross_platform_score = compute_cross_platform_v2(platforms)
        # 第 6 维占 5%
        after_decay = round(after_decay * 0.95 + cross_platform_score * 0.05, 1)

    # 5) Jaccard 语义（v1.7.0 第 8 维）
    semantic_score = 0
    if with_semantic:
        text = semantic_text or source.get('text', '') or source.get('snippet', '')
        semantic_score = compute_jaccard_v2(text, subject)
        # 第 8 维占 5%
        after_decay = round(after_decay * 0.95 + semantic_score * 0.05, 1)

    # 6) Trust 加权（v2.0.0 新增）
    trust_bonus = compute_trust_bonus_v2(
        source.get('url', ''),
        source.get('platform', ''),
        source.get('domain', 'general'),
    )
    final_score = min(after_decay + trust_bonus, 100)

    # 7) Domain 加权（v1.9.0）
    domain_bonus = 0
    if with_domain:
        domain_bonus = compute_domain_bonus_v2(source, domain_profile)
        final_score = min(final_score + domain_bonus, 100)

    # 8) 分类
    if final_score >= 70:
        classification = '🟢核心'
    elif final_score >= 40:
        classification = '🟡潜力'
    else:
        classification = '❌噪声'

    return {
        'base_score': base_score,
        'after_whitelist': final,
        'after_decay': after_decay,
        'after_whitelist_final': final_score,  # alias for after_whitelist
        'cross_platform_score': cross_platform_score,
        'semantic_similarity': semantic_score,
        'trust_bonus': trust_bonus,
        'domain_bonus': domain_bonus,
        'classification': classification,
        'whitelist_triggered': whitelist_triggered,
        'top3_triggered': top3_triggered,
        'tier': compute_trust_bonus_v2(source.get('url', ''), source.get('platform', ''), 'general') // 10,
        'version': '2.0.2',
    }


# ═══════════════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════════════

def main():
    """CLI: python -m core.anchor_score_v2 test"""
    if len(sys.argv) < 2:
        print("Usage: python -m core.anchor_score_v2 test")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'test':
        source = {
            'url': 'https://baosteel.com/article',
            'platform': '宝钢集团',
            'interaction': 80,
            'topic_match': 90,
            'credibility': 95,
            'llm_readability': 85,
            'snippet': '钢卷分切工艺圆盘刀重叠量 20-30%。',
        }
        result = compute_final_score_v2(
            source, subject='钢卷分切工艺',
            with_llm_readability=True,
            with_semantic=True,
            semantic_text=source['snippet'],
            with_domain=True,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import json
    main()