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

import os
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
# GA11（v2.0.0）键控事实槽（keyed slots）—— A 层
#   旧口径：短语袋 Jaccard 相似度（长文本稀释，段落级恒 0）
#   新口径：slot_key=(主体键, 方面aspect) → value(数值/枚举/极性)，
#           仅当「同槽键且值冲突」才计矛盾；两路证据 max 融合不叠加。
# ═══════════════════════════════════════════════════════════

import json as _json
_SYN_PATH = CORE_DIR.parent / "references" / "contradiction-synonyms.json"
_syn_cache = None


def _load_synonyms() -> dict:
    """加载谓词同义归一表（mtime 感知缓存）；缺失/损坏返回空骨架。"""
    global _syn_cache
    try:
        mtime = _SYN_PATH.stat().st_mtime
    except Exception:
        return {"aspects": {}, "opposites": {}, "negation_zh": [],
                "negation_en": [], "subject_stopwords": {"zh": [], "en": []}}
    if _syn_cache is not None and _syn_cache[0] == mtime:
        return _syn_cache[1]
    try:
        data = _json.loads(_SYN_PATH.read_text(encoding="utf-8"))
        data.setdefault("aspects", {})
        data.setdefault("opposites", {})
        _syn_cache = (mtime, data)
        return data
    except Exception:
        return {"aspects": {}, "opposites": {}, "negation_zh": [],
                "negation_en": [], "subject_stopwords": {"zh": [], "en": []}}


# 数值/极性抽取
_NUM_PAT = re.compile(
    r'(\d+(?:\.\d+)?)\s*(%|％|亿|万|千|百|billion|million|thousand|bn|m|k)?',
    re.IGNORECASE)
_CN_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_UNIT_SCALE = {'%': 1.0, '％': 1.0, '百': 100, '千': 1000, '万': 10000,
               '亿': 100000000, 'hundred': 100, 'thousand': 1000, 'k': 1000,
               'million': 1_000_000, 'm': 1_000_000, 'bn': 1_000_000_000,
               'billion': 1_000_000_000}

# 中文百分比特殊写法：百分之二十 / 百分之 20
_CN_PERCENT_PAT = re.compile(r'百分之\s*([零一二两三四五六七八九十\d]+(?:\.\d+)?)')


def _cn_number(token: str) -> Optional[float]:
    """简单中文数字（含 '十' 组合，覆盖 百分之二十 类写法）。"""
    token = token.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    if token == '十':
        return 10.0
    if '十' in token:
        left, _, right = token.partition('十')
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    if len(token) == 1 and token in _CN_NUM:
        return float(_CN_NUM[token])
    return None


# 方面类型：数值型才做就近抽数；枚举/动作/名词型仅走 opposites + 否定极性
NUMERIC_ASPECTS = {
    'revenue', 'profit', 'market_share', 'valuation', 'headcount',
    'founded_year', 'funding', 'pricing', 'product_spec', 'trend_dir',
}
# 数字 → 方面的就近距离（字符）：指标名词后侧窄窗（指标常在前、数值在后），
# 趋势词前窗为主（增长 12%），二者均收紧以避免跨指标串味
_METRIC_RADIUS = 10       # 指标名词（营收/利润/市值…），中文常隔"同比大幅增长"
_TREND_RADIUS = 5         # 趋势词（增长/下滑…），只收紧邻百分比
_TREND_ASPECTS = {'trend_dir'}
# 需排除的非指标数字（年份/季度）
_QUARTER_PAT = re.compile(r'[0-9一二三四两1-4]\s*季度')


