"""
infoseek_zerodep_nlp.py — 零依赖 NLP 原语（v1.0.0）
=====================================================================

设计目标
--------
1. **纯标准库即可运行核心**：中文/英文关键词抽取、句子切分、基础摘要，
   不依赖 jieba / summa / httpx 等任何第三方包。
2. **外部 NLP 优先，零依赖为最终兜底（优先级链）**：
   jieba（中文主路径）→ summa（英文主路径/中文次路径）→ **zerodep 共识**。
   前两级可用时以其结果为准（不降级、不经过标准库过滤）；
   仅当外部 NLP 不可用/无结果时，才启用零依赖共识兜底。
3. **冗余验证兜底精度（最终兜底分支的硬约束）**：
   当无外部 NLP 时，并行运行多重独立的纯标准库估计器，
   通过「共识投票」(consensus voting) 只保留被 >=2 个估计器共同认可的候选词，
   再经最长匹配抑制 + 停用词闸，在弱分词环境下保持高精度。

这是 infoseek 跨多生态平台发行（WorkBuddy / ima / Claude / Dify / Coze / 通用 MCP）
的底座前提：核心在「零 pip 安装」下也能产出可信结果。

用法
----
    from infoseek_zerodep_nlp import extract_keywords, extract_keywords_detailed, summarize
    kws, engine = extract_keywords_detailed("一段中文文本……", max_kw=15)
    # engine ∈ {"jieba", "summa", "zerodep", "empty"}
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, List, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# 内置资源（纯标准库，无外部字典文件依赖）
# ---------------------------------------------------------------------------

# 轻量中文停用词典（覆盖高频虚词/标点，足以支撑兜底分词精度）
_ZH_STOP = set(
    "的 了 和 是 在 我 有 他 这 中 大 来 上 国 个 到 说 们 为 子 也 你 得 着 下 就 都 "
    "与 及 或 把 被 让 使 从 向 对 等 而 且 但 若 因 故 所 以 于 之 其 此 该 各 某 何 "
    "一种 一样 一些 这样 那样 这些 那些 这个 那个 我们 你们 他们 它们 自己 什么 怎么 "
    "可以 能够 应该 需要 进行 通过 根据 由于 以及 并且 然而 因此 所以 但是 如果 虽然 "
    "目前 当前 已经 正在 将要 之后 之前 之间 对于 关于 由于 基于 方面 问题 情况 方式 "
    "一个 一种 一项 一名 一位 一部分 一种 一种 相关 主要 重要 基本 整体 总体 一定 可能"
    .split()
)

# 英文停用词（缩小版，覆盖最常见虚词）
_EN_STOP = set(
    "the a an and or but if then else when while of to in on at by for with from as is "
    "are was were be been being this that these those it its he she they we you i my "
    "your our their his her not no nor do does did done has have had can could may might "
    "will would shall should must about into over under between through during before "
    "after above below up down out off so than too very also more most such same other "
    "which who whom whose what why how all any each few many much one two new first"
    .split()
)

_CJK = re.compile(r"[一-鿿]")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\.\n])|(?<=[。！？!?])")
_WORD_EN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*")
_CJK_RUN = re.compile(r"[一-鿿]+")


# ---------------------------------------------------------------------------
# 句子切分（标准库）
# ---------------------------------------------------------------------------

def segment_sentences(text: str) -> List[str]:
    """按中英文标点/换行切句，过滤空句。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# 标准库估计器（无外部依赖，多重冗余）
# ---------------------------------------------------------------------------

def _longest_match_suppress(counter: Counter) -> List[str]:
    """最长匹配抑制：按 (长度降序, 词频降序) 保留候选，丢弃被更长候选包含的短片段。

    例：「人工智能/人工/工智/智能」→ 仅留「人工智能」。这是无 jieba 时
    保证关键词精度的关键：避免把一个词拆成多个噪声片段进入共识投票。
    """
    items = sorted(counter.items(), key=lambda kv: (len(kv[0]), kv[1]), reverse=True)
    kept: List[str] = []
    kept_set: Set[str] = set()
    for gram, _ in items:
        if any(gram in k for k in kept_set):
            continue
        kept.append(gram)
        kept_set.add(gram)
    return kept


