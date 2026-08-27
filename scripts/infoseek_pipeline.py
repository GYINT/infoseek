#!/usr/bin/env python3
"""
infoseek_pipeline.py — 锚点→采集→聚合 全链路调度器 (v1.2.0)

从 infos 锚点清单出发，经 anchor_adapter 转换为 seek 意图卡片，
依次执行：输入契约验证 → URL预检 → 三级降级提取 → 治理反馈 → 输出聚合。

用法:
  # 给定锚点文件
  python3 infoseek_pipeline.py --anchors anchors.json [--output ./outputs/]

  # 给定行业/主题（自动搜素+采集）
  python3 infoseek_pipeline.py --industry "量化交易" [--output ./outputs/]
"""

import concurrent.futures
import json, os, sys, time, logging
from datetime import datetime

# v1.0.1 评估升级：搜索引擎全生命周期管理（健康状态机 + 配额动态追踪）
try:
    from engine_lifecycle import get_lifecycle
except ImportError:  # 独立运行回退
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine_lifecycle import get_lifecycle

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# 导入单一真源模块
from anchor_adapter import infos_to_seek


# ═══════════════════════════════════════════════════════════════
# 阶段 0: 行业→锚点自动生成（新增, P0-A）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 阶段 0.0: 搜索引擎降级链（v1.0.0 重写）
# ═══════════════════════════════════════════════════════════════
# 背景：api.duckduckgo.com 即时接口自 2022 起被限流/废弃；旧 Bing 分支把
# RSS XML 当 HTML 正则解析（永远抓不到结果）；失败时静默回退单个 Wikipedia
# 「演示锚点」→ 能产出看似完整但覆盖 1 个来源的报告。
# v1.0.0 方案：DDG HTML → Bing RSS（正确 XML 解析）→ Wikipedia opensearch
# （真实结果）；全链失败返回 []，由调用方做覆盖率门控，不再伪造结果。

import re as _re

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def _http_get(url: str, timeout: int = 3) -> bytes:
    """标准库 GET（无第三方依赖）。v1.0.1 PATCH: 默认超时 10s→3s 加速降级链。"""
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _search_duckduckgo_html(query: str, max_results: int = 10) -> list:
    """DDG HTML 端点（html.duckduckgo.com，无需 API key，需 UA 伪装）。"""
    import urllib.parse
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    html = _http_get(url).decode('utf-8', errors='ignore')
    out = []
    for m in _re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html
    ):
        href, title = m.group(1), _re.sub(r'<[^>]+>', '', m.group(2)).strip()[:120]
        if href.startswith('//'):
            href = 'https:' + href
        if href and href not in [x['url'] for x in out]:
            out.append({"url": href, "title": title or query})
        if len(out) >= max_results:
            break
    return out


def _search_bing_rss(query: str, max_results: int = 10) -> list:
    """Bing RSS 端点（format=rss 返回 XML，用 ElementTree 正确解析）。"""
    import urllib.parse
    import xml.etree.ElementTree as ET
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&format=rss")
    out = []
    try:
        root = ET.fromstring(_http_get(url))
        for item in root.iter('item'):
            link = (item.findtext('link') or '').strip()
            title = (item.findtext('title') or query).strip()[:120]
            if link and link not in [x['url'] for x in out]:
                out.append({"url": link, "title": title})
            if len(out) >= max_results:
                break
    except Exception as e:
        log.warning(f"[Bing-RSS] 解析失败: {e}")
    return out


def _search_wikipedia(query: str, max_results: int = 10) -> list:
    """Wikipedia opensearch API（真实结果兜底；CJK 查询走中文维基）。"""
    import json as _json
    import urllib.parse
    lang = "zh" if _re.search(r'[\u4e00-\u9fff]', query) else "en"
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=opensearch"
           f"&format=json&limit={max_results}&search=" + urllib.parse.quote(query))
    try:
        data = _json.loads(_http_get(url).decode('utf-8', errors='ignore'))
    except Exception:
        return []
    titles = data[1] if len(data) > 1 else []
    links = data[3] if len(data) > 3 else []
    out = []
    for t, l in zip(titles, links):
        if l and l not in [x['url'] for x in out]:
            out.append({"url": l, "title": str(t)[:120]})
    return out[:max_results]