def _scan_numbers(text_low: str):
    """全文单次扫描数值，返回 [{value, percent, center, is_year}]。

    排除：季度序号（3 季度）、无单位 4 位年份（1900-2099，标记 is_year 供 founded_year 用）。
    """
    out = []
    # 中文百分比：百分之二十（span 需映射回原文位置）
    for m in _CN_PERCENT_PAT.finditer(text_low):
        v = _cn_number(m.group(1))
        if v is not None:
            out.append({"value": v, "percent": True,
                        "center": (m.start() + m.end()) / 2, "is_year": False})
    # 屏蔽季度序号区间（避免把"三季度"的 3 当指标）
    masked = list(text_low)
    for qm in _QUARTER_PAT.finditer(text_low):
        for i in range(qm.start(), qm.end()):
            masked[i] = ' '
    masked = ''.join(masked)
    for m in _NUM_PAT.finditer(masked):
        raw, unit = m.group(1), (m.group(2) or '').lower()
        try:
            val = float(raw)
        except ValueError:
            continue
        is_pct = unit in ('%', '％')
        is_year = (not unit) and len(raw) == 4 and 1900 <= int(val) <= 2099
        if unit in _UNIT_SCALE and not is_pct:
            val = val * _UNIT_SCALE[unit]
        out.append({"value": val, "percent": is_pct,
                    "center": (m.start() + m.end()) / 2, "is_year": is_year})
    return out


def _term_positions(text_low: str, terms):
    """方面命中词在原文的所有 (term, center) 位置。"""
    pos = []
    for term in terms:
        start = 0
        while True:
            idx = text_low.find(term, start)
            if idx == -1:
                break
            pos.append((term, idx + len(term) / 2))
            start = idx + 1
    return pos


def _assign_numbers(text_low: str, membership: dict):
    """数值就近归属。

    槽语义：增长率（百分比）同时承载于「指标槽」与「趋势槽」——
      「营收增长 12%」的 12% 既进 revenue 也进 trend_dir：同值不冲突，
      异值时两槽各自检出（指标绝对口径与趋势口径都不漏）。
    防串味：
      - 百分比只归「最近的一个指标词」（半径内），不跨到次近指标
        （「营收增长 35%，利润率新高」35% 归 revenue/trend，不误判 profit）；
      - 裸绝对值只归最近指标，不进趋势槽；
      - 年份只归 founded_year。
    返回 {aspect: [{"kind":"number","value","percent","term"}, ...]}。
    """
    numbers = _scan_numbers(text_low)
    asp_pos = {}
    for asp in NUMERIC_ASPECTS:
        if asp in membership:
            asp_pos[asp] = _term_positions(text_low, membership[asp])

    def nearest_side(asp, num_center, num_start, num_end, radius, side=None):
        """找方面词相对数值的最近命中。

        side='left'：仅取数值左侧（词在数值前，财务句式「指标 趋势 数值」）；
        side='right'：仅取右侧紧邻（「数值 元/万」后置指标的少见句式）；
        side=None：两侧均可。返回 (distance, term, side)。
        """
        best = None
        for term, center in asp_pos.get(asp, []):
            on_left = center <= num_center
            d = abs(num_center - center)
            if d > radius:
                continue
            if side == 'left' and not on_left:
                continue
            if side == 'right' and on_left:
                continue
            cand = (d, term, 'left' if on_left else 'right')
            if best is None or cand[0] < best[0]:
                best = cand
        return best

    # 数值区间（用于左/右判定，避免用中心点跨标点误归属）
    def _num_span(num):
        # 由 center 反推不精确，改为在调用点用原始 match；这里近似用 ±2
        return num["center"] - 2, num["center"] + 2

    assigned: dict = {}
    for num in numbers:
        ns, ne = _num_span(num)
        # 1) 年份 → founded_year（左右均可）
        if num["is_year"]:
            hit = nearest_side('founded_year', num["center"], ns, ne, _METRIC_RADIUS)
            if hit:
                assigned.setdefault('founded_year', []).append(
                    {"kind": "number", "value": num["value"],
                     "percent": False, "term": hit[1]})
            continue

        # 2) 指标归属：左优先（当前指标在数值前）；左侧无候选时才接受右侧紧邻指标
        left_cands, right_cands = [], []
        for asp in asp_pos:
            if asp in _TREND_ASPECTS:
                continue
            hl = nearest_side(asp, num["center"], ns, ne, _METRIC_RADIUS, 'left')
            if hl:
                left_cands.append((hl[0], asp, hl[1]))
            hr = nearest_side(asp, num["center"], ns, ne, 3, 'right')
            if hr:
                right_cands.append((hr[0], asp, hr[1]))
        left_cands.sort()
        right_cands.sort()
        chosen = left_cands[0] if left_cands else (right_cands[0] if right_cands else None)
        if chosen:
            _, asp, term = chosen
            assigned.setdefault(asp, []).append(
                {"kind": "number", "value": num["value"],
                 "percent": num["percent"], "term": term})

        # 3) 百分比增长率额外进趋势槽（趋势词优先取左侧：增长 12%）
        if num["percent"] and 'trend_dir' in asp_pos:
            hit = (nearest_side('trend_dir', num["center"], ns, ne, _TREND_RADIUS, 'left')
                   or nearest_side('trend_dir', num["center"], ns, ne, _TREND_RADIUS, 'right'))
            if hit:
                assigned.setdefault('trend_dir', []).append(
                    {"kind": "number", "value": num["value"],
                     "percent": True, "term": hit[1]})

    # 同方面去重（同值同量纲）+ 限量
    for asp, vals in assigned.items():
        uniq, seen = [], set()
        for v in vals:
            key = (round(v["value"], 4), v["percent"])
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        assigned[asp] = uniq[:8]
    return assigned


