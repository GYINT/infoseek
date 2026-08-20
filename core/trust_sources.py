#!/usr/bin/env python3
"""
core/trust_sources.py — Infoseek 统一信任源白名单（v2.0.0 新增）

合并 v1.6.0 老白名单 + v1.8.0 5 profile 信任源到单一权威源。

按 tier 分级：
- TIER1: 官方权威源（政府/标准/学术）权重 25-30
- TIER2: 行业头部（头部企业/官方媒体）权重 15-25
- TIER3: 一般可信（行业媒体/技术博客）权重 5-15
- TIER4: 低优先（社交媒体/普通用户）权重 0-5

按领域分类（5 个 + 通用）：
- tech-research / market-research / finance-research
- policy-research / competitor-intel / general
"""

from typing import List, Dict, Optional, Tuple
import re


# ═══════════════════════════════════════════════════════════════
# Tier 1: 官方权威源
# ═══════════════════════════════════════════════════════════════

TIER1_OFFICIAL = {
    'tech-research': [
        {'name': '中国金属学会', 'patterns': ['csm.org.cn', '金属学会'], 'weight': 25},
        {'name': '国家标准化管理委员会', 'patterns': ['sac.gov.cn', 'GB '], 'weight': 30},
        {'name': 'ASTM International', 'patterns': ['astm.org'], 'weight': 25},
        {'name': 'ISO', 'patterns': ['iso.org'], 'weight': 25},
        {'name': 'Google Scholar', 'patterns': ['scholar.google.com'], 'weight': 20},
        {'name': '知网', 'patterns': ['cnki.net'], 'weight': 20},
        # v2.0.1 扩充
        {'name': 'ScienceDirect', 'patterns': ['sciencedirect.com'], 'weight': 25},
        {'name': 'IEEE Xplore', 'patterns': ['ieeexplore.ieee.org'], 'weight': 22},
        {'name': 'SpringerLink', 'patterns': ['link.springer.com'], 'weight': 22},
        {'name': 'ResearchGate', 'patterns': ['researchgate.net'], 'weight': 18},
        # v1.0.1 PATCH (P0-3): 扩充通用技术源白名单，
        # 修复 arxiv/zhihu/github 等高频技术源 tier=4 bonus=0 的问题
        {'name': 'arXiv', 'patterns': ['arxiv.org'], 'weight': 25},
        {'name': 'GitHub', 'patterns': ['github.com'], 'weight': 20},
        {'name': 'Hugging Face', 'patterns': ['huggingface.co'], 'weight': 20},
        {'name': '知乎', 'patterns': ['zhihu.com'], 'weight': 15},
        {'name': 'CSDN', 'patterns': ['csdn.net'], 'weight': 10},
        {'name': '博客园', 'patterns': ['cnblogs.com'], 'weight': 10},
        {'name': '掘金', 'patterns': ['juejin.cn'], 'weight': 10},
        {'name': 'InfoQ', 'patterns': ['infoq.com', 'infoq.cn'], 'weight': 15},
        {'name': '极客时间', 'patterns': ['geekbang.org', 'time.geekbang'], 'weight': 12},
        {'name': '量子位', 'patterns': ['qbitai.com'], 'weight': 15},
        {'name': '机器之心', 'patterns': ['jiqizhixin.com'], 'weight': 15},
        {'name': '36氪', 'patterns': ['36kr.com'], 'weight': 15},
        {'name': '阿里云开发者', 'patterns': ['developer.aliyun.com'], 'weight': 15},
        {'name': '腾讯云开发者', 'patterns': ['cloud.tencent.com'], 'weight': 15},
        {'name': '百度开发者', 'patterns': ['developer.baidu.com'], 'weight': 12},
    ],
    'market-research': [
        {'name': '国家统计局', 'patterns': ['stats.gov.cn'], 'weight': 30},
        {'name': 'Statista', 'patterns': ['statista.com'], 'weight': 25},
        {'name': 'QuestMobile', 'patterns': ['questmobile.com.cn'], 'weight': 20},
    ],
    'finance-research': [
        {'name': '中国证监会', 'patterns': ['csrc.gov.cn'], 'weight': 30},
        {'name': '上海证券交易所', 'patterns': ['sse.com.cn'], 'weight': 30},
        {'name': '深圳证券交易所', 'patterns': ['szse.cn'], 'weight': 30},
        {'name': 'Wind', 'patterns': ['wind.com.cn'], 'weight': 25},
        {'name': 'Bloomberg', 'patterns': ['bloomberg.com'], 'weight': 25},
        {'name': '同花顺', 'patterns': ['10jqka.com.cn'], 'weight': 20},
        {'name': '东方财富', 'patterns': ['eastmoney.com'], 'weight': 20},
        # v2.0.1 扩充
        {'name': '巨潮资讯', 'patterns': ['cninfo.com.cn'], 'weight': 25},
        {'name': '证券时报', 'patterns': ['stcn.com'], 'weight': 18},
        {'name': '中国证券网', 'patterns': ['cs.com.cn', 'cnstock'], 'weight': 18},
    ],
    'policy-research': [
        {'name': '国务院', 'patterns': ['gov.cn'], 'weight': 30},
        {'name': '国家法律法规数据库', 'patterns': ['npc.gov.cn', 'flk.npc.gov.cn'], 'weight': 30},
        {'name': '工信部', 'patterns': ['miit.gov.cn'], 'weight': 25},
        {'name': '银保监会', 'patterns': ['cbirc.gov.cn'], 'weight': 25},
        {'name': '证监会', 'patterns': ['csrc.gov.cn'], 'weight': 25},
    ],
    'competitor-intel': [
        {'name': '公司官方文档', 'patterns': ['docs.', '/docs/'], 'weight': 20},
        {'name': 'The Verge', 'patterns': ['theverge.com'], 'weight': 20},
        {'name': 'TechCrunch', 'patterns': ['techcrunch.com'], 'weight': 20},
    ],
    'general': [
        {'name': 'Wikipedia', 'patterns': ['wikipedia.org'], 'weight': 15},
        {'name': 'Britannica', 'patterns': ['britannica.com'], 'weight': 20},
    ],
}