def _search_jina(query: str, max_results: int = 5) -> list:
    """Jina AI 搜索（s.jina.ai，免 key 基础版，v1.1.0 主选）。

    返回 LLM 友好内容（markdown）；keyless 有速率限制，故固定 top-5。
    """
    import urllib.parse
    url = "https://s.jina.ai/?q=" + urllib.parse.quote(query)
    data = json.loads(_http_get(url).decode('utf-8', errors='ignore'))
    out = []
    for r in (data.get('data') or []):
        u = r.get('url')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('content') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_exa(query: str, max_results: int = 5) -> list:
    """Exa 语义搜索（API key，v1.1.0 次选；免费 1000 次/月）。"""
    try:
        from core.key_manager import KeyManager
        key = KeyManager.instance().get('exa')
    except Exception:
        key = os.environ.get('EXA_API_KEY', '')
    if not key:
        return []
    import urllib.parse
    payload = json.dumps({
        "query": query, "numResults": max_results,
        "contents": {"text": {"maxCharacters": 200}},
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.exa.ai/search", data=payload,
        headers={'Content-Type': 'application/json',
                 'x-api-key': key}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
    out = []
    for r in (data.get('results') or []):
        u = r.get('url')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('text') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_tavily(query: str, max_results: int = 5) -> list:
    """Tavily 搜索（API key，RAG 调优，Exa 不可用时的冗余替代）。"""
    try:
        from core.key_manager import KeyManager
        key = KeyManager.instance().get('tavily')
    except Exception:
        key = os.environ.get('TAVILY_API_KEY', '')
    if not key:
        return []
    payload = json.dumps({"api_key": key, "query": query,
                          "max_results": max_results}).encode('utf-8')
    req = urllib.request.Request(
        "https://api.tavily.com/search", data=payload,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
    out = []
    for r in (data.get('results') or []):
        u = r.get('url')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('content') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_tinyfish(query: str, max_results: int = 5) -> list:
    """TinyFish 搜索（API key，agent 原生；最终冗余替代）。

    端点形态以官方文档为准（本实现为结构参考，发布前核验）。
    """
    try:
        from core.key_manager import KeyManager
        key = KeyManager.instance().get('tinyfish')
    except Exception:
        key = os.environ.get('TINYFISH_API_KEY', '')
    if not key:
        return []
    import urllib.parse
    url = ("https://api.search.tinyfish.ai/search?q="
           + urllib.parse.quote(query) + f"&key={key}&limit={max_results}")
    data = json.loads(_http_get(url, timeout=15).decode('utf-8', errors='ignore'))
    results = data.get('results') or data.get('data') or []
    out = []
    for r in results:
        u = r.get('url')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('snippet') or r.get('content') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_zhipu(query: str, max_results: int = 5) -> list:
    """智谱 GLM Web Search API（国内首选，v1.1.0；付费 key）。

    官方文档：open.bigmodel.cn/api/paas/v4/web_search。返回结构化结果列表
    （title/url/content/media/publish_date），聚合智谱自研 + 搜狗 + 夸克。
    """
    try:
        from core.key_manager import KeyManager
        key = KeyManager.instance().get('zhipu')
    except Exception:
        key = os.environ.get('ZHIPU_API_KEY', '')
    if not key:
        return []
    payload = json.dumps({"search_query": query, "search_engine": "search_pro",
                          "count": max_results, "search_intent": False}).encode('utf-8')
    req = urllib.request.Request(
        "https://open.bigmodel.cn/api/paas/v4/web_search", data=payload,
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
    out = []
    for r in (data.get('search_result') or []):
        u = r.get('link')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('content') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_metaso(query: str, max_results: int = 5) -> list:
    """秘塔 AI 搜索（国内次选，v1.1.0；付费 key）。

    端点按社区文档实现（api.metaso.cn/v1/search），发布前需以官方
    API 文档核验；无 key 返回 []（降级链自动跳过）。
    """
    try:
        from core.key_manager import KeyManager
        key = KeyManager.instance().get('metaso')
    except Exception:
        key = os.environ.get('METASO_API_KEY', '')
    if not key:
        return []
    payload = json.dumps({"query": query, "top_k": max_results}).encode('utf-8')
    req = urllib.request.Request(
        "https://api.metaso.cn/v1/search", data=payload,
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='ignore'))
    out = []
    for r in (data.get('results') or []):
        u = r.get('url')
        if u and u not in [x['url'] for x in out]:
            out.append({"url": u, "title": r.get('title') or query,
                        "snippet": (r.get('snippet') or '')[:200]})
        if len(out) >= max_results:
            break
    return out


def _search_cn_web(query: str, max_results: int = 5) -> list:
    """国内网页 AI 搜索最终兜底（opt-in，v1.1.0；非官方端点）。

    `INFOSEEK_CN_AI_SEARCH=1` 启用。针对 360AI搜 / Kimi探索版 / 天工 等
    无公开 API 的网页产品，请求其搜索页并通用解析（title/description/链接）。

    如实声明：端点为准官方/网页接口，**可能失效**，发布前需逐产品核验维护；
    任何失败自动降级（不影响主链）。默认关闭。
    """
    if os.environ.get('INFOSEEK_CN_AI_SEARCH') != '1':
        return []
    import urllib.parse
    import re as _re
    engines = [
        ("360AI搜", "https://so.com/s?q={q}"),
        ("Kimi探索版", "https://kimi.moonshot.cn/?q={q}"),
        ("天工AI", "https://www.tiangong.cn/?q={q}"),
    ]
    out = []
    for name, tpl in engines:
        try:
            html = _http_get(tpl.replace('{q}', urllib.parse.quote(query)),
                             timeout=10).decode('utf-8', errors='ignore')
            # 通用解析：标题 + meta description + 内链文本（best-effort）
            title = ''
            m = _re.search(r'<title[^>]*>(.*?)</title>', html, _re.S)
            if m:
                title = _re.sub(r'<[^>]+>', '', m.group(1)).strip()[:80]
            desc = ''
            m = _re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, _re.S)
            if m:
                desc = _re.sub(r'<[^>]+>', '', m.group(1)).strip()[:200]
            if title:
                out.append({"url": tpl.replace('{q}', urllib.parse.quote(query)),
                            "title": title, "snippet": desc or f"[{name}] 结果页（非官方端点，请人工核验）"})
        except Exception:
            continue
        if len(out) >= max_results:
            break
    log.warning(f"[CN-AI-Web] 兜底结果 {len(out)} 条（非官方端点，质量未评级）")
    return out


def _search_qveris(query: str, max_results: int = 5) -> list:
    """QVeris 能力路由引擎（v1.2 消费者接入）。

    结构化数据能力（金融/宏观/风控/加密/另类信号）：discover → call 返回 JSON 摘要。
    - 无 key → []（零网络开销）
    - 配额/认证错误**上抛**（QVerisQuotaError=429 / QVerisAuthError=401/403），
      由 _call_engine → engine_lifecycle.classify 分类（quota 禁用 / forbidden）
    - 结果 url 为 qveris://exec/<id> 伪 URL（结构化数据，非网页，带 tool_id/provider/cost）
    """
    try:
        from qveris_client import search as qv_search
        from qveris_client import QVerisQuotaError, QVerisAuthError
    except ImportError:
        return []
    try:
        return qv_search(query, max_results=max_results)
    except (QVerisQuotaError, QVerisAuthError):
        raise
    except Exception as e:
        log.warning(f"[QVeris] 搜索 '{query}' 失败: {e}")
        return []


def _ai_engines() -> list:
    """AI 键控冗余链：Exa → Tavily → 智谱（国内）→ 秘塔（国内）→ TinyFish → QVeris（结构化数据）。"""
    return [
        ("Exa", _search_exa),
        ("Tavily", _search_tavily),
        ("Zhipu", _search_zhipu),
        ("Metaso", _search_metaso),
        ("TinyFish", _search_tinyfish),
        ("QVeris", _search_qveris),
    ]


def _has_ai_key() -> bool:
    return any(os.environ.get(k) for k in
               ('EXA_API_KEY', 'TAVILY_API_KEY', 'TINYFISH_API_KEY',
                'ZHIPU_API_KEY', 'METASO_API_KEY', 'QVERIS_API_KEY'))


# ═══════════════════════════════════════════════════════════
# v1.1.0：search_web 并行化（层内并用 + 层间降级 + 动态保留）
# 设计依据：infoseek_parallel_audit.md / infoseek_parallel_four_evals.md
# ═══════════════════════════════════════════════════════════

# 引擎权重表（组间择优；组内保持引擎原始顺序）
_ENGINE_WEIGHT = {
    'Exa': 1.0, 'DuckDuckGo-HTML': 1.0, 'Bing-RSS': 0.9, 'Zhipu': 0.9,
    'Tavily': 0.9, 'QVeris': 0.9, 'Jina-AI': 0.8, 'Metaso': 0.8, 'Wikipedia': 0.7,
    'TinyFish': 0.7, 'CN-AI-Web': 0.3,
}

def _free_engines() -> list:
    """免费引擎（无限量；默认层主力）。运行时构建（支持测试 monkeypatch）。"""
    return [
        ("DuckDuckGo-HTML", _search_duckduckgo_html),
        ("Bing-RSS", _search_bing_rss),
        ("Jina-AI", _search_jina),
        ("Wikipedia", _search_wikipedia),
    ]

_KEY_ENV = {
    'Exa': 'EXA_API_KEY', 'Tavily': 'TAVILY_API_KEY', 'Zhipu': 'ZHIPU_API_KEY',
    'Metaso': 'METASO_API_KEY', 'TinyFish': 'TINYFISH_API_KEY',
    'QVeris': 'QVERIS_API_KEY',
}


def _engine_has_key(name: str) -> bool:
    return bool(os.environ.get(_KEY_ENV.get(name, '')))


def _quota_engines_with_key() -> list:
    """已配置 key 的限量引擎（默认模式保留池，配额保护）。"""
    return [(n, f) for n, f in _ai_engines() if _engine_has_key(n)]


def _default_layer() -> list:
    """默认层：4 免费引擎 + CN 网页兜底（opt-in，内部自判）。"""
    return _free_engines() + [("CN-AI-Web", _search_cn_web)]


def _call_engine(name: str, fn, query: str, max_results: int) -> list:
    """引擎调用包装（v1.0.1 生命周期升级）：健康检查 + 成功/失败记录 + 错误分类。

    - 引擎被禁用时直接跳过（返回 []，零网络开销）
    - 成功 → 清连续失败计数；失败 → 分类记录（429/401/403/timeout/...）
    返回结构 [{url,title,snippet}]。
    """
    lc = get_lifecycle()
    lc.reconcile(name)  # P3 新鲜度自愈：访问前先对账（仅异常态轻量变更，常态零开销）
    if lc.is_disabled(name):
        log.debug(f"[{name}] 引擎禁用中（健康/配额/认证），跳过")
        return []
    try:
        res = fn(query, max_results)
        lc.record_success(name, res)  # P3.3 传入响应做 API 漂移检测（默认关闭）
        return [r for r in (res or []) if r.get('url')]
    except Exception as e:
        lc.record_failure(name, e)
        log.warning(f"[{name}] 搜索 '{query}' 失败: {e}")
        return []


def _parallel_merge(engines: list, query: str, max_results: int,
                    max_workers: int = 4) -> list:
    """层内并用：并行调用 + url 去重 + 组间权重/组内保序 → top-N。

    引擎失败相互隔离（异常仅记录）；返回结构 [{url,title,snippet}]。
    集成生命周期：自动剔除禁用引擎 + 调用经 _call_engine 包装（记录健康/配额）。
    """
    collected = {name: [] for name, _ in engines}
    # 生命周期：剔除禁用引擎（健康/配额/认证）
    engines = get_lifecycle().get_active(engines)
    if not engines:
        return []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, max(1, len(engines)))) as ex:
        futs = {ex.submit(_call_engine, name, fn, query, max_results): name
                for name, fn in engines}
        for f in concurrent.futures.as_completed(futs):
            name = futs[f]
            try:
                collected[name] = [r for r in (f.result() or []) if r.get('url')]
            except Exception as e:
                log.warning(f"[{name}] 并行搜索 '{query}' 失败: {e}")
    # v1.2.x 召回增强：跨引擎多样性合并（默认开）——轮询防单源垄断
    if _env_flag('INFOSEEK_RECALL_DIVERSITY', True):
        return _merge_diverse(collected, max_results, query)
    merged, seen = [], set()
    for name in sorted(collected,
                       key=lambda n: _engine_weight_for(n, query), reverse=True):
        for r in collected[name]:
            if r['url'] not in seen:
                seen.add(r['url'])
                merged.append(r)
    return merged[:max_results]