def _subject_key(claim: Dict, text: str) -> str:
    """主体键：契约优先（claim.entity / claim.subject），缺省全局键 ''。"""
    if isinstance(claim, dict):
        for f in ('entity', 'subject'):
            v = claim.get(f)
            if v and str(v).strip():
                return re.sub(r'\s+', '', str(v).strip().lower())
    return ''  # 全局默认主体键：两条无主体声明默认同一对比对象


def _aspect_membership(text_low: str, aspects: dict) -> dict:
    """文本命中了哪些方面簇 → {aspect: sorted(命中词, 长优先)}。

    多簇 aspect 成员（同词出现在多个簇）原样保留，冲突比对阶段以
    「同簇内 opposites」优先，再回退跨簇枚举成员比对做修正。
    """
    hits: Dict[str, list] = {}
    for asp, vocab in aspects.items():
        words = list(vocab.get("zh", [])) + list(vocab.get("en", []))
        matched = sorted({w.lower() for w in words if w and w.lower() in text_low},
                         key=len, reverse=True)
        if matched:
            hits[asp] = matched
    return hits


def _negated(text_low: str, keyword: str, syn: dict) -> bool:
    """关键词前 3 字内是否有否定词（中英）。"""
    idx = text_low.rfind(keyword)
    if idx == -1:
        return False
    pre = text_low[max(0, idx - 4): idx]
    for w in syn.get("negation_zh", []):
        if w and w in pre:
            return True
    pre_en = text_low[max(0, idx - 25): idx]
    toks = re.findall(r"[a-z']+", pre_en)
    for w in syn.get("negation_en", []):
        if w in toks or (w.startswith("n't") and "n't" in pre_en):
            return True
    return False


# 长文本保护裁决（v2.0.0 §8.16）：50k 字符截断 / 500 方面命中上限 / 200 槽值上限
_KEYED_MAX_CHARS = 50_000
_KEYED_MAX_ASPECT_HITS = 500
_KEYED_MAX_VALUES = 200