# ═══════════════════════════════════════════════════════════════
# Tier 2: 行业头部
# ═══════════════════════════════════════════════════════════════

TIER2_HEAD = {
    'tech-research': [
        {'name': '宝钢', 'patterns': ['baosteel.com'], 'weight': 20},
        {'name': '首钢', 'patterns': ['shougang.com.cn'], 'weight': 15},
        {'name': '河钢', 'patterns': ['hbisco.com'], 'weight': 15},
        {'name': 'ArcelorMittal', 'patterns': ['arcelormittal.com'], 'weight': 20},
        {'name': 'Posco', 'patterns': ['posco.co.kr'], 'weight': 20},
        {'name': 'SMS group', 'patterns': ['sms-group.com'], 'weight': 18},
        {'name': 'Andritz', 'patterns': ['andritz.com'], 'weight': 18},
        {'name': 'Mitsubishi-Hitachi', 'patterns': ['mhi.com'], 'weight': 15},
        {'name': 'AIST', 'patterns': ['aist.org'], 'weight': 15},
        {'name': 'MPIF', 'patterns': ['mpif.org'], 'weight': 15},
    ],
    'market-research': [
        {'name': '艾瑞咨询', 'patterns': ['iresearch.com.cn'], 'weight': 20},
        {'name': '易观分析', 'patterns': ['analysys.cn'], 'weight': 18},
        {'name': '36氪', 'patterns': ['36kr.com'], 'weight': 15},
        {'name': '麦肯锡', 'patterns': ['mckinsey.com'], 'weight': 22},
        {'name': 'BCG', 'patterns': ['bcg.com'], 'weight': 22},
        {'name': '贝恩', 'patterns': ['bain.com'], 'weight': 22},
        {'name': 'Deloitte', 'patterns': ['deloitte.com'], 'weight': 20},
    ],
    'finance-research': [
        {'name': '中金', 'patterns': ['cicc.com.cn'], 'weight': 22},
        {'name': '中信证券', 'patterns': ['cs.com.cn'], 'weight': 22},
        {'name': '招商证券', 'patterns': ['cmschina.com.cn'], 'weight': 20},
        {'name': '海通证券', 'patterns': ['htsec.com'], 'weight': 20},
        {'name': '华泰证券', 'patterns': ['htsc.com.cn'], 'weight': 20},
        {'name': 'Goldman Sachs', 'patterns': ['goldmansachs.com'], 'weight': 22},
        {'name': 'Morgan Stanley', 'patterns': ['morganstanley.com'], 'weight': 22},
        {'name': '公司公告/年报', 'patterns': ['公告', 'annual report', '年报'], 'weight': 25},
    ],
    'policy-research': [
        {'name': '人民日报', 'patterns': ['peopleapp.com', 'rmrb'], 'weight': 20},
        {'name': '经济日报', 'patterns': ['jjw', 'ce.cn'], 'weight': 18},
        {'name': '学习时报', 'patterns': ['study times'], 'weight': 18},
        {'name': '中国法学会', 'patterns': ['chinalawsociety.com'], 'weight': 15},
    ],
    'competitor-intel': [
        {'name': 'Reddit', 'patterns': ['reddit.com'], 'weight': 18},
        {'name': '即刻', 'patterns': ['okjike.com'], 'weight': 15},
        {'name': '知乎', 'patterns': ['zhihu.com'], 'weight': 15},
        {'name': 'V2EX', 'patterns': ['v2ex.com'], 'weight': 15},
        {'name': '极客公园', 'patterns': ['geekpark.net'], 'weight': 18},
        {'name': '爱范儿', 'patterns': ['ifeng.com'], 'weight': 15},
    ],
}