def _min_expected(max_results: int) -> int:
    """质量门控阈值：结果不足则触发保留引擎兜底（默认对齐 industry≥3）。"""
    v = os.environ.get('INFOSEEK_SEARCH_MIN_RESULTS', '')
    try:
        return max(1, int(v)) if v else min(max_results, 3)
    except ValueError:
        return min(max_results, 3)


def _env_flag(name: str, default: bool) -> bool:
    """v1.2.x 召回增强：env 布尔开关（'' → default；0/false/no/off → False）"""
    v = os.environ.get(name, '')
    if v == '':
        return default
    return v not in ('0', 'false', 'False', 'no', 'off')


_QUERY_TYPE_KEYWORDS = {
    'finance': ('财报', '营收', '利润', '股价', '融资', '估值', '市值', 'IPO',
                '净利', '市盈率', '回购', '分红', '现金流', '业绩'),
    'tech': ('大模型', 'AI', '芯片', '算力', 'GPU', '开源', '算法', '模型', '智能体',
             '机器人', '自动驾驶', '半导体', 'API', '云', '数据中心'),
    'sentiment': ('舆情', '争议', '调查', '处罚', '监管', '诉讼', '危机', '负面', '口碑'),
}
_TYPE_BOOST = {
    'finance': {'Zhipu': 0.2, 'Metaso': 0.15, 'TinyFish': 0.15, 'Jina-AI': 0.1},
    'tech': {'Exa': 0.2, 'Tavily': 0.2, 'DuckDuckGo-HTML': 0.05},
    'sentiment': {'Tavily': 0.15, 'Bing-RSS': 0.1, 'Jina-AI': 0.1},
}


def _query_type(query: str) -> str:
    """v1.2.x 召回增强：按关键字启发式分类 query（finance/tech/sentiment/general）。"""
    q = query.lower()
    best, score = 'general', 0
    for t, kws in _QUERY_TYPE_KEYWORDS.items():
        s = sum(1 for k in kws if k.lower() in q)
        if s > score:
            best, score = t, s
    return best


def _engine_weight_for(name: str, query: str) -> float:
    """v1.2.x 召回增强：动态层权重（INFOSEEK_RECALL_DYN_WEIGHT=1 时按 query 类型加成）。"""
    w = _ENGINE_WEIGHT.get(name, 0.5)
    if not _env_flag('INFOSEEK_RECALL_DYN_WEIGHT', False):
        return w
    return w + _TYPE_BOOST.get(_query_type(query), {}).get(name, 0)


def _expand_query(query: str) -> str:
    """v1.2.x 召回增强：query 扩展（INFOSEEK_RECALL_EXPAND=1 默认开）。

    识别 query 中出现的已知实体（name/aliases 命中），追加其别名（≤3 个）
    以提升跨名召回（如「比亚迪」→ 补充「BYD 比亚迪股份」）。
    无命中/异常 → 原样返回；引入的噪声由 _filter_relevant 相关性门控兜底。
    """
    try:
        import sys as _s
        from pathlib import Path as _P
        _root = _P(__file__).parent.parent
        if str(_root) not in _s.path:
            _s.path.insert(0, str(_root))
        from core.entities import get_all_entities
        ql = query.lower()
        hits = []
        for e in get_all_entities():
            names = [e.get('name', '')] + list(e.get('aliases', []) or [])
            if any(n and str(n).lower() in ql for n in names):
                hits.append(e)
        extra = []
        for e in hits:
            for a in (e.get('aliases', []) or []):
                if a and a.lower() not in ql and a not in extra:
                    extra.append(a)
                if len(extra) >= 3:
                    break
            if len(extra) >= 3:
                break
        if extra:
            log.info(f"[recall] query 扩展 '{query}' → +{extra}")
            return (query + ' ' + ' '.join(extra)).strip()
        return query
    except Exception:
        return query


def _merge_diverse(collected: dict, max_results: int, query: str) -> list:
    """v1.2.x 召回增强：跨引擎多样性合并（INFOSEEK_RECALL_DIVERSITY=1 默认开）。

    按动态权重排序引擎 → 轮询逐引擎取 1 条 → 直到 top-N。
    避免单一引擎（如全来自 Bing RSS）垄断结果；结果浅拷贝附 engine 标签。
    """
    order = sorted(collected, key=lambda n: _engine_weight_for(n, query), reverse=True)
    queues = {n: [dict(r) for r in collected[n]] for n in order}
    merged, seen = [], set()
    while len(merged) < max_results:
        advanced = False
        for n in order:
            q = queues[n]
            while q:
                r = q.pop(0)
                if r.get('url') and r['url'] not in seen:
                    seen.add(r['url'])
                    r['engine'] = n
                    merged.append(r)
                    advanced = True
                    break
            if len(merged) >= max_results:
                break
        if not advanced:
            break
    return merged