def _build_keyed_slots(claim: Dict, text: str, syn: dict) -> dict:
    """单条 claim → 键控槽表 {aspect: {"values":[...], "terms":[...], "negated"}}。

    数值归属走全文单次扫描 + 就近唯一归属（不跨方面串味）；
    非数值型方面仅承载枚举词与否定极性。
    """
    t_low = text.lower()[:_KEYED_MAX_CHARS]  # 长文本保护：截断防超线性
    subj = _subject_key(claim, text)
    membership = _aspect_membership(t_low, syn.get("aspects", {}))
    # 命中方面数量上限（按方面名稳定排序截断，确定性可复现）
    if len(membership) > _KEYED_MAX_ASPECT_HITS:
        membership = {k: membership[k]
                      for k in sorted(membership)[:_KEYED_MAX_ASPECT_HITS]}
    numeric = _assign_numbers(t_low, membership)
    slots: Dict[str, dict] = {}
    for asp, terms in membership.items():
        neg_any = any(_negated(t_low, kw, syn) for kw in terms)
        slots[asp] = {
            "subject": subj,
            "values": numeric.get(asp, [])[:_KEYED_MAX_VALUES],
            "terms": terms[:4],
            "enum_terms": terms,
            "negated": neg_any,
            "polarity": None,
        }
    return slots


def _value_conflicts(asp: str, va: dict, vb: dict):
    """同槽键的值冲突迭代器，产出 (dedup_key, reason)。

    dedup_key 按归一化值对/极性构造，使同一冲突因跨多个方面槽时只计一次。
    """
    nums_a = [v for v in va.get("values", []) if v["kind"] == "number"]
    nums_b = [v for v in vb.get("values", []) if v["kind"] == "number"]
    for x in nums_a:
        for y in nums_b:
            if x["percent"] != y["percent"]:
                continue  # 量纲不同不比（5% vs 5亿 不构成同值冲突）
            xv, yv = x["value"], y["value"]
            if asp == 'founded_year':
                if abs(xv - yv) >= 1:
                    yield ("num", round(min(xv, yv), 4), round(max(xv, yv), 4),
                           x["percent"]), f"成立年份冲突 {xv:g}↔{yv:g}"
                continue
            base = max(abs(xv), abs(yv), 1.0)
            rel = abs(xv - yv) / base
            if (x["percent"] and abs(xv - yv) >= 3 and rel >= 0.15) or \
               (not x["percent"] and rel >= 0.15):
                yield ("num", round(min(xv, yv), 4), round(max(xv, yv), 4),
                       x["percent"]), f"数值冲突 {xv:g}↔{yv:g}"
    # 否定极性冲突（同一方面，一肯定一否定），且非双否定
    if va.get("negated") != vb.get("negated") and (va.get("negated") or vb.get("negated")):
        yield ("pol", asp), "极性冲突（肯/否对立）"


def _values_conflict(asp: str, va: dict, vb: dict) -> Optional[str]:
    """同槽键两值是否冲突（单值版，返回首个原因或 None）。"""
    for _key, why in _value_conflicts(asp, va, vb):
        return why
    return None


def _enum_conflict(asp: str, sa: dict, sb: dict, syn: dict) -> Optional[str]:
    """枚举/反义簇冲突：opposites 表中互斥词对跨两条声明各命中其一。"""
    ta = set(sa.get("enum_terms", []))
    tb = set(sb.get("enum_terms", []))
    for pair in syn.get("opposites", {}).get(asp, []):
        pa, pb = pair[0].lower(), pair[1].lower()
        if (pa in ta and pb in tb) or (pb in ta and pa in tb):
            return f"反义冲突 {pair[0]}↔{pair[1]}"
    return None


