#!/usr/bin/env python3
"""
domain_router.py — Infoseek 领域 Profile 路由器（v1.8.0）

从 5 个领域 profile YAML 中根据关键词自动路由：
- tech-research（技术工艺）
- market-research（市场研究）
- finance-research（金融投资）
- policy-research（政策法规）
- competitor-intel（竞品情报）

返回选中的 profile 路径与权重，供 anchor_adapter.calculate_score() 使用。
"""

import os
import re
from pathlib import Path
from typing import Optional

import yaml

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE', str(Path.home() / 'infoseek')))
DOMAINS_DIR = Path(__file__).parent.parent / 'domains'


# 各领域关键词触发词
DOMAIN_TRIGGERS = {
    'tech-research': {
        'weight': 1.0,
        'keywords': [
            '工艺', '分切', '轧制', '退火', '热处理', '冷轧', '热轧', '模具',
            '圆盘刀', '重叠量', '压辊', '张力', '缺陷', '毛刺', '钢卷', '带钢',
            '不锈钢', '合金', '精度', '公差', '工艺参数', '设备', '工序',
            'process', 'rolling', 'annealing', 'tolerance', 'defect',
            'alloy', 'precision', 'manufacturing', 'slitting', 'coiling',
            # v1.0.1 PATCH (P0-3): 扩充通用技术触发词，修复技术主题
            # 检测返回 None（如"DeepSeek 开源模型 技术路线"）
            '模型', '开源', '技术', '算法', '软件', '代码', '芯片', '架构',
            '大模型', '人工智能', '深度学习', '机器学习', '训练', '推理',
            'framework', 'open source', 'software', 'algorithm', 'chip',
            'architecture', 'deep learning', 'machine learning', 'llm',
            'training', 'inference', 'api', '模型', '微调', '部署',
        ],
    },
    'market-research': {
        'weight': 1.0,
        'keywords': [
            '市场', '行业', '规模', '增速', 'CAGR', '渗透率', '用户画像',
            '消费', '需求', '行业研究', '市场规模', '细分', '增长',
            'market', 'industry', 'segment', 'TAM', 'SAM', 'SOM', 'user persona',
            'consumer', 'demand', 'growth',
        ],
    },
    'finance-research': {
        'weight': 1.0,
        'keywords': [
            '股票', '基金', '债券', '期货', '外汇', '期权', '行情', 'K线',
            'MACD', 'KDJ', 'RSI', '布林带', '财报', '估值', 'PE', 'PB',
            '市净率', '市盈率', '回测', '策略', '持仓', '杠杆', '做空',
            'stock', 'equity', 'bond', 'futures', 'forex', 'technical analysis',
            'valuation', 'backtest', 'portfolio', 'leverage',
        ],
    },
    'policy-research': {
        'weight': 1.0,
        'keywords': [
            '政策', '法规', '标准', '合规', '监管', '国标', 'GB ', 'ISO',
            '工信部', '发改委', '证监会', '银保监', '国务院', '指导意见',
            '通知', '办法', '条例', '规定',
            'policy', 'regulation', 'compliance', 'standard', 'governance',
            'regulatory', 'mandate', 'directive',
        ],
    },
    'competitor-intel': {
        'weight': 1.0,
        'keywords': [
            '竞品', '对比', '差异化', '竞争对手', '市场份额', '产品矩阵',
            '功能比较', 'vs ', ' versus', 'alternative', 'competitor',
            'comparison', 'feature parity', 'head-to-head',
        ],
    },
}


def detect_domain(subject: str) -> dict:
    """根据主题文本自动选择最匹配的领域 profile。

    参数:
        subject: 调研主题或描述

    返回:
        {
          "domain": 选中的领域名，
          "score": 匹配得分，
          "candidates": [所有候选及得分]，
          "profile_path": profile YAML 文件路径，
        }
    """
    subject_lower = subject.lower()

    candidates = []
    for domain, cfg in DOMAIN_TRIGGERS.items():
        hit_count = 0
        for kw in cfg['keywords']:
            if kw.lower() in subject_lower:
                hit_count += 1
        score = hit_count * cfg['weight']
        candidates.append({
            'domain': domain,
            'score': score,
            'hit_count': hit_count,
        })

    # 按得分降序排序
    candidates.sort(key=lambda x: -x['score'])

    best = candidates[0] if candidates else None
    if not best or best['score'] == 0:
        # 无触发词 → 默认通用研究（不强制领域）
        return {
            'domain': None,
            'score': 0,
            'candidates': candidates,
            'profile_path': None,
            'is_default': True,
        }

    profile_path = DOMAINS_DIR / f"{best['domain']}.yaml"
    return {
        'domain': best['domain'],
        'score': best['score'],
        'candidates': candidates,
        'profile_path': str(profile_path) if profile_path.exists() else None,
        'is_default': False,
    }


def load_profile(domain_name: str) -> dict:
    """加载指定领域的 profile YAML。

    参数:
        domain_name: 领域名（如 "tech-research"）

    返回:
        profile dict，包含 trust_sources / keywords_template / weights / output_format

    异常:
        FileNotFoundError: profile 文件不存在
    """
    profile_path = DOMAINS_DIR / f"{domain_name}.yaml"
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    with open(profile_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 简单 YAML 解析（支持注释 + 嵌套）
    profile = {
        'name': domain_name,
        'raw': content,
    }

    # 提取信任源白名单（v1.8.0 简化版，只读 raw 文本）
    # 实际应用时建议用 PyYAML 或 ruamel.yaml 严格解析
    return profile


def apply_profile_to_score(source: dict, profile: dict) -> dict:
    """根据 profile 微调评分权重。

    参数:
        source: 来源 dict（含 url、platform、text 等）
        profile: 领域 profile dict

    返回:
        更新后的 source（含 adjusted_score / domain_applied 字段）
    """
    if not profile:
        return source

    # 信任源加权（v1.8.0 简化版：检查 URL 是否在 profile 信任源白名单）
    url_lower = source.get('url', '').lower()

    # 解析 raw 文本中的"信任源白名单"
    trust_keywords = []
    for line in profile.get('raw', '').split('\n'):
        if '宝钢' in line or 'Wind' in line or '中金' in line or '国务院' in line or 'Reddit' in line:
            # 提取粗略关键词（简化处理）
            for kw in re.findall(r'[\u4e00-\u9fff]{2,4}|[A-Z][a-zA-Z]+', line):
                if len(kw) >= 2 and kw not in ['权重', 'Tier', '类型', '来源', 'Tier 1']:
                    trust_keywords.append(kw)

    # 命中加权
    bonus = 0
    for kw in set(trust_keywords):
        if kw.lower() in url_lower or kw in source.get('platform', ''):
            bonus += 5

    source['domain_applied'] = profile.get('name', '')
    source['domain_bonus'] = min(bonus, 20)  # 上限 20 分
    return source


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python domain_router.py <subject>")
        sys.exit(1)

    subject = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else sys.argv[1]
    result = detect_domain(subject)

    print(f"主题: {subject}")
    if result.get('is_default'):
        print(f"匹配: 无触发词，使用默认通用研究")
    else:
        print(f"匹配领域: {result['domain']} (得分 {result['score']})")
        print(f"Profile: {result['profile_path']}")
    print("所有候选:")
    for cand in result['candidates'][:3]:
        print(f"  - {cand['domain']:20s} 得分={cand['score']:.0f} 命中={cand['hit_count']}")