def _filter_relevant(results: list, query: str, min_score: int = 12) -> list:
    """主题相关性过滤（v1.0.1 PATCH / P1-2）

    两层判定：
    1. 语义分阈值：title+snippet 与 query 的 Jaccard 相似度 ≥ min_score
    2. 多字词硬门槛（v1.0.1b PATCH / P2-1）：query 含中文时，
       用 jieba 提取 query 多字词（≥2 字），要求结果文本至少命中 1 个——
       杜绝「新能源汽车」误匹配「新（汉语汉字）」这类单字噪音。

    若过滤后结果不足 min_expected 则保留原列表（避免过度过滤导致空结果）。
    返回结果附加 relevance 字段（0-100 语义相似分）。
    """
    if not results:
        return results
    # v1.2.x 召回增强：自适应门槛（INFOSEEK_RECALL_ADAPTIVE=1 默认开）
    #   候选少（<6）→ 门槛 10 保召回；候选多（>20）→ 门槛 14 滤噪；否则 12。
    if _env_flag('INFOSEEK_RECALL_ADAPTIVE', True):
        n = len(results)
        min_score = 14 if n > 20 else (10 if n < 6 else 12)
    try:
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).parent.parent / 'scripts'))
        from anchor_adapter import compute_semantic_similarity

        # 多字词硬门槛（仅中文 query 启用）
        hard_words = set()
        if _re.search(r'[\u4e00-\u9fff]', query):
            try:
                import jieba
                hard_words = {w.strip().lower() for w in jieba.lcut(query)
                              if len(w.strip()) >= 2}
            except Exception:
                hard_words = set()

        kept = []
        for r in results:
            text = ' '.join(filter(None, [r.get('title', ''), r.get('snippet', '')]))
            score = compute_semantic_similarity(text, query)
            if score < min_score:
                continue
            if hard_words:
                text_lower = text.lower()
                if not any(w in text_lower for w in hard_words):
                    continue  # 多字词无交集 → 单字/噪音匹配，剔除
            r['relevance'] = score
            kept.append(r)
        if len(kept) >= _min_expected(max(3, len(results))):
            log.info(f"[relevance] '{query}' 过滤 {len(results)}→{len(kept)} 条")
            return kept
        return results
    except Exception:
        return results


def _reserve_pool(ai_mode: bool, engines: list) -> list:
    """保留池（层内冗余）：
      - INFOSEEK_SEARCH_RESERVED=<a[,b]> 固定保留（支持双保留 opt-in）
      - INFOSEEK_RESERVE_QUOTA=0 关闭配额保护（全池轮换）
      - 默认模式 → 限量引擎（配额保护：免费覆盖日常，限量引擎兜底）
      - AI 模式 → 免费引擎（AI 为主层，免费引擎兜底零成本）
    """
    fixed = os.environ.get('INFOSEEK_SEARCH_RESERVED', '')
    if fixed:
        names = {x.strip() for x in fixed.split(',') if x.strip()}
        return [(n, f) for n, f in engines if n in names]
    if os.environ.get('INFOSEEK_RESERVE_QUOTA') == '0':
        return list(engines)
    if ai_mode:
        return _free_engines()
    return _quota_engines_with_key()


def _parallel_merge_with_reserve(engines: list, query: str, max_results: int,
                                 reserve_pool: list) -> list:
    """层内并用 + 动态保留（层内冗余）：

      1. 主并行：除保留引擎外的全部引擎（md5 轮换选保留者，无状态可复现）
      2. 质量门控：并行结果 < min_expected 时触发保留引擎兜底（可双保留）
      3. 保留补充结果追加尾部（补充语义，不抢占），返回 top-N
    """
    if not reserve_pool:
        return _parallel_merge(engines, query, max_results)
    import hashlib
    idx = int(hashlib.md5(query.encode('utf-8')).hexdigest(), 16) % len(reserve_pool)
    reserved = [reserve_pool[idx]]
    reserved_names = {n for n, _ in reserved}
    main = [e for e in engines if e[0] not in reserved_names]
    got = _parallel_merge(main, query, max_results)
    if len(got) < _min_expected(max_results):
        got = [dict(r) for r in got]
        seen = {r['url'] for r in got}
        for rname, rfn in reserved:
            try:
                time.sleep(0.8)
                for r in (_call_engine(rname, rfn, query, max_results) or []):
                    if r.get('url') and r['url'] not in seen:
                        seen.add(r['url'])
                        got.append(r)
                log.info(f"[{rname}:reserved] '{query}' 兜底补充 → {len(got)} 条")
            except Exception as e:
                log.warning(f"[{rname}:reserved] '{query}' 兜底失败: {e}")
    return got[:max_results]


def _search_web_serial(query: str, max_results: int) -> list:
    """顺序降级（INFOSEEK_SEARCH_PARALLEL=0 回退；保留原语义）。"""
    engines = _default_layer()
    if os.environ.get('INFOSEEK_SEARCH_ENGINE', 'auto') == 'ai' and _has_ai_key():
        for name, fn in get_lifecycle().get_active(_ai_engines()):
            try:
                time.sleep(0.8)
                results = _call_engine(name, fn, query, max_results)
                if results:
                    return results[:max_results]
            except Exception as e:
                log.warning(f"[{name}] 搜索 '{query}' 失败: {e}")
    for name, fn in get_lifecycle().get_active(engines):
        try:
            time.sleep(0.8)
            results = _call_engine(name, fn, query, max_results)
            if results:
                return results[:max_results]
        except Exception as e:
            log.warning(f"[{name}] 搜索 '{query}' 失败: {e}")
    return []


def search_web(query: str, max_results: int = 10) -> list:
    """搜索降级链（v1.1.0 并行化）：

    - **层内并用**：每层引擎并行调用（ThreadPoolExecutor ≤4）+ url 去重
      + 组间权重/组内保序 → top-N；单引擎失败互不拖累。
    - **层间降级**：AI 键控层（=ai 且有 key）→ 默认层（免费 + CN opt-in）。
    - **动态保留**（层内冗余）：每查询 md5 轮换保留 1 个引擎不参与主并行，
      并行结果不足 min_expected 时触发兜底；默认模式保留池=限量引擎
      （配额保护），AI 模式保留池=免费引擎。
    - 回退：INFOSEEK_SEARCH_PARALLEL=0 → 顺序模式（原语义）。

    返回 [{"url","title","snippet"},...]；全链失败返回 []（不伪造）。
    """
    # v1.2.x 召回增强：query 扩展（默认开）——别名扩展提升跨名召回
    if _env_flag('INFOSEEK_RECALL_EXPAND', True):
        query = _expand_query(query)
    if os.environ.get('INFOSEEK_SEARCH_PARALLEL', '1') == '0':
        return _filter_relevant(_search_web_serial(query, max_results), query)
    ai_mode = os.environ.get('INFOSEEK_SEARCH_ENGINE', 'auto') == 'ai'
    time.sleep(0.8)  # 层间限速
    if ai_mode and _has_ai_key():
        # AI 模式：AI 引擎（权重高）+ 免费引擎全并行，免费引擎为保留池
        ai_layer = _ai_engines() + _free_engines()
        got = _parallel_merge_with_reserve(ai_layer, query, max_results,
                                           get_lifecycle().get_active(
                                               _reserve_pool(True, ai_layer)))
        if got:
            log.info(f"[AI-layer] '{query}' → {len(got)} 条（并行合并）")
            return _filter_relevant(got, query)
        log.warning("[AI-layer] 结果为空，回退默认层")
        time.sleep(0.8)
    # 默认层：免费并行 + 限量引擎保留池（配额保护）
    default_layer = _default_layer()
    got = _parallel_merge_with_reserve(default_layer, query, max_results,
                                       get_lifecycle().get_active(
                                           _reserve_pool(False, default_layer)))
    if got:
        log.info(f"[default-layer] '{query}' → {len(got)} 条（并行合并）")
        return _filter_relevant(got, query)
    log.warning(f"搜索降级链全失败: '{query}'")
    return []