def keyed_score(claim_a: Dict, claim_b: Dict, text_a: str, text_b: str) -> dict:
    """GA11 A 层：键控事实槽冲突评分。

    返回 {"score", "conflict_slots": [...], "slots_a", "slots_b", "reasons": [...]}。
    单冲突槽 35（medium）；2 槽 60（high 边界，保持 high 需明确矛盾）；≥3 槽 85。
    """
    syn = _load_synonyms()
    sa = _build_keyed_slots(claim_a, text_a, syn)
    sb = _build_keyed_slots(claim_b, text_b, syn)
    subj_a = _subject_key(claim_a, text_a)
    subj_b = _subject_key(claim_b, text_b)

    conflict_slots = []
    reasons = []
    # 冲突计数按「值对/反义对」去重：同一数值差异（如 12%↔35%）可能同时落在
    # revenue 与 trend_dir 两槽，但属同一冲突因，只计一次，避免分数虚高。
    conflict_keys = set()
    # 仅在主体键一致（或任一为全局默认键）时比对
    same_subject = (subj_a == subj_b) or subj_a == "" or subj_b == ""
    if same_subject:
        for asp in sorted(set(sa) & set(sb)):
            why = _enum_conflict(asp, sa[asp], sb[asp], syn)
            ckey = ("enum", asp)
            if why is not None:
                conflict_keys.add(ckey)
                conflict_slots.append(asp)
                reasons.append(f"[{asp}] {why}")
                continue
            for ckey2, why2 in _value_conflicts(asp, sa[asp], sb[asp]):
                if why2 and ckey2 not in conflict_keys:
                    conflict_keys.add(ckey2)
                    conflict_slots.append(asp)
                    reasons.append(f"[{asp}] {why2}")

    n = len(conflict_keys)
    if n >= 3:
        score = 85
    elif n == 2:
        score = 60
    elif n == 1:
        score = 35
    else:
        score = 0
    # 长文本保护：限制参与比对的文本规模（50k 字符 / 500 命中方面上限 / 200 槽值）
    return {"score": score, "conflict_slots": conflict_slots,
            "slots_a": sa, "slots_b": sb, "reasons": reasons}


# ═══════════════════════════════════════════════════════════
# 否定 / 反义检测
# ═══════════════════════════════════════════════════════════

# GA11：否定误报豁免——这些短语中的「不/未」表"恒定/达成"而非否定对立
NEG_EXEMPT = {
    "持平", "不变", "基本持平", "保持不变", "维持不变", "稳定", "不止",
    "不久", "不断", "不仅", "不少", "不错", "不可", "不到", "不下",
}


def _neg_hits(text_low: str) -> List[str]:
    """命中否定词，但剔除豁免短语内的假命中。"""
    hits = []
    for w in NEG_WORDS_ZH | NEG_WORDS_EN:
        if w not in text_low:
            continue
        if any(ex in text_low and w in ex for ex in NEG_EXEMPT):
            continue
        hits.append(w)
    return hits


def _detect_negation(text_a: str, text_b: str) -> Tuple[int, List[str]]:
    """返回 (neg_score 0-50, reason_list)"""
    score = 0
    reasons = []
    a_low, b_low = text_a.lower(), text_b.lower()

    # 1. 否定词（豁免"持平/不变"等非对立用法）
    hit_a = _neg_hits(a_low)
    hit_b = _neg_hits(b_low)
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

def _sev(total: int) -> str:
    if total >= 60:
        return 'high'
    if total >= 30:
        return 'medium'
    if total >= 10:
        return 'low'
    return 'none'


def score_legacy(claim_a: Dict, claim_b: Dict, text_a: str, text_b: str) -> dict:
    """v2.4.x 原口径（短语袋 Jaccard + 否定/反义），作为 max 融合的否定路保留。"""
    slots_a = _extract_slots(text_a)
    slots_b = _extract_slots(text_b)
    shared = slots_a & slots_b
    union = slots_a | slots_b
    jaccard = len(shared) / max(len(union), 1)
    slot_score = int(jaccard * 30)

    neg_score, reasons = _detect_negation(text_a, text_b)
    total = slot_score + neg_score
    if len(shared) >= 2 and neg_score > 0:
        total = min(int(total * 1.4), 100)
        reasons.append(f'极性放大：共享槽={len(shared)} + 反义命中')
    return {'score': total, 'severity': _sev(total), 'reasons': reasons,
            'shared_slots': sorted(shared)[:5],
            'slot_score': slot_score, 'neg_score': neg_score}