# ═══════════════════════════════════════════════════════════════
# Tier 3: 一般可信
# ═══════════════════════════════════════════════════════════════

TIER3_GENERAL = {
    'general': [
        {'name': '虎嗅', 'patterns': ['huxiu.com'], 'weight': 10},
        {'name': '钛媒体', 'patterns': ['tmtpost.com'], 'weight': 10},
        {'name': '第一财经', 'patterns': ['yicai.com'], 'weight': 12},
        {'name': '财新', 'patterns': ['caixin.com'], 'weight': 15},
    ],
}


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════

def get_all_sources(domain: str = 'general') -> List[Dict]:
    """获取某领域的所有信任源（合并 tier1+tier2+tier3）"""
    sources = []
    for tier_dict in [TIER1_OFFICIAL, TIER2_HEAD, TIER3_GENERAL]:
        domain_sources = tier_dict.get(domain, []) + tier_dict.get('general', [])
        sources.extend(domain_sources)
    return sources


def compute_trust_bonus(url: str, domain: str = 'general', platform: str = '') -> int:
    """计算单个源在指定领域的信任加权

    参数:
        url: 源 URL
        domain: 领域（tech-research / market-research / 等）
        platform: 平台名

    返回:
        0-30 分加分

    v2.5.1 PATCH: 改用 build_pattern_index 预构建 hash + 遍历累加（O(patterns) 但无重复构建）。
    原实现每次都调 get_all_sources(domain)（重复构造 tier 列表 + pattern 列表）。
    """
    url_lower = url.lower() if url else ''
    platform_lower = platform.lower() if platform else ''
    bonus = 0

    # v2.5.1: 用 hash 索引遍历，pattern 仍 O(patterns) 但避免重复构建 tier 列表
    idx = build_pattern_index(domain)
    for pat, (_tier, weight) in idx.items():
        if pat in url_lower or pat in platform_lower:
            bonus += weight

    return min(bonus, 30)


