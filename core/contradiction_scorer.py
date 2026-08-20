#!/usr/bin/env python3
"""
core/contradiction_scorer.py — Infoseek 语义矛盾评分（v2.4.0 MINOR 新增）

V2.3.0/v2.3.1 的冲突检测仅判 "同实体 ≥2 异源" 就报，severity 写死 medium，
对真实「自相矛盾 / 客观一致 / 并非冲突」三者区分力差。

v2.4.0 引入轻量语义矛盾评分（无需 LLM 也可用，可选 LLM 增强）：

输入：claim_a + claim_b（同实体两条声明，结构见 conflict_v3._extract_fact_claims）
输出：{'score': 0-100, 'severity': 'high|medium|low|none',
        'reasons': [..], 'neg_hits': [..], 'shared_slots': [..]}

评分三段：
  ① 共享事实槽（predicate 集合 Jaccard）        0-30 分
  ② 否定/反义命中（精确词 + 同义对）              0-50 分
  ③ 极性相反（attribute 同槽 + polarity 不同）     ×1.4 乘数（出现在 ② 之后）

总分映射 severity：
  ≥60  high     → 明确矛盾（核心事实相反）
  30-59 medium  → 疑似矛盾（需复核）
  10-29 low     → 表述分歧（人称/视角差异）
  <10  none     → 实为一致

可选 LLM：`score_with_llm(claim_a, claim_b, llm_router)` 拼接 prompt 调 LLM 拿 0-100 分。
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

CORE_DIR = Path(__file__).parent

# ─── 否定词（中英） ─────────────────────────────────────────
NEG_WORDS_ZH = {
    '不', '否', '非', '无', '未', '没', '别', '莫', '毋', '弗',
    '不开放', '不开源', '非开源', '不发布',
}
NEG_WORDS_EN = {
    'not', 'no', 'never', 'without', 'none', 'fail', "n't",
    'closed', 'proprietary',          # 与 open 对立
    'decline', 'reject', 'deny',
}

# ─── 反义对（同义簇） ─────────────────────────────────────────
ANTONYM_PAIRS = [
    ('开源', '闭源'), ('open source', 'closed source'),
    ('上涨', '下跌'), ('increase', 'decrease'),
    ('盈利', '亏损'), ('profit', 'loss'),
    ('增长', '下滑'), ('growth', 'decline'),
    ('同意', '拒绝'), ('agree', 'reject'),
    ('官方', '传闻'), ('official', 'rumor'),
    ('支持', '反对'), ('support', 'oppose'),
    ('即将', '搁置'), ('imminent', 'shelved'),
    ('公开', '保密'), ('public', 'classified'),
]

# ─── 谓词模板（事实槽提取用：把句子拆 (predicate, value)） ──
PRED_TEMPLATES_ZH = [
    r'(.{1,8}?)是(开源|闭源|公开|保密|盈利|亏损)',
    r'(.{1,12}?)(宣布|确认|否认|发布|停止)(.{1,20})',
    r'(.{1,12}?)(增长|下滑|上涨|下跌)\s*(\d+\.?\d*\s*%)',
]
PRED_TEMPLATES_EN = [
    r'(\w+\s+\w+)\s+is\s+(open|closed|public|profitable|loss)',
    r'(\w+\s+\w+)\s+(announce|confirm|deny|release|stop)\s+(.{1,40})',
]


# ═══════════════════════════════════════════════════════════
# 事实槽提取
# ═══════════════════════════════════════════════════════════

def _extract_slots(text: str) -> Set[str]:
    """从文本抽取事实槽（标准化字符串集合）。

    简化策略：用谓词模板抓 (predicate, value) 短语；未命中的退化为 n-gram 关键词集合。
    v2.4.1 PATCH (DEF-D): 模板/正则抛错时降级返回空集，reasons 留痕。
    """
    slots = set()
    t = text.strip()
    if not t:
        return slots
    try:
        # 模板命中
        for pat in PRED_TEMPLATES_ZH + PRED_TEMPLATES_EN:
            for m in re.finditer(pat, t):
                slot = ' '.join(g for g in m.groups() if g).strip().lower()
                if 4 <= len(slot) <= 60:
                    slots.add(slot)
        # 兜底：2-gram 关键词
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', t.lower())
        for i in range(len(tokens) - 1):
            bigram = f'{tokens[i]} {tokens[i+1]}'
            if len(bigram) >= 4 and not _is_stop_bigram(bigram):
                slots.add(bigram)
    except Exception:
        # v2.4.1 PATCH: 容错 — re.finditer 抛错时返回空集
        return set()
    return slots


def _is_stop_bigram(bg: str) -> bool:
    """过滤无信息 bigram（虚词 + 助词）"""
    stops = {'的 是', '了 的', '和 的', 'in the', 'of the', 'to the', 'is a'}
    return bg in stops


# ═══════════════════════════════════════════════════════════
# 否定 / 反义检测
# ═══════════════════════════════════════════════════════════

def _detect_negation(text_a: str, text_b: str) -> Tuple[int, List[str]]:
    """返回 (neg_score 0-50, reason_list)"""
    score = 0
    reasons = []
    a_low, b_low = text_a.lower(), text_b.lower()

    # 1. 否定词
    hit_a = [w for w in NEG_WORDS_ZH | NEG_WORDS_EN if w in a_low]
    hit_b = [w for w in NEG_WORDS_ZH | NEG_WORDS_EN if w in b_low]
    # 否定词不对称（一正一反） → 20 分
    if (hit_a and not hit_b) or (hit_b and not hit_a):
        score += 20
        reasons.append(f'否定词不对称：a={hit_a[:3]} b={hit_b[:3]}')
    elif hit_a and hit_b:
        score += 5  # 双否定抵消轻扣

    # 2. 反义对（一正一反） → 40 分（最强信号）
    for pos, neg in ANTONYM_PAIRS:
        pa, pb = pos.lower(), neg.lower()
        in_a_pos = pa in a_low, pa in b_low
        in_a_neg = pb in a_low, pb in b_low
        if (in_a_pos[0] and in_a_neg[1]) or (in_a_neg[0] and in_a_pos[1]):
            score += 40
            reasons.append(f'反义对命中：{pos}↔{neg}')
    return min(score, 50), reasons


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def score_contradiction(claim_a: Dict, claim_b: Dict) -> Dict:
    """计算两条声明的语义矛盾分

    参数: claim_a / claim_b 至少含 'text' 字段（与 conflict_v3 同结构）
    返回: {'score': 0-100, 'severity': 'high|medium|low|none',
           'reasons': [..], 'neg_hits': [..], 'shared_slots': [..],
           'slot_score': 0-30, 'neg_score': 0-50}
    """
    text_a = claim_a.get('text', '') if isinstance(claim_a, dict) else str(claim_a)
    text_b = claim_b.get('text', '') if isinstance(claim_b, dict) else str(claim_b)

    # ① 共享事实槽
    slots_a = _extract_slots(text_a)
    slots_b = _extract_slots(text_b)
    shared = slots_a & slots_b
    union = slots_a | slots_b
    jaccard = len(shared) / max(len(union), 1)
    slot_score = int(jaccard * 30)

    # ② 否定/反义
    neg_score, reasons = _detect_negation(text_a, text_b)

    total = slot_score + neg_score
    # ③ 极性放大：槽共享≥2 且 neg>0 时 ×1.4（命中"同一主题不同观点"经典模式）
    if len(shared) >= 2 and neg_score > 0:
        total = min(int(total * 1.4), 100)
        reasons.append(f'极性放大：共享槽={len(shared)} + 反义命中')

    if total >= 60:
        sev = 'high'
    elif total >= 30:
        sev = 'medium'
    elif total >= 10:
        sev = 'low'
    else:
        sev = 'none'

    return {
        'score': total,
        'severity': sev,
        'reasons': reasons,
        'neg_hits': reasons,                  # 兼容旧字段名
        'shared_slots': sorted(shared)[:5],   # 截断避免日志爆炸
        'slot_score': slot_score,
        'neg_score': neg_score,
    }


# ═══════════════════════════════════════════════════════════
# 可选 LLM 增强
# ═══════════════════════════════════════════════════════════

def score_with_llm(claim_a: Dict, claim_b: Dict, llm_router=None) -> Dict:
    """调用 LLM 拿矛盾评分。llm_router=None 时降级到 score_contradiction

    返回格式同 score_contradiction，并多 'llm_used': bool / 'llm_raw': str
    """
    if llm_router is None:
        # 沙箱无 LLM：直接退化为本地版本
        res = score_contradiction(claim_a, claim_b)
        res['llm_used'] = False
        return res

    text_a = claim_a.get('text', '') if isinstance(claim_a, dict) else str(claim_a)
    text_b = claim_b.get('text', '') if isinstance(claim_b, dict) else str(claim_b)
    prompt = (
        f"判断下列两条声明是否表达相反事实（输出 0-100 分，0=完全一致，100=完全矛盾）。\n"
        f"声明 A：{text_a[:300]}\n"
        f"声明 B：{text_b[:300]}\n"
        f"仅输出一个整数。"
    )
    try:
        raw = llm_router(prompt)
        m = re.search(r'\d{1,3}', raw or '')
        score = int(m.group(0)) if m else 0
        score = max(0, min(score, 100))
        local = score_contradiction(claim_a, claim_b)
        # 取 LLM 与本地的较大值（任一一方强烈判矛盾都尊重）
        final = max(score, local['score'])
        return {
            'score': final,
            'severity': 'high' if final >= 75 else 'medium' if final >= 45
                       else 'low' if final >= 15 else 'none',
            'reasons': local['reasons'] + [f'LLM={score}'],
            'neg_hits': local['reasons'],
            'shared_slots': local['shared_slots'],
            'llm_used': True,
            'llm_raw': raw.strip()[:200],
        }
    except Exception as e:
        res = score_contradiction(claim_a, claim_b)
        res['llm_used'] = False
        res['llm_error'] = str(e)
        return res


# v2.7.2 PATCH: score_contradiction 异步版本；v2.7.3 PATCH: 内部逻辑清理
async def score_contradiction_async(claim_a: Dict, claim_b: Dict, llm_router=None) -> Dict:
    """v2.7.2 新增；v2.7.3 PATCH 清理：score_contradiction 异步版

    当前实现：asyncio.to_thread 包装同步 score_contradiction（CPU 密集）
    v1.0.1 PATCH / G6: 补齐 LLM 分支 —— llm_router 提供时走 score_with_llm_async
    """
    import asyncio
    if llm_router:
        return await score_with_llm_async(claim_a, claim_b, llm_router=llm_router)
    return await asyncio.to_thread(score_contradiction, claim_a, claim_b)


async def score_with_llm_async(claim_a: Dict, claim_b: Dict, llm_router=None) -> Dict:
    """v1.0.1 PATCH / G6: score_with_llm 异步版（补齐 v2.7.3 未实现接口）。

    llm_router 兼容两种签名：
    - 同步可调用 `llm_router(prompt) -> str`（旧接口，to_thread 包装）
    - 异步可调用 `await llm_router(prompt) -> str`（新接口，直接 await）

    返回格式同 score_with_llm（含 llm_used/llm_raw）。
    """
    import asyncio
    import inspect

    if llm_router is None:
        res = await asyncio.to_thread(score_contradiction, claim_a, claim_b)
        res['llm_used'] = False
        return res

    text_a = claim_a.get('text', '') if isinstance(claim_a, dict) else str(claim_a)
    text_b = claim_b.get('text', '') if isinstance(claim_b, dict) else str(claim_b)
    prompt = (
        f"判断下列两条声明是否表达相反事实（输出 0-100 分，0=完全一致，100=完全矛盾）。\n"
        f"声明 A：{text_a[:300]}\n"
        f"声明 B：{text_b[:300]}\n"
        f"仅输出一个整数。"
    )
    try:
        if inspect.iscoroutinefunction(llm_router) or inspect.iscoroutinefunction(
                getattr(llm_router, '__call__', None)):
            raw = await llm_router(prompt)
        else:
            raw = await asyncio.to_thread(llm_router, prompt)
        m = re.search(r'\d{1,3}', raw or '')
        score = int(m.group(0)) if m else 0
        score = max(0, min(score, 100))
        local = await asyncio.to_thread(score_contradiction, claim_a, claim_b)
        final = max(score, local['score'])
        return {
            'score': final,
            'severity': 'high' if final >= 75 else 'medium' if final >= 45
                       else 'low' if final >= 15 else 'none',
            'reasons': local['reasons'] + [f'LLM={score}'],
            'neg_hits': local['reasons'],
            'shared_slots': local['shared_slots'],
            'llm_used': True,
            'llm_raw': raw.strip()[:200],
        }
    except Exception as e:
        res = await asyncio.to_thread(score_contradiction, claim_a, claim_b)
        res['llm_used'] = False
        res['llm_error'] = str(e)
        return res


async def score_contradictions_batch_async(claim_pairs: List[tuple]) -> List[Dict]:
    """v2.7.2 新增：批量异步评分（asyncio.gather 并发）"""
    import asyncio
    return await asyncio.gather(
        *[score_contradiction_async(a, b) for a, b in claim_pairs]
    )


# ═══════════════════════════════════════════════════════════
# CLI: python core/contradiction_scorer.py "<text_a>" "<text_b>"
# ═══════════════════════════════════════════════════════════

def main():
    import sys
    import json as _json
    if len(sys.argv) < 3:
        print("usage: contradiction_scorer.py <text_a> <text_b>")
        sys.exit(1)
    res = score_contradiction({'text': sys.argv[1]}, {'text': sys.argv[2]})
    print(_json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