def industry_to_anchors(industry: str, min_anchors: int = 3) -> list:
    """
    从行业名称自动生成锚点清单（替代 infos 的手动嗅探步骤）
    使用 web search 搜素行业关键词，收敛为锚点列表。

    v1.0.0：删除静默演示锚点；结果低于 min_anchors 时显式失败（返回 []），
    由调用方（KB 兜底 / run_pipeline 覆盖率门控）决定是否继续。

    输入: "量化交易"
    输出: [{name, platform, score, entry, entry_type}, ...]
    """
    log.info(f"行业嗅探: {industry}")
    search_terms = [
        industry,
        f"{industry} 2026 最新",
        f"{industry} 文章 教程",
    ]

    anchors = []
    seen_urls = set()
    for term in search_terms:
        for hit in search_web(term, max_results=10):
            url = hit["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            anchors.append({
                "name": hit["title"][:80] if hit["title"] else industry,
                "platform": "web", "score": 70,
                "entry": url, "entry_type": "URL"})
        if len(anchors) >= min_anchors * 2:  # 提前收敛
            break

    # 覆盖率门控（v1.0.0）：不再返回伪完整结果
    if len(anchors) < min_anchors:
        log.error(
            f"行业嗅探覆盖率不足: 仅 {len(anchors)} 个锚点（要求 ≥ {min_anchors}）。"
            f"不返回演示锚点，由调用方兜底。")
        return []

    log.info(f"行业嗅探完成: {len(anchors)} 个锚点")
    return anchors


# ═══════════════════════════════════════════════════════════════
# 阶段 0.5: 名称类锚点→URL自动搜索（新增, P0-B）
# ═══════════════════════════════════════════════════════════════

def search_name_to_url(name: str, platform: str = "", min_results: int = 2) -> list:
    """
    将名称/频道名类锚点通过 web search 转换为 URL 列表。
    v1.0.0：改用 search_web 降级链（DDG HTML → Bing RSS → Wikipedia）；
    结果低于 min_results 时显式返回 []（覆盖率门控，不再静默返回单条假结果）。
    输入: "丁鹏", platform="综合"
    输出: [{url, title, score}, ...]
    """
    results = []
    search_queries = [name]

    # 按平台构造更精准的搜素词
    platform_lower = platform.lower()
    if "b站" in platform_lower or "bilibili" in platform_lower:
        search_queries.append(f"{name} B站 UP主")
    elif "公众号" in platform_lower or "微信" in platform_lower:
        search_queries.append(f"{name} 公众号")
    elif "知乎" in platform_lower:
        search_queries.append(f"{name} 知乎")
    else:
        search_queries.append(f"{name} 文章")
        search_queries.append(f"{name} 主页")

    seen = set()
    for query in search_queries[:2]:  # 最多 2 轮搜索
        for hit in search_web(query, max_results=8):
            url = hit["url"]
            if url in seen:
                continue
            seen.add(url)
            results.append({"url": url, "title": hit["title"][:80], "score": 65})
        if len(results) >= min_results:
            break

    if len(results) < min_results:
        log.warning(
            f"名称搜索 '{name}' 覆盖率不足: 仅 {len(results)} 条（要求 ≥ {min_results}）。"
            f"显式返回空列表。")
        return []
    return results


# ═══════════════════════════════════════════════════════════════
# 阶段 1: 输入契约验证
# ═══════════════════════════════════════════════════════════════

def validate_anchor(anchor: dict) -> tuple:
    """锚点字段完整性校验"""
    required = ['platform', 'type', 'entry', 'entry_type']
    missing = [k for k in required if not anchor.get(k)]
    if missing:
        return False, f"字段缺失: {', '.join(missing)}"
    if anchor.get('entry_type') == 'URL' and anchor.get('entry'):
        from urllib.parse import urlparse
        parsed = urlparse(anchor['entry'])
        if not parsed.scheme or not parsed.netloc:
            return False, f"无效URL: {anchor['entry']}"
    return True, "OK"


# ═══════════════════════════════════════════════════════════════
# 阶段 2: URL 预检
# ═══════════════════════════════════════════════════════════════

def url_validate(url: str) -> tuple:
    """URL 存活预检"""
    from urllib.parse import urlparse
    import socket

    if not url or not isinstance(url, str):
        return False, "URL为空", None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False, f"格式无效: {url[:60]}", None
    if parsed.scheme not in ('http', 'https'):
        return False, f"不支持的协议: {parsed.scheme}", None

    try:
        socket.getaddrinfo(parsed.netloc, 80, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"域名不可解析: {parsed.netloc}", None

    import urllib.request
    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; infoseek/1.0)')
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status >= 400:
            return False, f"HTTP {resp.status}", resp.status
        return True, "OK", resp.status
    except urllib.error.HTTPError as e:
        if e.code == 429:
            log.warning(f"URL 预检 → 429 限流，熔断跳过")
            return True, "跳过(429限流)", 429  # 熔断放行
        return False, f"HTTP {e.code}", e.code
    except Exception as e:
        return True, f"跳过({str(e)[:50]})", None


# ═══════════════════════════════════════════════════════════════
# 阶段 3: 三级降级 + 自动路由
# ═══════════════════════════════════════════════════════════════

def degradation_router(url: str, tier1_result: dict = None,
                       tier2_result: dict = None) -> dict:
    """降级路由状态机"""
    if not url or not isinstance(url, str) or len(url.strip()) < 5:
        return {'action': 'final', 'reason': 'URL为空或格式错误'}

    if tier1_result is not None:
        title = (tier1_result.get('title') or '').strip()
        text = (tier1_result.get('text') or '').strip()
        status = tier1_result.get('status', 0)
        err = tier1_result.get('error', '')

        if status in (404, 410):
            return {'action': 'final', 'reason': f'HTTP {status} 内容不存在'}
        if status == 403 or 'cloudflare' in err.lower() or 'cf_' in err.lower():
            return {'action': 'tier2', 'reason': '反爬拦截'}
        if not title and not text:
            return {'action': 'tier2', 'reason': 'JS渲染/SPA页面'}
        if title and not text:
            return {'action': 'tier2', 'reason': '仅有标题无正文'}
        if title and len(text) > 100:
            return {'action': 'done', 'reason': 'Tier 1 采集成功'}
        if title and len(text) < 100:
            return {'action': 'tier2', 'reason': f'正文过短({len(text)}字)'}
        return {'action': 'tier2', 'reason': 'Tier 1结果异常'}

    if tier2_result is not None:
        title = (tier2_result.get('title') or '').strip()
        text = (tier2_result.get('text') or '').strip()
        ct = tier2_result.get('content_type', '')
        if ct in ('video', 'audio', 'live'):
            return {'action': 'tier3', 'reason': f'媒体类型: {ct}'}
        if title and len(text) > 50:
            return {'action': 'done', 'reason': 'Tier 2 采集成功'}
        return {'action': 'tier3', 'reason': 'Tier 2提取不完整'}

    return {'action': 'tier1', 'reason': '初始状态'}


# ═══════════════════════════════════════════════════════════════
# 阶段 3.3: 凭证降级层（新增, Tier2.5 — 用户控制+不存储）
# ═══════════════════════════════════════════════════════════════