def get_tier_level(url: str, domain: str = 'general') -> int:
    """返回源的 tier 等级（1=最高，4=最低）"""
    url_lower = url.lower() if url else ''

    # Tier 1
    for source in get_all_sources(domain):
        if source in TIER1_OFFICIAL.get(domain, []) + TIER1_OFFICIAL.get('general', []):
            for pattern in source['patterns']:
                if pattern.lower() in url_lower:
                    return 1
    # Tier 2
    for source in get_all_sources(domain):
        if source in TIER2_HEAD.get(domain, []):
            for pattern in source['patterns']:
                if pattern.lower() in url_lower:
                    return 2
    # Tier 3
    for source in get_all_sources(domain):
        if source in TIER3_GENERAL.get('general', []):
            for pattern in source['patterns']:
                if pattern.lower() in url_lower:
                    return 3
    return 4


def list_sources_by_domain(domain: str = 'general') -> dict:
    """列出某领域的所有源（按 tier 分组）"""
    return {
        'tier1': TIER1_OFFICIAL.get(domain, []) + TIER1_OFFICIAL.get('general', []),
        'tier2': TIER2_HEAD.get(domain, []),
        'tier3': TIER3_GENERAL.get('general', []),
    }


# v2.5.0 MINOR: 批量 hash 索引 + O(1) 查询
# v2.4.3 之前每源独立 compute_trust_bonus + get_tier_level 都要全量遍历 patterns，
# 100 源 × 2 调用 = 200 次线性扫描。预构建 hash 索引后 O(N×M) → O(N+M)
_PATTERN_HASH_CACHE = {}  # domain -> {pattern_lower -> (tier, weight)}

def build_pattern_index(domain: str = 'general') -> Dict[str, Tuple[int, int]]:
    """v2.5.0 新增：构建指定领域的 pattern → (tier, weight) hash 索引

    一次构建后多次 O(1) 查询，适合批量评分场景（score_sources_batch_async）
    """
    if domain in _PATTERN_HASH_CACHE:
        return _PATTERN_HASH_CACHE[domain]
    idx: Dict[str, Tuple[int, int]] = {}
    for tier_name, tier_dict in [('tier1', TIER1_OFFICIAL),
                                   ('tier2', TIER2_HEAD),
                                   ('tier3', TIER3_GENERAL)]:
        tier_num = int(tier_name[-1])
        for source in tier_dict.get(domain, []) + tier_dict.get('general', []):
            for pat in source['patterns']:
                idx.setdefault(pat.lower(), (tier_num, source['weight']))
    _PATTERN_HASH_CACHE[domain] = idx
    return idx


def query_pattern_index(url: str, domain: str = 'general') -> Tuple[int, int]:
    """v2.5.0 新增；v2.6.1 PATCH: URL 缓存层

    第一次调 query_pattern_index(url) 时遍历 patterns 找最优；
    之后同 (url, domain) 直接返回 cache 结果。
    """
    if not url:
        return (4, 0)
    url_lower = url.lower()
    cache_key = (url_lower, domain)
    if cache_key in _URL_QUERY_CACHE:
        return _URL_QUERY_CACHE[cache_key]

    idx = build_pattern_index(domain)
    best = (4, 0)
    for pat, (tier, weight) in idx.items():
        if pat in url_lower:
            if tier < best[0]:
                best = (tier, weight)
    _URL_QUERY_CACHE[cache_key] = best
    return best


def clear_pattern_index_cache():
    """v2.5.0 新增；v2.6.1 PATCH: 同时清 URL 缓存"""
    _PATTERN_HASH_CACHE.clear()
    _URL_QUERY_CACHE.clear()


# v2.6.1 PATCH: URL 查询缓存（避免重复遍历 patterns）
_URL_QUERY_CACHE = {}  # (url_lower, domain) -> (tier, weight)

# v2.6.1 PATCH: 模块加载时预热 5 个 domain 的 pattern index
for _d in ['tech-research', 'finance-research', 'market-research', 'policy-research', 'general']:
    build_pattern_index(_d)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    domain = sys.argv[1] if len(sys.argv) > 1 else 'general'
    sources = list_sources_by_domain(domain)
    for tier, items in sources.items():
        print(f"\n{tier.upper()} ({len(items)} sources):")
        for s in items:
            print(f"  - {s['name']:20s} weight={s['weight']:3d}  patterns={s['patterns']}")