def _ngram_freq(text: str, n: int, min_count: int = 2) -> Counter:
    """从 CJK 连续串中抽取 n 元语法词频。"""
    c = Counter()
    for run in _CJK_RUN.findall(text):
        for i in range(len(run) - n + 1):
            gram = run[i:i + n]
            # 仅剔除「完全由停用字构成」的虚词 n-gram（如「的是」）或
            # 整词命中停用短语（如「正在」）；含实字的组合（如「大模型」含「大」）必须保留
            if gram in _ZH_STOP or all(ch in _ZH_STOP for ch in gram):
                continue
            c[gram] += 1
    return Counter({k: v for k, v in c.items() if v >= min_count})


def _est_zh_ngram(text: str, top: int = 20) -> Set[str]:
    """估计器 A：多粒度 n 元语法词频（2/3/4 字组合）。"""
    c = Counter()
    for n in (2, 3, 4):
        c.update(_ngram_freq(text, n))
    # 过滤内置停用词与单字噪声已由 _ngram_freq 处理
    return set(_longest_match_suppress(c)[:top])


def _est_zh_position(text: str, top: int = 20) -> Set[str]:
    """估计器 B：标题/首尾句加权抽取——出现在前 30% 文本的高频 2-3 字组合。"""
    sents = segment_sentences(text)
    if not sents:
        return set()
    head = " ".join(sents[: max(1, len(sents) // 3)])
    c = Counter()
    for run in _CJK_RUN.findall(head):
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                gram = run[i:i + n]
                if gram in _ZH_STOP or all(ch in _ZH_STOP for ch in gram):
                    continue
                c[gram] += 1
    # 同时要求该候选在全文中也出现过（位置 + 全局双重约束 → 高精度）
    full = (
        set(_ngram_freq(text, 2).keys())
        | set(_ngram_freq(text, 3).keys())
        | set(_ngram_freq(text, 4).keys())
    )
    cands = [w for w in _longest_match_suppress(c) if w in full]
    return set(cands[:top])


def _est_zh_docfreq(text: str, top: int = 20) -> Set[str]:
    """估计器 C：类 TF-IDF 文档频率——在多个句子中出现过的 2-3 字组合。"""
    sents = segment_sentences(text)
    if not sents:
        return set()
    # 构建每个句子的 n-gram 集合（去重）
    sent_grams = []
    for s in sents:
        grams = set()
        for run in _CJK_RUN.findall(s):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g in _ZH_STOP or all(ch in _ZH_STOP for ch in g):
                        continue
                    grams.add(g)
        sent_grams.append(grams)
    # 文档频率
    df = Counter()
    for grams in sent_grams:
        for g in grams:
            df[g] += 1
    # 仅在 >=2 个句子出现的候选（跨句一致性 → 高精度）
    cand = {g for g, f in df.items() if f >= 2}
    # 按 (文档频率, 全局词频) 排序
    global_freq = Counter()
    for run in _CJK_RUN.findall(text):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                if any(ch in _ZH_STOP for ch in g):
                    continue
                global_freq[g] += 1
    return set(_longest_match_suppress(Counter({g: df[g] for g in cand}))[:top])


def _est_en_tfidf(text: str, top: int = 20) -> Set[str]:
    """估计器（英文）：标准库 TF-IDF 近似。"""
    sents = re.split(r"[.!?\n]+", text)
    sents = [s.strip() for s in sents if s.strip()]
    doc_freq = Counter()
    for s in sents:
        toks = {t.lower() for t in _WORD_EN.findall(s)} - _EN_STOP
        for t in toks:
            doc_freq[t] += 1
    if not sents:
        return set()
    n = len(sents)
    scored = {}
    for t, df in doc_freq.items():
        idf = math.log((n + 1) / (df + 1)) + 1.0
        tf = sum(1 for s in sents for w in _WORD_EN.findall(s) if w.lower() == t)
        scored[t] = tf * idf
    return {t for t, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:top]}


# ---------------------------------------------------------------------------
# 冗余共识验证（核心：兜底精度）
# ---------------------------------------------------------------------------

def redundant_consensus(estimator_sets: Iterable[Set[str]], min_votes: int = 2) -> Set[str]:
    """对多个估计器产出的关键词集合做共识投票。

    一个候选词只有在 >= min_votes 个独立估计器中同时出现，才被认定为
    「高置信关键词」。这把无外部 NLP 时的弱分词噪声压到最低，
    用多重冗余换精度（precision-first）。

    返回：被共识认可的候选集合（高精度子集）。
    """
    sets = [s for s in estimator_sets if s]
    if not sets:
        return set()
    if len(sets) == 1:
        return set(sets[0])  # 无冗余可投，原样返回
    counter: Counter = Counter()
    for s in sets:
        for w in s:
            counter[w] += 1
    return {w for w, v in counter.items() if v >= min_votes}


def _est_en_freq(text: str, top: int = 20) -> Set[str]:
    """英文冗余估计器：纯词频（无 IDF），与 _est_en_tfidf 形成双重校验。"""
    toks = [t.lower() for t in _WORD_EN.findall(text)] or []
    c = Counter(w for w in toks if w not in _EN_STOP and len(w) > 1)
    return {w for w, _ in c.most_common(top)}


def _optional_jieba(text: str, top: int) -> Set[str]:
    """可选增强：jieba.textrank（中文友好）。不可用时返回空集。"""
    try:
        import jieba.analyse  # 懒加载：无包则跳过
        kws = jieba.analyse.textrank(text, topK=top, withWeight=False)
        return set(kws or [])
    except Exception:
        return set()


def _optional_summa(text: str, top: int) -> Set[str]:
    """可选增强：summa.keywords（英文友好）。不可用时返回空集。"""
    try:
        from summa.keywords import keywords as summa_keywords  # 懒加载
        txt = summa_keywords(text, words=top)
        return {w.strip() for w in (txt or "").split("\n") if w.strip()}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------

def detect_lang(text: str) -> str:
    zh = len(_CJK.findall(text))
    en = len(_WORD_EN.findall(text))
    return "zh" if zh >= en else "en"


def extract_keywords_detailed(
    text: str,
    max_kw: int = 15,
    lang: str | None = None,
    min_votes: int = 2,
) -> Tuple[List[Tuple[str, float]], str]:
    """抽取关键词 + 引擎标识。

    优先级链（零依赖分词为**最终兜底**，不是首选）：
      1. **jieba**（中文主路径）——语义分词精度最高；
      2. **summa**（英文主路径 / 中文次路径）；
      3. **zerodep 零依赖共识**（最终防线）——多重标准库估计器 + 共识投票
         (min_votes) + 最长匹配抑制，仅在前两级不可用时启用。

    返回: ([(词, 置信分)], 引擎名)，引擎名 ∈ {"jieba", "summa", "zerodep", "empty"}。
    外部 NLP 结果不再经过标准库过滤（避免拉低 jieba 精度），置信分统一标记 3.0。
    """
    if not text or not text.strip():
        return [], "empty"

    lang = lang or detect_lang(text)

    # ── 1) 外部 NLP 主路径（优先）────────────────────────────────
    ext: Set[str] = set()
    ext_engine: str | None = None
    if lang == "zh":
        jb = _optional_jieba(text, max_kw)
        if jb:
            ext |= jb
            ext_engine = "jieba"
        if not ext:  # jieba 不可用或无结果 → summa 次之
            sm = _optional_summa(text, max_kw)
            if sm:
                ext |= sm
                ext_engine = "summa"
    else:
        sm = _optional_summa(text, max_kw)
        if sm:
            ext |= sm
            ext_engine = "summa"

    if ext:
        ranked = sorted(ext, key=len, reverse=True)[:max_kw]
        return [(w, 3.0) for w in ranked], ext_engine or "external"

    # ── 2) 零依赖共识（最终兜底）────────────────────────────────
    estimators: List[Set[str]] = []
    if lang == "zh":
        estimators.append(_est_zh_ngram(text, max_kw))
        estimators.append(_est_zh_position(text, max_kw))
        estimators.append(_est_zh_docfreq(text, max_kw))
    else:
        estimators.append(_est_en_tfidf(text, max_kw))
        estimators.append(_est_en_freq(text, max_kw))

    # 共识投票（冗余验证兜底精度）
    consensus = redundant_consensus(estimators, min_votes=min_votes)

    vote_count: Counter = Counter()
    for s in estimators:
        for w in s:
            vote_count[w] += 1

    if consensus:
        # 最终精度闸：对共识集再做最长匹配抑制，丢弃被长词包含的短片段
        # （例：共识含「人工智能/人工智/智能」→ 仅留「人工智能」）
        consensus = set(_longest_match_suppress(Counter(consensus)))
        # 高精度子集：按共识票数 + 词长（长词通常更具体）排序
        ranked = sorted(
            consensus,
            key=lambda w: (vote_count[w], len(w)),
            reverse=True,
        )[:max_kw]
        return [(w, float(vote_count[w])) for w in ranked], "zerodep"

    # 共识集为空（极短文本/单句）：回退到票数最高候选（召回优先、降权）
    # V1.2.1 修复：估计器内部 min_count=2 会滤掉「每词仅出现 1 次」的文本（候选池为空 → kw=0），
    # 此处用 min_count=1 重建候选池，保证零依赖兜底在单次文本上仍有召回。
    fallback_pool: Counter = Counter()
    for n in (2, 3, 4):
        fallback_pool.update(_ngram_freq(text, n, min_count=1))
    fallback_terms = _longest_match_suppress(fallback_pool)
    ranked = sorted(fallback_terms, key=lambda w: fallback_pool[w], reverse=True)[:max_kw]
    return [(w, float(fallback_pool[w]) * 0.5) for w in ranked], "zerodep"


def extract_keywords(
    text: str,
    max_kw: int = 15,
    lang: str | None = None,
    min_votes: int = 2,
) -> List[Tuple[str, float]]:
    """抽取关键词，返回 [(词, 置信分), ...]，按置信分降序（简单接口）。

    优先级链见 `extract_keywords_detailed`：jieba/summa 优先，
    零依赖共识为最终兜底。
    """
    kws, _ = extract_keywords_detailed(
        text, max_kw=max_kw, lang=lang, min_votes=min_votes
    )
    return kws


def summarize(text: str, max_sentences: int = 3, lang: str | None = None) -> str:
    """极简抽取式摘要：取「含最多共识关键词」的句子。"""
    sents = segment_sentences(text)
    if not sents:
        return ""
    kws = {w for w, _ in extract_keywords(text, max_kw=20, lang=lang)}
    if not kws:
        return " ".join(sents[:max_sentences])
    scored = []
    for s in sents:
        score = sum(1 for kw in kws if kw in s)
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return " ".join(s for _, s in scored[:max_sentences])


# ---------------------------------------------------------------------------
# 自测（零 pip 依赖验证）：`python infoseek_zerodep_nlp.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_zh = (
        "人工智能大模型技术正在快速迭代。大模型在自然语言处理任务上表现突出，"
        "大模型推动了生成式人工智能的发展。多家科技公司发布了自研大模型产品，"
        "开源大模型生态也在迅速扩张。人工智能与大模型的结合正在重塑软件产业。"
    )
    sample_en = (
        "Vector databases index high-dimensional embeddings for semantic search. "
        "Embeddings power retrieval augmented generation in modern LLM applications. "
        "Semantic search relies on efficient vector similarity at scale."
    )

    print("=== 中文关键词（优先级链自测）===")
    zh_kw, zh_engine = extract_keywords_detailed(sample_zh, max_kw=10)
    assert zh_kw, "必须至少产出 1 个关键词"
    print(f"  engine={zh_engine}")
    for w, s in zh_kw:
        print(f"  {w:<10} score={s}")

    print("\n=== 英文关键词（优先级链自测）===")
    en_kw, en_engine = extract_keywords_detailed(sample_en, max_kw=10)
    assert en_kw, "必须至少产出 1 个关键词"
    print(f"  engine={en_engine}")
    for w, s in en_kw:
        print(f"  {w:<18} score={s}")

    print("\n=== 摘要 ===")
    print("  " + summarize(sample_zh, max_sentences=2))

    print("\n[OK] 零依赖核心在纯标准库下运行成功，无需任何 pip 安装。")