def score_contradiction(claim_a: Dict, claim_b: Dict) -> Dict:
    """计算两条声明的语义矛盾分（GA11 v2.0.0：keyed + negation 两路 max 融合）

    参数: claim_a / claim_b 至少含 'text' 字段（与 conflict_v3 同结构）；
          可选 'entity'/'subject' 提供键控主体（缺省两条默认同一对比对象）。
    返回: {'score': 0-100, 'severity': ..., 'reasons', 'neg_hits',
           'shared_slots', 'slot_score', 'neg_score',          # 老字段语义不变
           'keyed_score', 'conflict_slots', 'scorer_mode'}      # GA11 新增
    scorer_mode: 'keyed'（键控槽路主导）| 'negation'（否定/反义路主导）。
    """
    text_a = claim_a.get('text', '') if isinstance(claim_a, dict) else str(claim_a)
    text_b = claim_b.get('text', '') if isinstance(claim_b, dict) else str(claim_b)

    # 路 1：v2.4.x 原口径（Jaccard 槽 + 否定/反义）
    legacy = score_legacy(claim_a, claim_b, text_a, text_b)
    # 路 2：GA11 键控事实槽（同槽键值冲突）
    keyed = {}
    keyed_score_val = 0
    try:
        keyed = keyed_score(claim_a if isinstance(claim_a, dict) else {'text': text_a},
                            claim_b if isinstance(claim_b, dict) else {'text': text_b},
                            text_a, text_b)
        keyed_score_val = keyed['score']
    except Exception:
        keyed = {'conflict_slots': [], 'reasons': []}

    # max 融合：两路证据取强，不叠加（避免同义复述被相似度抬分）
    if keyed_score_val >= legacy['score']:
        total = keyed_score_val
        mode = 'keyed'
        reasons = legacy['reasons'] + keyed.get('reasons', [])
    else:
        total = legacy['score']
        mode = 'negation'
        reasons = legacy['reasons']

    return {
        'score': total,
        'severity': _sev(total),
        'reasons': reasons,
        'neg_hits': legacy['reasons'],          # 兼容旧字段名（仅否定路）
        'shared_slots': legacy['shared_slots'],
        'slot_score': legacy['slot_score'],
        'neg_score': legacy['neg_score'],
        # GA11 新增字段（老消费方无感）
        'keyed_score': keyed_score_val,
        'conflict_slots': keyed.get('conflict_slots', []),
        'scorer_mode': mode,
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


# ═══════════════════════════════════════════════════════════
# GA11 B 层（v2.0.0）：LLM 辅助 JSON 槽表 → 复用键控比对
#   opt-in：INFOSEEK_CONTRADICTION_LLM=1 或 enable_llm=True
#   降级链：B 不可用（无 provider/解析失败）→ A（score_contradiction）→ legacy
# ═══════════════════════════════════════════════════════════

import hashlib as _hashlib
_LLM_SLOT_CACHE: Dict[str, dict] = {}
_LLM_SLOT_CACHE_MAX = 512

_LLM_SLOT_PROMPT = """你是事实核查助手。从下面这条声明中抽取结构化事实槽，只输出 JSON，不要解释。

输出 schema：
{"slots": [{"subject": "主体(原文实体，无法判断用空串)", "aspect": "方面英文蛇形命名(如 revenue/profit/ceo_name/hq_city/founded_year)", "value": "归一化值(数字尽量转阿拉伯数字；枚举用小写中文或英文)", "unit": "单位(%, 亿, 万, 年, 人, 个, 或空串)", "polarity": "pos|neg|neutral"}]}
规则：
1) 抽取该声明的核心事实槽，忽略评价性措辞；同一方面只保留最确定的一个值。
2) 数值统一为阿拉伯数字并标注 unit；百分比 unit 用 %。
3) 布尔/肯否事实（如是否开源、是否发布）polarity 用 pos/neg，value 用枚举词。
4) 只输出 JSON。

声明：%TEXT%
JSON："""


def _hash_texts(a: str, b: str) -> str:
    return _hashlib.md5((a + "\u0001" + b).encode("utf-8")).hexdigest()


def _parse_llm_slot_json(content: str) -> dict:
    """从 LLM 输出中鲁棒提取 JSON 槽表。失败抛 ValueError。"""
    if not content:
        raise ValueError("empty llm content")
    txt = content.strip()
    # 去掉 ```json fence
    if "```" in txt:
        m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
        if m:
            txt = m.group(1).strip()
    # 截取首个 { 到末个 }
    s, e = txt.find("{"), txt.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError("no json object")
    data = _json.loads(txt[s:e + 1])
    if not isinstance(data, dict) or not isinstance(data.get("slots"), list):
        raise ValueError("bad schema")
    return data


def _norm_num(v, unit):
    """归一 LLM 给的数值（支持中文数字单位简单换算）。返回 (float, unit) 或 None。"""
    try:
        f = float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
    return f, (unit or "").strip()


def _llm_slot_conflicts(slots_a: list, slots_b: list):
    """复用键控比对思想：同 (主体键, aspect) 比 value/unit/polarity。

    表外方面（LLM 动态命名，如 ceo_name）同样参与。产出 (dedup_key, reason, aspect)。
    """
    def index(slots):
        out = {}
        for s in slots:
            if not isinstance(s, dict):
                continue
            asp = str(s.get("aspect", "")).strip().lower()
            if not asp:
                continue
            subj = re.sub(r"\s+", "", str(s.get("subject", "")).lower())
            out.setdefault((subj, asp), []).append(s)
        return out

    ia, ib = index(slots_a), index(slots_b)
    conflicts = []
    seen = set()
    for key in set(ia) & set(ib):
        subj, asp = key
        # 主体不同（且都非全局空键）不比
        ka = next(iter(ia[key])); kb = next(iter(ib[key]))
        # 极性冲突
        pa, pb = ka.get("polarity"), kb.get("polarity")
        if pa and pb and pa in ("pos", "neg") and pb in ("pos", "neg") and pa != pb:
            ck = ("llm-pol", subj, asp)
            if ck not in seen:
                seen.add(ck)
                conflicts.append((ck, f"[LLM:{asp}] 肯否冲突", asp))
            continue
        na = _norm_num(ka.get("value"), ka.get("unit"))
        nb = _norm_num(kb.get("value"), kb.get("unit"))
        if na and nb:
            (xa, ua), (xb, ub) = na, nb
            if ua == ub:  # 同量纲才比
                base = max(abs(xa), abs(xb), 1.0)
                rel = abs(xa - xb) / base
                if asp.endswith("year"):
                    hit = abs(xa - xb) >= 1
                elif ua == "%":
                    hit = abs(xa - xb) >= 3 and rel >= 0.15
                else:
                    hit = rel >= 0.15
                if hit:
                    ck = ("llm-num", subj, asp, round(min(xa, xb), 4),
                          round(max(xa, xb), 4), ua)
                    if ck not in seen:
                        seen.add(ck)
                        conflicts.append((ck, f"[LLM:{asp}] 数值冲突 {xa:g}{ua}↔{xb:g}{ua}", asp))
        else:
            va = str(ka.get("value", "")).strip().lower()
            vb = str(kb.get("value", "")).strip().lower()
            if va and vb and va != vb:
                ck = ("llm-enum", subj, asp, va, vb)
                if ck not in seen:
                    seen.add(ck)
                    conflicts.append((ck, f"[LLM:{asp}] 枚举冲突 {va}↔{vb}", asp))
    return conflicts


def _llm_keyed_score(parsed_a: dict, parsed_b: dict) -> dict:
    """对两份 LLM 槽表做键控冲突评分（复用单冲突 35/双 60/≥3 85 裁决）。"""
    conflicts = _llm_slot_conflicts(parsed_a.get("slots", []), parsed_b.get("slots", []))
    n = len({c[0] for c in conflicts})
    score = 85 if n >= 3 else 60 if n == 2 else 35 if n == 1 else 0
    return {"score": score,
            "conflict_slots": sorted({c[2] for c in conflicts}),
            "reasons": [c[1] for c in conflicts]}


def score_contradiction_hybrid(claim_a: Dict, claim_b: Dict, *,
                               enable_llm: Optional[bool] = None,
                               llm_call_fn=None, temperature: float = 0.0) -> Dict:
    """GA11 C 混合分层：B(LLM JSON 槽) → A(本地键控) → legacy。

    - enable_llm=None 时读 env INFOSEEK_CONTRADICTION_LLM；
    - LLM 不可用（无 provider/超时/JSON 解析失败）自动降级本地，不抛错；
    - 两路 max 融合：LLM 槽表用于补充 A 层覆盖不到的表外方面。
    返回在 score_contradiction 基础上增补 llm_used/scorer_mode 细化。
    """
    base = score_contradiction(claim_a, claim_b)  # 已含 A(keyed)+legacy negation
    if enable_llm is None:
        enable_llm = os.environ.get("INFOSEEK_CONTRADICTION_LLM", "").lower() \
            in ("1", "true", "on", "yes")
    if not enable_llm:
        base["llm_used"] = False
        return base

    text_a = claim_a.get("text", "") if isinstance(claim_a, dict) else str(claim_a)
    text_b = claim_b.get("text", "") if isinstance(claim_b, dict) else str(claim_b)
    cache_key = _hash_texts(text_a[:5000], text_b[:5000])
    try:
        if cache_key in _LLM_SLOT_CACHE:
            llm_res = _LLM_SLOT_CACHE[cache_key]
        else:
            if llm_call_fn is None:
                from core.llm_router import llm_call as _llm_call
            else:
                _llm_call = llm_call_fn

            # A/B 分别独立出槽，保证槽归属正确（结果按文本哈希缓存）
            # 用字面替换而非 str.format：模板 JSON 示例含裸花括号
            def _one(text):
                p = _LLM_SLOT_PROMPT.replace("%TEXT%", text[:2000])
                o = _llm_call(p, max_tokens=400, temperature=temperature)
                return _parse_llm_slot_json(
                    o.get("content", "") if isinstance(o, dict) else str(o))

            parsed_a = _one(text_a)
            parsed_b = _one(text_b)
            llm_res = _llm_keyed_score(parsed_a, parsed_b)
            if len(_LLM_SLOT_CACHE) >= _LLM_SLOT_CACHE_MAX:
                _LLM_SLOT_CACHE.clear()
            _LLM_SLOT_CACHE[cache_key] = llm_res

        final_score = max(base["score"], llm_res["score"])
        base["keyed_score"] = max(base.get("keyed_score", 0), llm_res["score"])
        base["conflict_slots"] = sorted(set(base.get("conflict_slots", []))
                                        | set(llm_res["conflict_slots"]))
        base["reasons"] = base.get("reasons", []) + [
            r for r in llm_res["reasons"] if r not in base.get("reasons", [])]
        base["score"] = final_score
        base["severity"] = _sev(final_score)
        base["scorer_mode"] = "llm_hybrid" if llm_res["score"] > 0 else base["scorer_mode"]
        base["llm_used"] = True
        return base
    except Exception as e:
        # 降级链：B 失败 → A/legacy（base 已是其结果）
        base["llm_used"] = False
        base["llm_error"] = f"{type(e).__name__}:{str(e)[:80]}"
        return base


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