CREDENTIAL_TOOLS = {
    "firecrawl": {
        "name": "Firecrawl API",
        "cost": "💰免费层(1000页/月)",
        "credential_type": "API Key",
        "endpoint": "https://api.firecrawl.dev/v1/scrape",
        "how_to": "用户输入 API Key → Firecrawl.scrape(url) → 返回Markdown",
        "session_only": True
    },
    "jina_reader": {
        "name": "Jina Reader API",
        "cost": "💰免费层",
        "credential_type": "API Key",
        "endpoint": "https://r.jina.ai/http://<url>",
        "how_to": "用户输入 API Key → Jina Reader 提取 → 返回结构化内容",
        "session_only": True
    },
    "wechat_exporter": {
        "name": "wechat-article-exporter",
        "cost": "💰免费",
        "credential_type": "浏览器扫码",
        "how_to": "启动本地Web界面(docker) → 用户微信扫码 → 选择文章导出",
        "session_only": True
    },
}


def request_credential(anchor_name: str, url: str = "", tier1_reason: str = "") -> dict:
    """
    Tier 2.5 凭证降级请求 — 输出操作界面模板，**不自动执行，不保存凭证**。
    
    返回: {
        'action': 'credential_needed' | 'skip_to_final',
        'message': str,          # 给用户的操作指引
        'options': list,         # 可选工具列表
    }
    """
    options = []
    for key, tool in CREDENTIAL_TOOLS.items():
        options.append({
            "id": key,
            "name": tool["name"],
            "cost": tool["cost"],
            "credential_type": tool["credential_type"],
            "how_to": tool["how_to"],
            "session_only": tool["session_only"]
        })

    return {
        "action": "credential_needed",
        "message": (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 免费工具已耗尽: {anchor_name}\n"
            f"   原因: {tier1_reason or 'Tier1+Tier2均失败'}\n"
            f"   以下备选需您提供凭证(不保存, 仅本次会话):\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ),
        "options": options,
        "credential_policy": "SESSION_ONLY — 凭证仅在当前会话内存中使用，不写入磁盘"
    }


# ═══════════════════════════════════════════════════════════════
# 阶段 3.5: Tier2/Tier3 执行函数壳（新增, C1）
# ═══════════════════════════════════════════════════════════════

def _tier2_execute(url: str, tier1_result: dict) -> dict:
    """
    Tier 2 执行: 反爬/JS渲染/平台封闭场景
    当前为函数壳 — 返回空文本以触发凭证降级层(Tier2.5)。
    v1.2.0 将集成实际工具调用。
    """
    log.info(f"Tier2 需要人工降级 ({url[:60]}...) — 指令级，需集成 Scrapling/crawl4ai")
    return {
        "action": "tier2_stub",
        "reason": "Tier2 指令壳 — 需实际工具或凭证降级",
        "title": "",
        "text": "",  # 空文本 → 触发凭证降级层
        "status": 0
    }


def _tier3_execute(url: str) -> dict:
    """
    Tier 3 执行: 视频/多媒体下载+三源降级
    当前为函数壳 — 输出指令级指引。v1.2.0 将集成 yt-dlp/ASR/OCR。
    """
    log.info(f"Tier3 执行 ({url[:60]}...) — 指令级，需集成 yt-dlp/ASR/OCR")
    return {
        "action": "final",
        "reason": "Tier3 指令执行完成（v1.2.0 将替换为实际调用）",
        "title": "",
        "text": f"[Tier3 指令模式] 需人工执行: yt-dlp --write-subs --sub-langs all '{url}'",
        "status": 0
    }


# ═══════════════════════════════════════════════════════════════
# 阶段 4: 治理反馈生成（新增, P1-D）
# ═══════════════════════════════════════════════════════════════

def generate_feedback(details: list) -> list:
    """从失败结果生成锚点降级建议"""
    feedbacks = []
    for r in details:
        status = r.get("status")
        if status in ("dead_link", "failed", "needs_tier2"):
            anchor = r.get("anchor", {})
            penalty = -20 if status == "dead_link" else -10
            feedbacks.append({
                "anchor_name": anchor.get("name", "?"),
                "anchor_platform": anchor.get("platform", "?"),
                "anchor_entry": anchor.get("entry", "?"),
                "original_score": anchor.get("score", 0),
                "failure_type": status,
                "failure_reason": (r.get("steps", [{}])[-1].get("reason", "")) if r.get("steps") else "",
                "suggested_penalty": penalty,
                "suggested_new_score": max(0, (anchor.get("score", 0) or 0) + penalty)
            })
    return feedbacks


# ═══════════════════════════════════════════════════════════════
# 阶段 5: 执行一个锚点的完整采集
# ═══════════════════════════════════════════════════════════════

def execute_anchor(anchor: dict, output_dir: str) -> dict:
    """对单个锚点执行完整 infoseek 流水线（含异常保护）"""
    start_time = time.time()
    result = {
        "anchor": anchor,
        "status": "pending",
        "steps": [],
        "output": None,
        "elapsed_s": 0,
        "errors": []
    }

    try:
        # 1. 锚点适配
        seek_card = infos_to_seek(anchor)
        if seek_card is None:
            result["status"] = "skipped"
            result["steps"].append({"step": "anchor_adapter", "status": "skip", "reason": "score<40"})
            result["elapsed_s"] = time.time() - start_time
            return result
        result["steps"].append({"step": "anchor_adapter", "status": "ok", "card": seek_card})

        # 2. 输入契约验证
        valid, reason = validate_anchor(seek_card)
        if not valid:
            result["status"] = "failed"
            result["steps"].append({"step": "validate", "status": "fail", "reason": reason})
            result["elapsed_s"] = time.time() - start_time
            return result
        result["steps"].append({"step": "validate", "status": "ok"})

        # 3-5. 按 entry_type 分支处理
        entry_type = seek_card.get("entry_type", "")
        entry = seek_card.get("entry", "")

        # ─── URL 类路径 ───
        if entry_type == "URL" and entry:
            url = entry
            # URL 预检
            valid_url, url_reason, status_code = url_validate(url)
            if not valid_url:
                result["status"] = "dead_link"
                result["steps"].append({"step": "url_validate", "status": "fail",
                                         "reason": url_reason, "http_status": status_code})
                result["elapsed_s"] = time.time() - start_time
                return result
            result["steps"].append({"step": "url_validate", "status": "ok"})

            # Tier 1 提取
            tier1_result = {"title": "", "text": "", "status": 0, "error": ""}
            try:
                from newspaper import Article
                a = Article(url)
                a.download()
                a.parse()
                tier1_result = {"title": a.title or "", "text": a.text or "",
                                "status": 200, "error": ""}
            except Exception as e:
                tier1_result = {"title": "", "text": "", "status": 0, "error": str(e)}

            # 自动路由
            decision = degradation_router(url, tier1_result=tier1_result)
            result["steps"].append({"step": "tier1", "status": "ok" if decision["action"] == "done" else "partial",
                                     "decision": decision})

            if decision["action"] in ("tier2", "tier3"):
                result["steps"].append({"step": "tier2_needed", "reason": decision["reason"]})
                # C1: 调用 Tier2/Tier3 函数壳（v1.2.0 将替换为实际工具调用）
                if decision["action"] == "tier2":
                    t2_result = _tier2_execute(url, tier1_result)
                    result["steps"].append({"step": "tier2_exec", "status": "stub",
                                             "output": t2_result["text"][:100]})
                    # Tier 2 仍失败 → 提示用户是否使用凭证降级
                    if not t2_result.get("text"):
                        cred = request_credential(
                            anchor.get("name", "?"), url, decision["reason"])
                        result["steps"].append({"step": "credential_offer",
                                                 "options": [o["name"] for o in cred["options"]]})
                        result["credential_offer"] = cred
                        result["status"] = "needs_credential"
                elif decision["action"] == "tier3":
                    t3_result = _tier3_execute(url)
                    result["steps"].append({"step": "tier3_exec", "status": "stub",
                                             "output": t3_result["text"][:100]})
                log.warning(f"需人工介入降级 — {url[:60]} → {decision['action']}: {decision['reason']}")
                result["needs_human_intervention"] = True

            # 如果已经是 needs_credential，不再被下面覆盖
            if result.get("status") != "needs_credential":
                if tier1_result.get("text"):
                    result["status"] = "success" if decision["action"] == "done" else "partial"
                result["output"] = {
                    "title": tier1_result["title"],
                    "text_length": len(tier1_result["text"]),
                    "text_preview": tier1_result["text"][:200],
                    "source": "tier1"
                }
            else:
                if result.get("status") != "needs_credential":
                    result["status"] = "needs_tier2"

        # ─── 名称/频道名类路径（新增, P0-B）───
        elif entry_type in ("名称", "频道名"):
            name = entry
            platform = seek_card.get("platform", "综合")
            result["steps"].append({"step": "search_needed", "entry": name, "platform": platform})

            # 自动搜索 → 转URL
            search_results = search_name_to_url(name, platform)
            if search_results:
                result["steps"].append({"step": "name_search", "status": "ok",
                                         "found": len(search_results),
                                         "results": search_results[:5]})
                # 对第一个搜索结果执行 URL 提取
                first = search_results[0]
                result["steps"].append({"step": "name_to_url", "url": first["url"]})

                # 递归执行 URL 提取
                sub_anchor = {"name": anchor.get("name", name), "platform": platform,
                              "score": anchor.get("score", 70), "entry": first["url"],
                              "entry_type": "URL"}
                sub_result = execute_anchor(sub_anchor, output_dir)
                result["status"] = sub_result.get("status", "failed")
                result["output"] = sub_result.get("output")
                result["steps"].extend(sub_result.get("steps", []))
            else:
                result["status"] = "needs_search"
                result["steps"].append({"step": "name_search", "status": "fail",
                                         "reason": "未找到相关URL"})

        else:
            result["status"] = "unknown_type"

    except Exception as e:
        # 全局异常保护（P2-F）
        result["status"] = "error"
        result["errors"].append({"step": "execute_anchor", "error": str(e)})
        log.error(f"锚点处理异常: {anchor.get('name','?')}: {e}")

    result["elapsed_s"] = round(time.time() - start_time, 2)
    return result


# ═══════════════════════════════════════════════════════════════
# 入口：批量执行
# ═══════════════════════════════════════════════════════════════

def run_pipeline(anchors: list, output_dir: str = "./outputs", min_anchors: int = 0) -> dict:
    """批量执行锚点采集

    v1.0.0：新增覆盖率门控——anchors 数量低于 min_anchors 时直接产出
    显式失败报告（status=insufficient_coverage），不执行采集、不产出伪完整报告。
    min_anchors=0 表示不启用门控（手动 --anchors 指定场景）。
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 覆盖率门控（v1.0.0）
    if min_anchors > 0 and len(anchors) < min_anchors:
        log.error(f"覆盖率门控: 锚点数 {len(anchors)} < 要求 {min_anchors}，拒绝执行采集")
        report = {
            "pipeline": "infoseek",
            "version": "1.0.0",
            "timestamp": timestamp,
            "status": "insufficient_coverage",
            "coverage": {"anchors": len(anchors), "min_anchors": min_anchors},
            "stats": {"total": len(anchors), "success": 0, "failed": 0,
                      "error": 1, "total_elapsed_s": 0},
            "details": [],
            "feedback": [{
                "type": "coverage_gate",
                "severity": "error",
                "message": f"锚点数不足（{len(anchors)} < {min_anchors}），未执行采集。"
                           f"请检查搜索后端，或改用 --anchors 手动指定。",
            }],
            "output_dir": output_dir,
        }
        report_path = os.path.join(output_dir, f"infoseek_report_{timestamp}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info(f"失败报告已保存: {report_path}")
        return report

    all_results = []
    for i, anchor in enumerate(anchors):
        log.info(f"[{i+1}/{len(anchors)}] 处理锚点: {anchor.get('name','?')}")
        result = execute_anchor(anchor, output_dir)
        all_results.append(result)
        log.info(f"  → 状态: {result['status']} ({result['elapsed_s']}s)")

    # 聚合统计
    stats = {
        "total": len(anchors),
        "success": sum(1 for r in all_results if r["status"] == "success"),
        "partial": sum(1 for r in all_results if r["status"] in ("partial", "needs_tier2")),
        "needs_credential": sum(1 for r in all_results if r["status"] == "needs_credential"),
        "needs_search": sum(1 for r in all_results if r["status"] == "needs_search"),
        "dead_link": sum(1 for r in all_results if r["status"] == "dead_link"),
        "skipped": sum(1 for r in all_results if r["status"] == "skipped"),
        "failed": sum(1 for r in all_results if r["status"] == "failed"),
        "error": sum(1 for r in all_results if r["status"] == "error"),
        "total_elapsed_s": round(sum(r["elapsed_s"] for r in all_results), 2),
    }

    # 生成治理反馈（P1-D）
    feedbacks = generate_feedback(all_results)

    report = {
        "pipeline": "infoseek",
        "version": "1.0.0",
        "timestamp": timestamp,
        "stats": stats,
        "details": all_results,
        "feedback": feedbacks,
        "output_dir": output_dir
    }

    # 保存报告
    report_path = os.path.join(output_dir, f"infoseek_report_{timestamp}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info(f"报告已保存: {report_path}")

    # 单独保存治理反馈（P1-D）
    if feedbacks:
        fb_path = os.path.join(output_dir, f"infoseek_feedback_{timestamp}.json")
        with open(fb_path, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, ensure_ascii=False, indent=2)
        log.info(f"治理反馈已保存: {fb_path}")

    # C2: 自动应用治理反馈到本地锚点库
    applied = apply_feedback(feedbacks)
    if applied:
        log.info(f"治理反馈已自动应用: {applied} 条")

    return report


# ═══════════════════════════════════════════════════════════════
# 阶段 6: 治理反馈自动应用（新增, C2）
# ═══════════════════════════════════════════════════════════════

def apply_feedback(feedbacks: list, anchor_db_path: str = "./anchor_db.json") -> int:
    """
    将治理反馈自动应用到本地锚点库。
    若 anchor_db.json 不存在则跳过（锚点库尚未建立时静默处理）。
    返回实际更新的锚点数量。
    """
    if not feedbacks:
        return 0
    try:
        if not os.path.exists(anchor_db_path):
            # 首次运行，创建空锚点库
            with open(anchor_db_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            log.info(f"锚点库已创建: {anchor_db_path}")
            return 0

        with open(anchor_db_path, "r", encoding="utf-8") as f:
            db = json.load(f)

        updated = 0
        for fb in feedbacks:
            entry = fb.get("anchor_entry", "")
            new_score = fb.get("suggested_new_score")
            for item in db:
                if item.get("entry") == entry:
                    old_score = item.get("score", 0)
                    item["score"] = new_score
                    item["score_history"] = item.get("score_history", []) + [old_score]
                    updated += 1
                    log.info(f"  锚点降级: {item.get('name','?')} {old_score}→{new_score}")
                    break

        with open(anchor_db_path, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

        return updated
    except Exception as e:
        log.warning(f"治理反馈应用失败(可忽略): {e}")
        return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="infoseek 全链路采集管道")
    parser.add_argument("--anchors", help="锚点JSON文件路径")
    parser.add_argument("--industry", help="行业/主题名称（自动嗅探+采集）")
    parser.add_argument("--output", default="./outputs", help="输出目录")
    args = parser.parse_args()

    # P2-F: 空输入处理
    if not args.anchors and not args.industry:
        print("请提供 --anchors 或 --industry 参数")
        print("示例: python3 infoseek_pipeline.py --industry '量化交易'")
        sys.exit(1)

    # P0-A: --industry 路径
    if args.industry:
        log.info(f"infoseek v1.2.0 | 行业嗅探模式: {args.industry}")
        anchors = industry_to_anchors(args.industry)

        # ── KB 补充：用可信源兜底 ──
        try:
            from trusted_kb import kb_lookup, kb_add, kb_merge, kb_fallback, _extract_domain
            kb_hits = kb_lookup(args.industry, limit=5)
            if kb_hits:
                log.info(f"KB补充: 命中 {len(kb_hits)} 条可信源")
                merged = kb_merge(anchors, kb_hits)
                log.info(f"合并后: {len(anchors)} web + {len(kb_hits)} KB → {len(merged)} 总锚点")
                anchors = merged
            else:
                # web search 无结果时的兜底
                fb = kb_fallback(args.industry, limit=5)
                if fb and len(anchors) <= 2:
                    log.warning(f"web结果稀少({len(anchors)}条)，启用KB兜底(+{len(fb)}条)")
                    anchors = kb_merge(anchors, fb)
        except ImportError:
            log.info("trusted_kb 模块未找到，跳过KB补充")
        except Exception as e:
            log.warning(f"KB补充异常(非致命): {e}")

        # 执行管道（v1.0.0：industry 自动嗅探路径启用覆盖率门控 ≥3）
        report = run_pipeline(anchors, args.output, min_anchors=3)

        # ── 自动沉淀：采集成功的源写入KB ──
        try:
            from trusted_kb import kb_add as _kb_add
            for detail in report.get("details", []):
                if detail.get("status") == "success":
                    anchor = detail.get("anchor", {})
                    entry = anchor.get("entry", "")
                    domain_match = __import__('re').search(r"https?://([^/]+)", entry)
                    if domain_match and anchor.get("score", 0) >= 70:
                        domain = domain_match.group(1)
                        _kb_add(domain, anchor.get("name", domain),
                                [args.industry], anchor.get("credibility", 70), "web")
        except Exception as e:
            log.warning(f"KB自动沉淀异常(非致命): {e}")

    # --anchors 路径
    if args.anchors:
        with open(args.anchors) as f:
            anchors = json.load(f)
        run_pipeline(anchors, args.output)


# ═══════════════════════════════════════════════════════════════
# M0.3：身份归因阶段（可选，默认 OFF，合规 opt-in）
# 锚点矩阵"平面 B"：已知用户名 → 平台账号锚点
# 复用统一能力注册表 + 代偿层：Maigret → Sherlock → manual_review
# ═══════════════════════════════════════════════════════════════

def _ensure_cap_paths():
    """确保 core / scripts 在 sys.path（独立运行时兜底）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for p in (root, here):
        if p not in sys.path:
            sys.path.insert(0, p)


def _identity_handlers(consent: bool, max_results: int) -> dict:
    """构建能力→handler 映射（懒加载客户端，隔离重依赖）。"""
    def _maigret(u, **kw):
        from maigret_client import search as m_search
        return m_search(u, consent=consent, max_sites=500, timeout=180)

    def _sherlock(u, **kw):
        from sherlock_client import search as s_search
        return s_search(u, consent=consent, timeout=120)

    def _manual(u, **kw):
        # 优雅降级末端：返回缺口标记（非真实数据，避免静默误导）
        return [{"platform": "(需人工核实)", "url": "", "username": u,
                 "fullname": "", "site_rank": 0, "confidence": 0.0,
                 "source": "manual_review", "_gap": True}]

    return {"Maigret": _maigret, "Sherlock": _sherlock, "manual_review": _manual}


def _audit_identity(msg: str) -> None:
    """审计落盘（复用 state_dir.audit_log_path）。"""
    try:
        _ensure_cap_paths()
        from core.state_dir import audit_log_path
        p = audit_log_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [identity_attribution] {msg}\n")
    except Exception:
        pass


def search_identity_attribution(username: str, consent: bool = False,
                                 max_results: int = 10) -> list:
    """身份归因阶段（锚点矩阵平面 B）：已知用户名 → 平台账号锚点。

    双重闸口（合规优先）：
      - INFOSEEK_ENABLE_IDENTITY_ATTRIBUTION=1 显式启用
      - 注册表 Maigret/Sherlock 经 enabled ∩ consent 判定
    代偿：沿注册表 degrade_to（Maigret → Sherlock → manual_review），
          每个尝试经 engine_lifecycle 记录健康，失败自动续链。
    返回锚点条目 [{url,title,snippet,score,source,identity_attribution,confidence}]；
          缺口（全链耗尽）→ 审计标记，不包装为锚点。
    """
    if not os.environ.get("INFOSEEK_ENABLE_IDENTITY_ATTRIBUTION"):
        log.debug("[身份归因] 未启用（INFOSEEK_ENABLE_IDENTITY_ATTRIBUTION 未设），跳过")
        return []
    _ensure_cap_paths()
    from core.capability_registry import is_effective_enabled
    if not (is_effective_enabled("Maigret") or is_effective_enabled("Sherlock")):
        log.debug("[身份归因] Maigret/Sherlock 均不可用（未启用或未授权），跳过")
        return []

    handlers = _identity_handlers(consent, max_results)
    from capability_compensator import compensate, audit_trail
    res = compensate("Maigret", handlers, username, max_results=max_results)
    _audit_identity(audit_trail(res))

    if res.result is None:
        return []
    accounts = res.result if isinstance(res.result, list) else []
    if res.gap_flag:
        # 仅缺口标记，不包装为锚点（避免误导）
        log.warning(f"[身份归因] 能力链耗尽，标记需人工核实: {username}")
        return []

    anchors = []
    for acc in accounts[:max_results]:
        conf = float(acc.get("confidence") or 0)
        anchors.append({
            "url": acc.get("url") or "",
            "title": acc.get("platform") or acc.get("source") or "未知平台",
            "snippet": f"{acc.get('username') or username} @ {acc.get('platform','')}"
                       + (f" ({acc.get('fullname')})" if acc.get("fullname") else ""),
            "score": int(conf * 100),
            "source": acc.get("source", "Maigret"),
            "identity_attribution": True,
            "confidence": conf,
        })
    log.info(f"[身份归因] '{username}' → {len(anchors)} 个账号锚点（via {res.used}）")
    return anchors
