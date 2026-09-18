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
import json, os, sys, time, logging, threading
import numpy as np
from datetime import datetime

# v1.0.1 评估升级：搜索引擎全生命周期管理（健康状态机 + 配额动态追踪）
try:
    from engine_lifecycle import get_lifecycle
    from engine_router import route_engines
except ImportError:  # 独立运行回退
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine_lifecycle import get_lifecycle
    from engine_router import route_engines

from platform_adapter import platform_engines, platform_weight

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# 导入单一真源模块
from anchor_adapter import infos_to_seek
# v1.8.4：原 _filter_relevant 函数体内 996-999「每次调用 sys.path.insert + 局部 import」
# 已收口为顶层单次导入（消除 sys.path 单调膨胀 → import find_spec O(n²)，同 GA8 根因）。
# ⚠️ 必须以**模块对象**方式引用（晚绑定），不可用 `from anchor_adapter import
# compute_semantic_similarity`：from-import 会在导入期固化函数对象引用，使既有测试对
# anchor_adapter 模块属性的 monkey-patch / mock.patch 全部失效（实测击穿 3 套件 9 项：
# test_p1p3p2_fixes / test_recall_enhance_v101 / test_relevance_gate_v177）。
# 属性访问在调用时求值 → patch 生效；守护见 tests/test_ga5_tokenizer_v184.py G13/G23。
import anchor_adapter as _anchor_mod
# GA5 分词单源化（v1.8.4）：唯一分词真源
from text_tokenizer import tokenize_text
# v1.9.0 GA9/GA10：人名消歧 + 跨语言别名桥接真源（core/ 下模块，顶层模块名导入）。
# ⚠️ 必须模块对象方式引用（晚绑定），禁 from-import —— 与 _anchor_mod 同一铁律
# （§8.13.3：from-import 早绑定击穿 mock/monkey-patch 契约；守护见 GA9/GA10 测试）。
# sys.path 保障为模块导入期一次（幂等判重，GA8 纪律：禁止调用期 insert）。
_XLING_CORE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
if _XLING_CORE_DIR not in sys.path:
    sys.path.insert(0, _XLING_CORE_DIR)
import xling_bridge as _xling_mod
import person_ner as _person_mod


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
    ai = [
        ("Exa", _search_exa),
        ("Tavily", _search_tavily),
        ("Zhipu", _search_zhipu),
        ("Metaso", _search_metaso),
        ("TinyFish", _search_tinyfish),
        ("QVeris", _search_qveris),
    ]
    return route_engines(ai)


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
    'TinyFish': 0.7, 'CN-AI-Web': 0.3, 'WorkBuddy-WebSearch': platform_weight(),
}

def _free_engines() -> list:
    """免费引擎（无限量；默认层主力）。运行时构建（支持测试 monkeypatch）。"""
    free = [
        ("DuckDuckGo-HTML", _search_duckduckgo_html),
        ("Bing-RSS", _search_bing_rss),
        ("Jina-AI", _search_jina),
        ("Wikipedia", _search_wikipedia),
    ]
    return route_engines(free)

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
    return route_engines(_free_engines() + [("CN-AI-Web", _search_cn_web)] + platform_engines())  # 平台 adapter（探测失败 → []）


# ─── v1.7.7 包D：引擎可观测（本轮状态快照）───
_ENGINE_STATS = {}            # name -> {'ok','empty','fail','skip','err'}
_ENGINE_STATS_LOCK = threading.Lock()


def _stat(name: str, key: str, err: object = None) -> None:
    with _ENGINE_STATS_LOCK:
        st = _ENGINE_STATS.setdefault(
            name, {'ok': 0, 'empty': 0, 'fail': 0, 'skip': 0, 'err': ''})
        st[key] = st.get(key, 0) + 1
        if err:
            st['err'] = str(err)[:80]


def engine_stats_snapshot(reset: bool = True) -> dict:
    """本轮引擎状态快照（包D）。reset=True 取后清空。"""
    with _ENGINE_STATS_LOCK:
        snap = {k: dict(v) for k, v in _ENGINE_STATS.items()}
        if reset:
            _ENGINE_STATS.clear()
    return snap


def _log_engine_stats(query: str) -> None:
    """输出本轮引擎状态汇总（INFOSEEK_ENGINE_STATS=0 关闭）。"""
    if os.environ.get('INFOSEEK_ENGINE_STATS', '1') in ('0', 'false', 'False', 'no', 'off'):
        return
    snap = engine_stats_snapshot()
    if not snap:
        return
    parts = []
    for n, st in sorted(snap.items()):
        tag = ('ok' if st['ok'] else ('empty' if st['empty'] else
                                     ('skip' if st['skip'] else 'fail')))
        extra = f",err={st['err']}" if tag == 'fail' and st['err'] else ''
        parts.append(f"{n}:{tag}(ok={st['ok']},empty={st['empty']},fail={st['fail']}{extra})")
    log.info(f"[engine-stats] '{query}' " + '; '.join(parts))


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
        _stat(name, 'skip')
        return []
    try:
        res = fn(query, max_results)
        lc.record_success(name, res)  # P3.3 传入响应做 API 漂移检测（默认关闭）
        out = [r for r in (res or []) if r.get('url')]
        _stat(name, 'ok' if out else 'empty')
        return out
    except Exception as e:
        lc.record_failure(name, e)
        _stat(name, 'fail', err=e)
        log.warning(f"[{name}] 搜索 '{query}' 失败: {e}")
        return []


# ─── v1.x（G5）：搜索层并发控制 —— 常驻池 + 聚合窗口 ───
_POOL = None

# ─── v1.6.1（G5 P1/P2）：超窗计数降级 + 提前收敛 + 层间共享预算 ───
_overrun_count = {}       # 引擎名 -> 连续超窗次数
_muted_until = {}         # 引擎名 -> 降权静默截止（time.monotonic 绝对时间）
_OVERRUN_LOCK = threading.Lock()


def _early_factor() -> float:
    """提前收敛倍数（env INFOSEEK_SEARCH_EARLY_FACTOR，默认 1.5；0 = 关闭）。

    已完成引擎的去重合并结果 ≥ max_results×factor 时提前返回，不等满窗口。
    """
    try:
        v = float(os.environ.get('INFOSEEK_SEARCH_EARLY_FACTOR', '1.5') or 1.5)
        return max(0.0, v)
    except (TypeError, ValueError):
        return 1.5


def _shared_budget() -> bool:
    """层间共享总预算开关（env INFOSEEK_SEARCH_SHARED_BUDGET，默认 1 开）。"""
    return _env_flag('INFOSEEK_SEARCH_SHARED_BUDGET', True)


def _total_budget_s() -> float:
    """层间共享总预算秒（env INFOSEEK_SEARCH_TOTAL_BUDGET_MS，默认 = WINDOW_MS）。"""
    try:
        raw = os.environ.get('INFOSEEK_SEARCH_TOTAL_BUDGET_MS', '')
        if raw != '':
            return max(0.0, float(raw)) / 1000.0
    except (TypeError, ValueError):
        pass
    return _window_s()


def _overrun_limit() -> int:
    """连续超窗降级阈值（env INFOSEEK_SEARCH_OVERRUN_LIMIT，默认 2；0 = 关闭降级）。"""
    try:
        return max(0, int(os.environ.get('INFOSEEK_SEARCH_OVERRUN_LIMIT', '2') or 2))
    except (TypeError, ValueError):
        return 2


def _overrun_mute_s() -> float:
    """降权冷却秒（env INFOSEEK_SEARCH_OVERRUN_MUTE_S，默认 60；0 = 静默到进程结束）。"""
    try:
        return max(0.0, float(os.environ.get('INFOSEEK_SEARCH_OVERRUN_MUTE_S', '60') or 60))
    except (TypeError, ValueError):
        return 60.0


def _note_overruns(names) -> None:
    """窗口到期仍未完成的引擎 → 连续超窗计数；达阈值 → 临时降权（mute）。

    计数在触发 mute 后清零（冷却结束可重新积累）；限次后冷却期内 _filter_muted
    不再拉起该引擎（健康记录仍由线程内 _call_engine 正常完成）。
    """
    lim = _overrun_limit()
    if lim <= 0:
        return
    now = time.monotonic()
    mute_s = _overrun_mute_s()
    with _OVERRUN_LOCK:
        for n in names:
            c = _overrun_count.get(n, 0) + 1
            if c >= lim:
                _overrun_count[n] = 0
                _muted_until[n] = now + mute_s
                log.warning(f"[overrun] 引擎 '{n}' 连续 {lim} 次超窗 → 临时降权 "
                            f"{mute_s:.0f}s（INFOSEEK_SEARCH_OVERRUN_MUTE_S）")
            else:
                _overrun_count[n] = c


def _is_muted(name: str) -> bool:
    """引擎当前是否处于降权静默期（到期自动复活）。"""
    with _OVERRUN_LOCK:
        return time.monotonic() < _muted_until.get(name, 0.0)


def _filter_muted(engines: list) -> list:
    """剔除降权静默期引擎（不破坏原列表顺序）。"""
    return [(n, f) for n, f in engines if not _is_muted(n)]


def _reset_overrun_state() -> None:
    """测试钩子：清空超窗计数与静默表。"""
    with _OVERRUN_LOCK:
        _overrun_count.clear()
        _muted_until.clear()


def _search_deadline():
    """层间共享总预算 deadline（P2）：共享开关关闭或窗口=0 → None（原语义）。"""
    if not _shared_budget() or _window_s() <= 0:
        return None
    return time.monotonic() + _total_budget_s()


def _remaining_budget(deadline) -> float:
    """剩余预算秒（deadline 为 None → 0，便于日志与门控）。"""
    if deadline is None:
        return 0.0
    return deadline - time.monotonic()


def _sleep_throttle(deadline) -> None:
    """层间限速 0.8s。

    共享预算模式（deadline 非 None）：剩余预算不足 0.8s 时跳过限速，
    把时间留给下一层（预算就是节流，延迟上界优先）。
    """
    if deadline is None:
        time.sleep(0.8)
        return
    if deadline - time.monotonic() >= 0.8:
        time.sleep(0.8)


def _max_workers() -> int:
    """并发上限（env INFOSEEK_SEARCH_MAX_WORKERS，默认 12）。"""
    try:
        return max(1, int(os.environ.get('INFOSEEK_SEARCH_MAX_WORKERS', '12') or 12))
    except (TypeError, ValueError):
        return 12


def _window_s() -> float:
    """聚合窗口秒（env INFOSEEK_SEARCH_WINDOW_MS，默认 8000ms；0 = 关闭 → 原语义）。"""
    try:
        raw = os.environ.get('INFOSEEK_SEARCH_WINDOW_MS', '8000')
        return max(0.0, float(raw if raw != '' else 8000) / 1000.0)
    except (TypeError, ValueError):
        return 8.0


def _get_pool():
    """进程级常驻线程池（懒创建；atexit 非阻塞关闭）。

    为何常驻：原 `with ThreadPoolExecutor(...)` 退出时 shutdown(wait=True)，
    会把聚合窗口省下的时间又等回来 —— 池常驻后单次调用退出不再阻塞。
    """
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=_max_workers(), thread_name_prefix='infoseek-eng')
        try:
            import atexit
            atexit.register(lambda: _POOL and _POOL.shutdown(wait=False))
        except Exception:
            pass
    return _POOL


def _reset_pool() -> None:
    """测试钩子：关闭并重建常驻池（env 变更后需调用）。"""
    global _POOL
    if _POOL is not None:
        try:
            _POOL.shutdown(wait=False)
        except Exception:
            pass
        _POOL = None


def _parallel_merge(engines: list, query: str, max_results: int,
                    max_workers: int | None = None,
                    deadline: float | None = None) -> list:
    """层内并用：并行调用 + url 去重 + 组间权重/组内保序 → top-N。

    引擎失败相互隔离（异常仅记录）；返回结构 [{url,title,snippet}]。
    集成生命周期：自动剔除禁用引擎 + 调用经 _call_engine 包装（记录健康/配额）。

    v1.x（G5）并发优化：
      - 常驻池：避免 with 退出 shutdown(wait=True) 阻塞（层耗时不再等于最慢引擎）
      - 聚合窗口：wait(timeout=window) 到期即聚合返回，未达引擎结果丢弃；
        其健康记录仍由线程内 _call_engine 正常完成（不误杀引擎）
      - window=0 或单引擎 → 退回 as_completed 原语义
      - max_workers 参数仅兼容保留；并发上限由常驻池 env 统一控制

    v1.6.1（G5 P1/P2）：
      - deadline 参数：外部传入绝对截止时间（层间共享总预算）；None → 本层窗口内自算
      - 提前收敛：已完成引擎的去重合并结果 ≥ max_results×EARLY_FACTOR
        即提前返回（FIRST_COMPLETED 轮询，不等满窗）
      - 超窗降级：窗口到期未完成引擎记连续超窗，达阈值后临时 mute
        （_filter_muted 后续调用不拉起；冷却后复活）
    """
    collected = {name: [] for name, _ in engines}
    # 生命周期：剔除禁用引擎（健康/配额/认证）+ 降权静默期引擎
    engines = get_lifecycle().get_active(engines)
    engines = _filter_muted(engines)
    if not engines:
        return []
    pool = _get_pool()
    futs = {pool.submit(_call_engine, name, fn, query, max_results): name
            for name, fn in engines}
    window = _window_s()
    if window > 0 and len(engines) > 1:
        endpoint = deadline if deadline is not None else time.monotonic() + window
        factor = _early_factor()
        early_n = int(max_results * factor) if factor > 0 else 0
        seen = set()
        got_n = 0
        remaining = endpoint - time.monotonic()
        while futs and remaining > 0:
            done, pending = concurrent.futures.wait(
                futs, timeout=remaining,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                name = futs.pop(f)
                try:
                    rs = [r for r in (f.result() or []) if r.get('url')]
                    collected[name] = rs
                    for r in rs:
                        if r['url'] not in seen:
                            seen.add(r['url'])
                            got_n += 1
                except Exception as e:
                    log.warning(f"[{name}] 并行搜索 '{query}' 失败: {e}")
            remaining = endpoint - time.monotonic()
            if early_n and got_n >= early_n:
                # 提前收敛：尽力取消未达引擎（健康记录仍由线程完成），不误记超窗
                for f in tuple(pending):
                    if f in futs:
                        f.cancel()
                        collected.pop(futs[f], None)
                        del futs[f]
                log.info(f"[early] '{query}' 已收敛 {got_n} ≥ {early_n} 条（提前返回）")
                break
        if futs:  # 窗口到期仍有未完成 → 超窗计数 + 丢弃结果
            _note_overruns([futs[f] for f in tuple(futs)])
            for f in tuple(futs):
                f.cancel()
                collected.pop(futs[f], None)
    else:
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

        # v1.6.2 P3 词边界（补全）：实体命中匹配与 ner.py 同语义——
        # 拉丁/数字词强制词边界（'pe' 不命中 'openai'），中文词保持子串。
        def _q_hit(n: str) -> bool:
            nl = (n or '').lower()
            if not nl:
                return False
            if _re.search(r'[a-z0-9]', nl):
                try:
                    return _re.search(
                        r'(?<![a-z0-9_])' + _re.escape(nl) + r'(?![a-z0-9_])', ql
                    ) is not None
                except Exception:
                    return nl in ql
            return nl in ql

        hits = []
        for e in get_all_entities():
            names = [e.get('name', '')] + list(e.get('aliases', []) or [])
            if any(_q_hit(n) for n in names):
                hits.append(e)
        extra = []
        for e in hits:
            # v1.7.7 包B：候选扩展词含实体正名（query 命中别名/短名时补正名，反向扩展；
            # 如「无限工坊」→ 补「无限工坊科技」，提升召回覆盖）
            for a in ([e.get('name', '')] + list(e.get('aliases', []) or [])):
                if a and a.lower() not in ql and a not in extra:
                    extra.append(a)
                if len(extra) >= 3:
                    break
            if len(extra) >= 3:
                break

        # v2.5.0 G3 (P2-1): 图谱邻域召回——复用跨 research 累积的会话图谱，
        # 追加邻居实体词（≤2/实体，总上限 4，weight≥0.2 过滤弱关联）。
        # 冷启动无图谱 → 纯别名扩展（零行为变化）；噪声由 _filter_relevant 门控兜底。
        # 导入统一走顶层 entity_graph（与 infoseek_core_v2 注册端一致，避免
        # core.entity_graph 双模块状态分裂）。
        if _env_flag('INFOSEEK_RECALL_GRAPH', True) and len(extra) < 4:
            try:
                _core_dir = _P(__file__).parent.parent / 'core'
                if str(_core_dir) not in _s.path:
                    _s.path.insert(0, str(_core_dir))
                from entity_graph import get_global_graph
                g = get_global_graph()
                if g is not None:
                    graph_extra = []
                    for e in hits:
                        for nb in g.get_neighbors(e['name'], top_n=2):
                            nname = nb['entity_name']
                            if (nname.lower() not in ql and nname not in extra
                                    and nname not in graph_extra
                                    and nb.get('weight', 0) >= 0.2):
                                graph_extra.append(nname)
                            if len(graph_extra) >= 4 - len(extra):
                                break
                        if len(extra) + len(graph_extra) >= 4:
                            break
                    if graph_extra:
                        log.info(f"[recall] 图谱邻域 '{query}' → +{graph_extra}")
                        extra.extend(graph_extra)
            except Exception:
                pass

        # v1.9.0 GA9⑤/GA10：跨语言别名扩展（中→拼音→拉丁，提升跨语言源召回）
        # + 人名实体引导注册（D2 闭合：融合链/NER 可按人名索引源文本）。
        # 拼音别名与词典别名互补（BYD 类已被实体通道扩出 → exclude 去重）；
        # 总预算 4 → 6（人名类 query 专属增量，非人名 query 零变化）；
        # 噪声仍由 _filter_relevant 相关性门控兜底（§8.11.2 ③：生成端不硬判）。
        if _env_flag('INFOSEEK_XLING_BRIDGE', True) and len(extra) < 6:
            try:
                xtra = _xling_mod.expansion_aliases(
                    query, cap=6 - len(extra), exclude_lower=ql,
                    exclude={e.lower() for e in extra})
                if xtra:
                    log.info(f"[recall] 跨语言别名扩展 '{query}' → +{xtra}")
                    extra.extend(xtra)
            except Exception:
                pass
        try:
            _person_mod.bootstrap_subject(query)  # 幂等；失败静默不阻断召回
        except Exception:
            pass

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


# v1.7.7 包A：相关性门控 v2 常量
_RELEVANCE_TOP_RATIO = 0.6      # 保底相对阈值（0.6 × top1）
# v1.8.4：_RELEVANCE_WARNED 已移除 —— jieba 缺失一次性告警迁至 text_tokenizer


def _tokenize_query(query: str) -> set:
    """query 主体词提取（v1.7.7 包A / P0#2；v1.8.4 GA5 单源化委托）

    **实现已收敛至唯一真源 `text_tokenizer.tokenize_text()`**（GA5 闭合）——本函数退化为
    薄封装，仅为向后兼容 `_filter_relevant` 调用点与 `test_relevance_gate_v177` 断言保留名字。

    `require_chinese=True`：纯英文/数字 query 返回空集（有意门控，服务「中文多字词硬门槛」
    ——纯英文 query 不触发中文门槛）；`warn_on_fallback=True`：jieba 缺失发一次性告警
    （原 `_RELEVANCE_WARNED` 语义，此前静默 except 使多字词硬门槛完全失效，'无限工坊'
    类主题漂移因此漏检）。

    与 `anchor_adapter._tokenize_subject` 除上述门控外**算法完全同源**（v1.8.4 前为两份
    独立实现，v1.8.2 仅对齐回退算法；审计 P1-1「同算法」声明曾被实测证伪）。
    """
    return tokenize_text(query, require_chinese=True, warn_on_fallback=True)


def _llm_judge_relevance(text: str, query: str) -> float:
    """v1.7.7 包C：LLM 复判相关性（opt-in，INFOSEEK_RELEVANCE_LLM=1）。

    返回 0-100 分；未启用 / 不可用 / 解析失败 → -1（调用方回落规则分）。
    仅对边缘样本调用（控成本）。
    """
    if os.environ.get('INFOSEEK_RELEVANCE_LLM', '0') not in ('1', 'true', 'True', 'yes', 'on'):
        return -1.0
    try:
        import sys as _s
        from pathlib import Path as _P
        _core = _P(__file__).parent.parent / 'core'
        if str(_core) not in _s.path:
            _s.path.insert(0, str(_core))
        from llm_router import llm_call
        prompt = ('判断下面文本与检索主题的相关性，只输出一个 0-100 的整数'
                  '（0=完全无关，100=高度相关）：\n'
                  f'主题：{query}\n文本：{(text or "")[:400]}\n分数：')
        out = llm_call(prompt, max_tokens=8)
        content = (out or {}).get('content', '') if isinstance(out, dict) else str(out)
        m = _re.search(r'\d{1,3}', content or '')
        if not m:
            return -1.0
        return float(min(100, max(0, int(m.group(0)))))
    except Exception:
        return -1.0


def _filter_relevant(results: list, query: str, min_score: int = 12) -> list:
    """主题相关性过滤（v1.0.1 PATCH / P1-2；v1.7.7 包A 门控 v2）

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
        # v1.7.7 包A（P1#5）：下限固定 12（候选少不放松，宁走覆盖门控）
        min_score = 14 if n > 20 else 12
    try:
        # v1.8.4：原 996-999 的函数体内 sys.path.insert + 局部 import 已删除 ——
        # 改用顶层模块对象 _anchor_mod 属性访问（见文件头），sys.path 恒定不膨胀，
        # 且保留晚绑定语义（mock.patch('anchor_adapter.*') 仍可生效）。
        # 多字词硬门槛（仅中文 query 启用）；v1.7.7 包A（P0#2）：
        # 统一走 _tokenize_query（jieba 优先 → 缺失回退 + 告警，不再静默失效）
        hard_words = set(_tokenize_query(query))

        kept = []
        for r in results:
            text = ' '.join(filter(None, [r.get('title', ''), r.get('snippet', '')]))
            # v1.0.1b 口径对齐（P1 2026-09-10）：max(Jaccard, 字符串包含×0.8)
            # Jaccard 关键词提取对短中文主题过严（n-gram 滑动窗口致
            # 主题词单字不交集 → 中文结果普遍 ~0 分），containment 兜底
            # 让真实中文搜索结果可评分；0 分垃圾（完全不含主题词）仍有保底拦截。
            jaccard = _anchor_mod.compute_semantic_similarity(text, query)
            try:
                containment = _anchor_mod._string_containment_similarity(text, query) or 0
            except Exception:
                containment = 0
            score = max(jaccard, containment * 0.8)
            # v1.7.7 包C：LLM 复判（opt-in，仅边缘样本控成本；失败回落规则分）
            if (min_score - 5) <= score <= (min_score + 15):
                _ls = _llm_judge_relevance(text, query)
                if _ls >= 0:
                    score = _ls
                    r['relevance_llm'] = _ls
            r['relevance'] = score  # 全量落分（P1 保底窗口数据底座）
            # v1.9.0 GA10：跨语言别名桥接（D1 召回侧闸门）——中文 query × 拉丁文源
            # 语义低分/中文多字词零交集时，subject 人名拼音别名 / 实体拉丁别名
            # 词边界命中 → 豁免双门槛（英文一手源不再被中文硬门槛整体拦截）。
            # 未触发（无别名组/无命中/env off）→ bridge=0，判定路径与 v1.8.4 完全一致。
            # ⚠️ _xling_mod 模块对象属性访问（晚绑定），禁 from-import（§8.13.3 铁律）。
            _passed = score >= min_score
            if _passed and hard_words:
                text_lower = text.lower()
                _passed = any(w in text_lower for w in hard_words)
            if not _passed:
                try:
                    _br = _xling_mod.bridge_score(text, query) or 0
                except Exception:
                    _br = 0
                if _br >= min_score:
                    r['relevance'] = max(score, _br)
                    r['xling_bridge'] = _br
                    _passed = True
            if not _passed:
                continue
            kept.append(r)
        min_expected = _min_expected(max(3, len(results)))
        if len(kept) >= min_expected:
            log.info(f"[relevance] '{query}' 过滤 {len(results)}→{len(kept)} 条")
            return kept
        # P1 保底加固（2026-09-10）：结果不足预期时不再裸返全量原始列表
        # （此前会把 0 分 / SEO 克隆站群全量保送；且 kept 为空时返回的是未落分原始条目）。
        # 降级策略：分数兜底窗口 —— 保留 relevance ≥ 绝对下限的条目按分降序取 top，
        #             连下限都无达标 → 返回 []（宁缺毋滥，空结果由下游覆盖门控处理）。
        floor = max(8, int(min_score * 0.5))
        # v1.7.7 包A（P0#1）：相对阈值 —— 保底不低于 top1 的 60%，杜绝低分陪跑
        _top1 = max((r.get('relevance', 0) for r in results), default=0)
        floor = max(floor, int(_top1 * _RELEVANCE_TOP_RATIO))
        try:
            floor = int(os.environ.get('INFOSEEK_RELEVANCE_FLOOR', floor))
        except ValueError:
            pass  # env 非法 → 保持默认下限
        fallback = sorted((r for r in results if r.get('relevance', 0) >= floor),
                          key=lambda r: r.get('relevance', 0), reverse=True)
        if fallback:
            log.warning(f"[relevance] '{query}' 过滤后 {len(kept)} 条 < 预期 {min_expected}；"
                        f"保底窗口保留 {len(fallback)} 条（≥{floor} 分，按分降序）")
            return fallback[:min_expected]
        log.warning(f"[relevance] '{query}' 无达标结果（全部 <{floor} 分），返回空（宁缺毋滥）")
        return []
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
                                 reserve_pool: list,
                                 deadline: float | None = None) -> list:
    """层内并用 + 动态保留（层内冗余）：

      1. 主并行：除保留引擎外的全部引擎（md5 轮换选保留者，无状态可复现）
      2. 质量门控：并行结果 < min_expected 时触发保留引擎兜底（可双保留）
      3. 保留补充结果追加尾部（补充语义，不抢占），返回 top-N

    v1.6.1（G5 P1）：保留兜底纳入同一窗口预算 ——
      - deadline 为 None 时以本函数起点推导总预算（等价原窗口语义）
      - 主并行结果不足且预算仍有剩余 → 保留引擎并行提交、并入剩余预算等待
        （不再 sleep(0.8) 串行兜底，总耗时受预算上界约束）
      - 预算耗尽（remaining ≤ 0.05s）→ 跳过兜底直接返回（延迟上界优先）
    """
    if not reserve_pool:
        return _parallel_merge(engines, query, max_results, deadline=deadline)
    import hashlib
    idx = int(hashlib.md5(query.encode('utf-8')).hexdigest(), 16) % len(reserve_pool)
    reserved = [reserve_pool[idx]]
    reserved_names = {n for n, _ in reserved}
    main = [e for e in engines if e[0] not in reserved_names]
    endpoint = deadline if deadline is not None else time.monotonic() + _window_s()
    got = _parallel_merge(main, query, max_results, deadline=endpoint)
    if len(got) < _min_expected(max_results):
        remaining = endpoint - time.monotonic()
        if remaining <= 0.05:
            log.info(f"[reserved] '{query}' 预算已耗尽（remaining={remaining:.2f}s），"
                     f"跳过兜底（共 {len(got)} 条）")
            return got[:max_results]
        got = [dict(r) for r in got]
        seen = {r['url'] for r in got}
        pool = _get_pool()
        rfuts = {}
        for rname, rfn in reserved:
            if _is_muted(rname):
                continue
            rfuts[pool.submit(_call_engine, rname, rfn, query, max_results)] = rname
        if rfuts:
            try:
                done, pending = concurrent.futures.wait(rfuts, timeout=remaining)
                for f in done:
                    rname = rfuts[f]
                    try:
                        for r in (f.result() or []):
                            if r.get('url') and r['url'] not in seen:
                                seen.add(r['url'])
                                got.append(r)
                        log.info(f"[{rname}:reserved] '{query}' 兜底补充 → {len(got)} 条")
                    except Exception as e:
                        log.warning(f"[{rname}:reserved] '{query}' 兜底失败: {e}")
                if pending:
                    _note_overruns([rfuts[f] for f in pending])
                    for f in pending:
                        f.cancel()
            except Exception as e:
                log.warning(f"[reserved] '{query}' 兜底异常: {e}")
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
        got_serial = _filter_relevant(_search_web_serial(query, max_results), query)
        _log_engine_stats(query)
        return got_serial
    ai_mode = os.environ.get('INFOSEEK_SEARCH_ENGINE', 'auto') == 'ai'
    deadline = _search_deadline()  # G5 P2：层间共享总预算（None = 原语义各层各耗窗口）
    if deadline is None:
        time.sleep(0.8)  # 层间限速（原语义：进函数先限速）
    # 共享预算模式：不预扣 0.8s，预算本身即节流；层间回退时再限速
    if ai_mode and _has_ai_key():
        # AI 模式：AI 引擎（权重高）+ 免费引擎全并行，免费引擎为保留池
        ai_layer = _ai_engines() + _free_engines()
        got = _parallel_merge_with_reserve(ai_layer, query, max_results,
                                           get_lifecycle().get_active(
                                               _reserve_pool(True, ai_layer)),
                                           deadline=deadline)
        if got:
            log.info(f"[AI-layer] '{query}' → {len(got)} 条（并行合并）")
            _got = _filter_relevant(got, query)
            _log_engine_stats(query)
            return _got
        log.warning("[AI-layer] 结果为空，回退默认层"
                    + (f"（剩余预算 {_remaining_budget(deadline):.2f}s）" if deadline else ""))
        _sleep_throttle(deadline)
    # 默认层：免费并行 + 限量引擎保留池（配额保护）
    default_layer = _default_layer()
    if _env_flag('INFOSEEK_CONCURRENT_ALL', False):
        default_layer = default_layer + _quota_engines_with_key()
        log.info('[concurrent-all] 付费引擎并入主并行（INFOSEEK_CONCURRENT_ALL=1）')

    got = _parallel_merge_with_reserve(default_layer, query, max_results,
                                       get_lifecycle().get_active(
                                           _reserve_pool(False, default_layer)),
                                       deadline=deadline)
    if got:
        log.info(f"[default-layer] '{query}' → {len(got)} 条（并行合并）")
        _got = _filter_relevant(got, query)
        _log_engine_stats(query)
        return _got
    log.warning(f"搜索降级链全失败: '{query}'")
    _log_engine_stats(query)
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

def search_name_to_url(name: str, platform: str = "", min_results: int = 2,
                       prefer_kb: bool = False) -> list:
    """
    将名称/频道名类锚点通过 web search 转换为 URL 列表。
    v1.0.0：改用 search_web 降级链（DDG HTML → Bing RSS → Wikipedia）；
    结果低于 min_results 时显式返回 []（覆盖率门控，不再静默返回单条假结果）。
    v1.7.2：prefer_kb（多域交集场景）时对命中 KB 域的结果加分并上浮排序。
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

    # v1.7.2 prefer_kb（交集场景）：KB 域命中结果加分并上浮（单源 kb_intersect_bonus）
    if prefer_kb:
        try:
            import re as _re
            from domain_router import kb_intersect_bonus
            from trusted_kb import kb_lookup
            kb_domains = set()
            for h in kb_lookup(name, limit=5):
                m = _re.search(r'https?://([^/]+)', h.get('entry', '') or '')
                if m:
                    kb_domains.add(m.group(1))
            for r in results:
                m = _re.search(r'https?://([^/]+)', r.get('url', '') or '')
                dom = m.group(1) if m else ''
                if dom and dom in kb_domains:
                    r['_kb_domain'] = dom
                    r['score'] = min(100, r.get('score', 65)
                                     + kb_intersect_bonus({'_kb_domain': dom, '_kb_hit_count': 1}))
            results.sort(key=lambda x: x.get('score', 0), reverse=True)
        except ImportError:
            pass

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

# v1.7.6 G2-2：低相关成功源温和降权阈值（relevance < 阈值 → 质量反馈）
_QUALITY_RELEVANCE_MIN = 20


def generate_feedback(details: list) -> list:
    """治理反馈生成（唯一真源，v1.7.6）。

    两类反馈：
      1. failure：dead_link / failed / needs_tier2 → 降权 −20 / −10
      2. quality（v1.7.6 G2-2）：success/partial 但 relevance < _QUALITY_RELEVANCE_MIN
         → 温和降权 −5（填补"成功但低质"治理盲区）
    """
    feedbacks = []
    for r in details:
        anchor = r.get("anchor", {})
        status = r.get("status")
        if status in ("dead_link", "failed", "needs_tier2"):
            penalty = -20 if status == "dead_link" else -10
            feedbacks.append({
                "feedback_type": "failure",
                "anchor_name": anchor.get("name", "?"),
                "anchor_platform": anchor.get("platform", "?"),
                "anchor_entry": anchor.get("entry", "?"),
                "original_score": anchor.get("score", 0),
                "failure_type": status,
                "failure_reason": (r.get("steps", [{}])[-1].get("reason", "")) if r.get("steps") else "",
                "suggested_penalty": penalty,
                "suggested_new_score": max(0, (anchor.get("score", 0) or 0) + penalty)
            })
        elif status in ("success", "partial"):
            rel = r.get("relevance")
            if rel is None:
                rel = (r.get("output") or {}).get("relevance")
            try:
                rel = float(rel) if rel is not None else None
            except (TypeError, ValueError):
                rel = None
            if rel is not None and rel < _QUALITY_RELEVANCE_MIN:
                penalty = -5
                feedbacks.append({
                    "feedback_type": "quality",
                    "anchor_name": anchor.get("name", "?"),
                    "anchor_platform": anchor.get("platform", "?"),
                    "anchor_entry": anchor.get("entry", "?"),
                    "original_score": anchor.get("score", 0),
                    "failure_type": "low_relevance",
                    "failure_reason": f"relevance={rel} < {_QUALITY_RELEVANCE_MIN}",
                    "suggested_penalty": penalty,
                    "suggested_new_score": max(0, (anchor.get("score", 0) or 0) + penalty)
                })
    return feedbacks


# ═══════════════════════════════════════════════════════════════
# 阶段 5: 执行一个锚点的完整采集
# ═══════════════════════════════════════════════════════════════

def execute_anchor(anchor: dict, output_dir: str) -> dict:
    """对单个锚点执行完整 infoseek 流水线（含异常保护）

    v1.7.2：读取 anchor['_prefer_kb']（多域交集信号）→ 透传名称搜索排序 + result 可观测。
    """
    start_time = time.time()
    prefer_kb = bool(anchor.get('_prefer_kb', False))
    result = {
        "anchor": anchor,
        "status": "pending",
        "prefer_kb": prefer_kb,
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
            search_results = search_name_to_url(name, platform, prefer_kb=prefer_kb)
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

def run_pipeline(anchors: list, output_dir: str = "./outputs", min_anchors: int = 0,
                 subject: str = None, prefer_kb: bool = None) -> dict:
    """批量执行锚点采集

    v1.0.0：新增覆盖率门控——anchors 数量低于 min_anchors 时直接产出
    显式失败报告（status=insufficient_coverage），不执行采集、不产出伪完整报告。
    min_anchors=0 表示不启用门控（手动 --anchors 指定场景）。
    v1.7.2：subject/prefer_kb 贯穿全链——prefer_kb 缺省由 subject（或首锚点 name）
    经 detect_domain 推导；解析后注入每条 anchor 供下游 execute_anchor/搜索排序消费，
    并写入报告供可观测。
    """
    # v1.7.2 多域交集信号解析与贯穿
    if prefer_kb is None:
        _subj = subject or (anchors[0].get('name', '') if anchors else '')
        prefer_kb = False
        if _subj:
            try:
                from domain_router import detect_domain
                prefer_kb = bool(detect_domain(_subj).get('prefer_kb'))
            except Exception:
                prefer_kb = False
    prefer_kb = bool(prefer_kb)
    for _a in anchors:
        _a.setdefault('_prefer_kb', prefer_kb)

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
            "prefer_kb": prefer_kb,
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
        "prefer_kb": prefer_kb,
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

    # v1.7.7 包B：实体回流（高分源实体 → 动态词典，供后续 _expand_query 复用）
    try:
        _reflow_entities(all_results)
    except Exception as _e:
        log.debug(f"[entity-reflow] 跳过: {_e}")

    return report


# ═══════════════════════════════════════════════════════════════
# 阶段 6: 治理反馈自动应用（新增, C2）
# ═══════════════════════════════════════════════════════════════

def _default_anchor_db_path() -> str:
    """锚点库默认路径（v1.7.6 G2-3）：INFOSEEK_DATA_DIR 锚定，避免相对 cwd 不稳定。"""
    d = os.environ.get('INFOSEEK_DATA_DIR')
    if d:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return os.path.join(d, 'anchor_db.json')
    return os.path.join(os.path.expanduser('~'), '.infoseek', 'anchor_db.json')


def _reflow_entities(details: list, min_freq: int = 2) -> int:
    """v1.7.7 包B：从采集结果回流候选实体（防噪声：机构后缀门控 + 频次阈值）。

    仅提取「中文机构后缀词」（公司/科技/集团/股份/协会/研究院/大学/实验室）
    与英文专有名词；同会话内出现 ≥ min_freq 次才回流。返回回流条数。
    """
    try:
        import sys as _s
        from pathlib import Path as _P
        from collections import Counter
        _root = _P(__file__).parent.parent
        for _p in (str(_root), str(_root / 'core')):
            if _p not in _s.path:
                _s.path.insert(0, _p)
        import core.entities as _ent
    except Exception:
        return 0
    cnt = Counter()
    for r in (details or []):
        if r.get('status') not in ('success', 'partial'):
            continue
        out = r.get('output') or {}
        text = ' '.join(filter(None, [
            out.get('title', ''), out.get('text_preview', ''),
            (r.get('anchor') or {}).get('name', '')]))
        for m in _re.findall(
                r'[\u4e00-\u9fff]{2,6}(?:公司|科技|集团|股份|协会|研究院|大学|实验室)', text):
            cnt[m] += 1
        for m in _re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b', text):
            cnt[m] += 1
    n = 0
    for name, c in cnt.items():
        if c >= min_freq and _ent.learn_entity(
                name, category='AUTO', confidence=min(0.9, 0.3 + c * 0.1)):
            n += 1
    if n:
        log.info(f"[entity-reflow] 回流 {n} 个候选实体（≥{min_freq} 次）")
    return n


def apply_feedback(feedbacks: list, anchor_db_path: str = None) -> int:
    """
    将治理反馈自动应用到本地锚点库。
    若 anchor_db.json 不存在则跳过（锚点库尚未建立时静默处理）。
    返回实际更新的锚点数量。
    v1.7.6 G2-3：默认路径锚定 INFOSEEK_DATA_DIR / ~/.infoseek；兼容回退 cwd 旧库。
    """
    if not feedbacks:
        return 0
    if anchor_db_path is None:
        anchor_db_path = _default_anchor_db_path()
        # 平滑迁移：数据目录无库但 cwd 有旧库 → 沿用旧库（不丢历史）
        if not os.path.exists(anchor_db_path) and os.path.exists('./anchor_db.json'):
            anchor_db_path = './anchor_db.json'
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
            # v1.7.2 多域交集信号透传（prefer_kb → KB 源上浮）
            _prefer_kb = False
            try:
                from domain_router import detect_domain
                _prefer_kb = bool(detect_domain(args.industry).get('prefer_kb'))
            except Exception:
                _prefer_kb = False
            kb_hits = kb_lookup(args.industry, limit=5)
            if kb_hits:
                log.info(f"KB补充: 命中 {len(kb_hits)} 条可信源")
                merged = kb_merge(anchors, kb_hits, prefer_kb=_prefer_kb)
                log.info(f"合并后: {len(anchors)} web + {len(kb_hits)} KB → {len(merged)} 总锚点")
                anchors = merged
            else:
                # web search 无结果时的兜底
                fb = kb_fallback(args.industry, limit=5)
                if fb and len(anchors) <= 2:
                    log.warning(f"web结果稀少({len(anchors)}条)，启用KB兜底(+{len(fb)}条)")
                    anchors = kb_merge(anchors, fb, prefer_kb=_prefer_kb)
        except ImportError:
            log.info("trusted_kb 模块未找到，跳过KB补充")
        except Exception as e:
            log.warning(f"KB补充异常(非致命): {e}")

        # 执行管道（v1.0.0：industry 自动嗅探路径启用覆盖率门控 ≥3）
        report = run_pipeline(anchors, args.output, min_anchors=3,
                              subject=args.industry, prefer_kb=_prefer_kb)

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

        # ── KB 补充：anchors 路径接线（与 --industry 同构 + 域感知扩展）──
        try:
            from trusted_kb import kb_lookup, kb_merge, kb_fallback, kb_enrich
            enrich_topic = anchors[0].get("name", "") if anchors else ""
            # v1.7.2 多域交集信号透传（prefer_kb → KB 源上浮）
            _prefer_kb = False
            try:
                from domain_router import detect_domain
                _prefer_kb = bool(detect_domain(enrich_topic).get('prefer_kb')) if enrich_topic else False
            except Exception:
                _prefer_kb = False
            kb_hits = kb_lookup(enrich_topic, limit=5) if enrich_topic else []
            if not kb_hits and enrich_topic:
                # 域感知扩展：按 detect_domain 判定领域，种子词横向补齐
                kb_hits = kb_enrich(enrich_topic, limit=5, prefer_kb=_prefer_kb)
            if kb_hits:
                log.info(f"KB补充(anchors): 命中 {len(kb_hits)} 条可信源")
                merged = kb_merge(anchors, kb_hits, prefer_kb=_prefer_kb)
                log.info(f"合并后: {len(anchors)} anchors + {len(kb_hits)} KB → {len(merged)} 总锚点")
                anchors = merged
            else:
                # web search 无结果时的兜底
                fb = kb_fallback(enrich_topic, limit=5) if enrich_topic else []
                if fb and len(anchors) <= 2:
                    log.warning(f"anchors稀少({len(anchors)}条)，启用KB兜底(+{len(fb)}条)")
                    anchors = kb_merge(anchors, fb, prefer_kb=_prefer_kb)
        except ImportError:
            log.info("trusted_kb 模块未找到，跳过KB补充(anchors)")
        except Exception as e:
            log.warning(f"KB补充异常(anchors, 非致命): {e}")

        run_pipeline(anchors, args.output, subject=enrich_topic,
                     prefer_kb=_prefer_kb)


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


def _audit_identity(msg: str, prefix: str = "identity_attribution") -> None:
    """审计落盘（复用 state_dir.audit_log_path）。
    prefix 可指定能力族（identity_attribution / account_forensics 等），C2 合规审计。"""
    try:
        _ensure_cap_paths()
        from core.state_dir import audit_log_path
        p = audit_log_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [{prefix}] {msg}\n")
    except Exception:
        pass


def _augment_cross_matches(accounts: list) -> list:
    """发现层交叉命中补齐（T4/G1）：同名账号跨平台命中数是发现层天然产出，
    但 Maigret/Sherlock client 未显式写入 cross_platform_matches 字段，
    此处按 username 统计平台集合补齐，供三因子融合的 A 因子使用。
    已带 cross_platform_matches 的条目保留原值；username 缺失条目不补齐。"""
    by_user: dict = {}
    for acc in accounts:
        u = acc.get("username")
        if u:
            by_user.setdefault(u, set()).add(acc.get("platform") or acc.get("source") or "")
    out = []
    for acc in accounts:
        a = dict(acc)
        u = a.get("username")
        if u and a.get("cross_platform_matches") is None:
            a["cross_platform_matches"] = max(0, len(by_user.get(u, set())) - 1)
        out.append(a)
    return out


def _build_identity_anchors(accounts: list, username: str, max_results: int) -> list:
    """发现/验证结果 → 锚点条目（v1.6.0 起：附加三因子融合字段）。

    - 原字段（confidence/verdict/verdict_cn/trust_score/trust_confidence）保持透传
    - 融合模块可用 → 附加 confidence_final/confidence_label(/_cn)/fusion/
      fusion_degradation/verdict_final；不可用/异常 → 仅原字段（降级不阻断）
    """
    anchors = []
    for acc in accounts[:max_results]:
        conf = float(acc.get("confidence") or 0)
        anchor = {
            "url": acc.get("url") or "",
            "title": acc.get("platform") or acc.get("source") or "未知平台",
            "snippet": f"{acc.get('username') or username} @ {acc.get('platform','')}"
                       + (f" ({acc.get('fullname')})" if acc.get("fullname") else "")
                       + (f" [验证:{acc.get('verdict_cn')}]" if acc.get("verdict_cn") else ""),
            "score": int(conf * 100),
            "source": acc.get("source", "Maigret"),
            "identity_attribution": True,
            "confidence": conf,
            "verdict": acc.get("verdict", ""),
            "verdict_cn": acc.get("verdict_cn", ""),
            "trust_score": acc.get("trust_score"),
            "trust_confidence": acc.get("trust_confidence"),
        }
        # v2.1.0 双源聚合元信息（存在则透传，便于审计/呈现）
        if acc.get("sources"):
            anchor["attribution_sources"] = list(acc["sources"])
        if acc.get("cross_source_confirmed"):
            anchor["cross_source_confirmed"] = True
        if acc.get("weak_single_source"):
            anchor["weak_single_source"] = True
        if acc.get("region"):
            anchor["region"] = acc["region"]
        try:
            from identity_confidence_fusion import fuse_anchor
            fused = fuse_anchor(acc)
            if fused.get("confidence_final") is not None:
                anchor["confidence_final"] = fused["confidence_final"]
                anchor["confidence_label"] = fused["confidence_label"]
                anchor["confidence_label_cn"] = fused["confidence_label_cn"]
                anchor["fusion"] = fused["fusion"]
                anchor["fusion_degradation"] = fused["fusion_degradation"]
                anchor["verdict_final"] = fused["verdict_final"]
        except Exception as e:
            log.debug(f"[身份归因] 融合附加失败（降级原输出）: {e}")
        anchors.append(anchor)
    return anchors


def _account_deep_sufficient(acc: dict) -> bool:
    """A2 信号充分性判定（单账号级，语义对齐 assess_sufficiency）：
    成长时序可用（权重最高）→ 充足；ER+图谱组合 → 充足；否则不足。
    pipeline 自动路径（Maigret/Sherlock 仅 username）天然不足 → 恒降级 AccountTrustScorer。"""
    has_ts = bool(acc.get("growth_series")) or bool(acc.get("likes_series"))
    has_graph = bool(acc.get("graph_edges")) or bool(acc.get("edges"))
    has_er = acc.get("er") is not None or acc.get("engagement_rate") is not None
    if has_ts:
        return True
    if has_graph and has_er:
        return True
    return False


def _run_fake_detect(accounts_subset: list) -> dict:
    """把带深度信号的账号子集构造 Dataset 跑 FakeDetect，返回 report。
    异常/充分性不足 → 返回 degraded report（调用方降级 AccountTrustScorer，零替代风险）。
    id 契约：meta index = 整数位置（与 from_raw 的 meta 对齐），likes/growth key 同；
    edges 端点按账号位置归一化（无法解析的边忽略，不影响时序检测）。"""
    import pandas as pd
    _ext = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "extensions", "fake_detect")
    if _ext not in sys.path:
        sys.path.insert(0, _ext)
    from fake_detect_engine import detect
    from data_adapter import from_raw
    pos_of = {acc.get("username") or acc.get("id") or f"acc_{i}": i
              for i, acc in enumerate(accounts_subset)}
    rows, likes, growth, edges = [], {}, {}, []
    for i, acc in enumerate(accounts_subset):
        aid = i  # 整数位置 id（与 from_raw meta index 对齐）
        rows.append({"id": aid,
                     "followers": acc.get("followers") or 100,
                     "following": acc.get("following") or 100,
                     "posts": acc.get("posts") or 50,
                     "er": float(acc.get("er", acc.get("engagement_rate", 0.0)) or 0.0)})
        if acc.get("likes_series"):
            likes[aid] = np.asarray(acc["likes_series"], dtype=float)
        if acc.get("growth_series"):
            growth[aid] = np.asarray(acc["growth_series"], dtype=float)
        for e in (acc.get("edges") or acc.get("graph_edges") or []):
            try:
                a, b = e
                edges.append((pos_of.get(a, int(a)), pos_of.get(b, int(b))))
            except Exception:
                continue
    ds = from_raw(meta_df=pd.DataFrame(rows), likes=likes, growth=growth,
                  edges=edges if edges else None)
    return detect(ds)


def _verify_accounts(accounts: list) -> list:
    """发现→验证闭环（T1/G3 + A2 信号门控）：批量人因验证，验证为增强不阻断发现。

    - 注册表 AccountTrustScorer 经 enabled ∩ consent 判定（合规双闸口）
    - A2 信号充分性分支：账号带深度信号（成长时序/互动ER/图谱）且 FakeDetect
      双闸通过 → 深度取证（L1+L2+L3+时序）；不足 → AccountTrustScorer（零替代风险）
    - 从发现条目构造画像（缺省信号按中性处理，绝不捏造数据）
    - 附加 trust_score / verdict / verdict_cn / trust_confidence，不覆盖发现层 confidence
    - 验证层未启用/异常 → 原样返回发现结果（验证是增强，不阻断发现）
    """
    if not accounts:
        return accounts
    try:
        from core.capability_registry import is_effective_enabled
        fd_on = is_effective_enabled("FakeDetect")
        ats_on = is_effective_enabled("AccountTrustScorer")
        if not (fd_on or ats_on):
            log.debug("[身份归因] 验证层未启用/未授权，跳过验证（发现结果保留）")
            return accounts
        from account_trust_scorer import score_account
        verified = []
        fd_pending = []   # (index, acc) 深度信号充分，待 FakeDetect 批量
        for i, acc in enumerate(accounts):
            if acc.get("_gap"):
                verified.append(acc)
                continue
            # 构造画像（跨站匹配数为发现层天然产出）
            profile = {
                "username": acc.get("username") or "",
                "cross_platform_matches": acc.get("cross_platform_matches"),
            }
            if fd_on and _account_deep_sufficient(acc):
                fd_pending.append((i, acc))
                verified.append(None)  # 占位，FakeDetect 完成后统一回填
                continue
            r = score_account(profile)
            acc = dict(acc)
            acc["trust_score"] = r["trust_score"]
            acc["verdict"] = r["verdict"]
            acc["verdict_cn"] = r["verdict_cn"]
            acc["trust_confidence"] = r["confidence"]
            acc["verify_engine"] = "AccountTrustScorer"
            verified.append(acc)
        # A2 深度取证：批量子集一次跑引擎（充分性不足/异常 → 该子集降级 ATS）
        if fd_pending:
            try:
                rep = _run_fake_detect([a for _, a in fd_pending])
                if rep.get("degradation") == "insufficient_signals":
                    log.info("[身份归因] FakeDetect 数据充分性不足，子集降级 AccountTrustScorer")
                    rep = None
            except Exception as e:
                log.warning(f"[身份归因] FakeDetect 批量执行失败（降级 AccountTrustScorer）: {e}")
                rep = None
            verdicts = (rep or {}).get("verdicts", {}) if rep else {}
            for j, (i, acc) in enumerate(fd_pending):
                v = verdicts.get(str(j)) if rep else None   # 子集内位置 <-> Dataset id
                if v and rep is not None:
                    out = dict(acc)
                    out["trust_score"] = v["trust_score"]
                    out["verdict"] = v["verdict"]
                    out["verdict_cn"] = v["verdict_cn"]
                    out["trust_confidence"] = 0.85   # 深度取证置信（对抗盲区已声明）
                    out["verify_engine"] = "FakeDetect"
                    out["forensics"] = {
                        "coord_clusters": rep.get("coord_clusters", []),
                        "sync_groups": rep.get("sync_groups", []),
                        "summary": rep.get("summary", {}),
                        "degradation": rep.get("degradation"),
                        "blindspots": rep.get("blindspots", []),
                    }
                    verified[i] = out
                else:
                    r = score_account({"username": acc.get("username") or "",
                                       "cross_platform_matches": acc.get("cross_platform_matches")})
                    out = dict(acc)
                    out["trust_score"] = r["trust_score"]
                    out["verdict"] = r["verdict"]
                    out["verdict_cn"] = r["verdict_cn"]
                    out["trust_confidence"] = r["confidence"]
                    out["verify_engine"] = "AccountTrustScorer"
                    verified[i] = out
            n_fd = sum(1 for a in verified if a and a.get("verify_engine") == "FakeDetect")
            if n_fd:
                _audit_identity(f"FakeDetect 深度取证完成: {n_fd}/{len(fd_pending)} 账号 via {_e_verdicts(verdicts)}",
                                prefix="account_forensics")
        log.info(f"[身份归因] 验证层完成: {len(verified)} 个账号 "
                 f"(AT={sum(1 for a in verified if a and a.get('verify_engine')=='AccountTrustScorer')}"
                 f"/FD={sum(1 for a in verified if a and a.get('verify_engine')=='FakeDetect')})")
        return verified
    except Exception as e:
        log.warning(f"[身份归因] 验证层跳过（异常）: {e}")
        return accounts


def _e_verdicts(verdicts: dict) -> str:
    """审计摘要：verdict 分布（bot/suspicious 计数）"""
    from collections import Counter
    c = Counter(v.get("verdict") for v in (verdicts or {}).values())
    return ", ".join(f"{k}={v}" for k, v in c.items() if v)


def _collect_identity_multisource(username: str, consent: bool, max_results: int):
    """v2.1.0 双源聚合采集：Maigret 与 Sherlock 都有效时分别执行，经
    identity_aggregator 去重/交叉确认/误报抑制；任一源失败不影响另一源。

    返回 (accounts, used_sources:list, trail:list[(src,status)])。
    - 两源均不可用/全失败 → (None, [], trail)，调用方回退单源代偿链。
    - 仅一源成功 → 用该源结果（aggregator 亦兼容单源）。
    """
    try:
        from core.capability_registry import is_effective_enabled
        from core.identity_aggregator import aggregate
    except Exception:
        return None, [], []

    sources = []
    if is_effective_enabled("Maigret"):
        sources.append("Maigret")
    if is_effective_enabled("Sherlock"):
        sources.append("Sherlock")
    if not sources:
        return None, [], []

    def _run(src):
        if src == "Maigret":
            from maigret_client import search as m_search
            return m_search(username, consent=consent, max_sites=100, timeout=180)
        from sherlock_client import search as s_search
        return s_search(username, consent=consent, timeout=120)

    results_by_source = {}
    trail = []
    for src in sources:
        try:
            res = _run(src)
            if res:
                results_by_source[src] = res
                trail.append((src, f"ok:{len(res)}"))
            else:
                trail.append((src, "empty"))
        except Exception as e:  # 单源失败不拖垮另一源
            trail.append((src, f"fail:{type(e).__name__}"))

    if not results_by_source:
        return None, [], trail

    try:
        agg = aggregate(results_by_source)
        accounts = agg.get("accounts", [])
        log.info(f"[身份归因] 双源聚合 raw={agg['stats']['raw_total']} "
                 f"deduped={agg['stats']['deduped_total']} "
                 f"cross={agg['stats']['cross_confirmed']} "
                 f"weak={agg['stats']['weak_single']} "
                 f"dropped_fp={agg['stats']['dropped_fp']} "
                 f"region={agg['stats']['by_region']}")
        return accounts, list(results_by_source.keys()), trail
    except Exception as e:
        log.warning(f"[身份归因] 聚合失败，回退拼接单源结果: {e}")
        flat = [a for lst in results_by_source.values() for a in lst]
        return flat, list(results_by_source.keys()), trail


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

    # v2.1.0：优先双源聚合（Maigret+Sherlock 交叉确认/去重/误报抑制）；
    # 两源均无有效结果时，回退原 compensate 单源代偿链（含 manual_review 缺口标记）。
    accounts, used_sources, multi_trail = _collect_identity_multisource(
        username, consent, max_results)
    if accounts:
        _audit_identity(
            f"identity_multisource user={username} used={used_sources} trail={multi_trail}")
    else:
        from capability_compensator import compensate, audit_trail
        res = compensate("Maigret", handlers, username, max_results=max_results)
        _audit_identity(audit_trail(res))
        if res.result is None:
            return []
        accounts = res.result if isinstance(res.result, list) else []
        used_sources = [res.used] if res.used else []
        if res.gap_flag:
            # 仅缺口标记，不包装为锚点（避免误导）
            log.warning(f"[身份归因] 能力链耗尽，标记需人工核实: {username}")
            return []

    # T1/G3：发现→验证闭环（AccountTrustScorer 批量人因评分，验证为增强不阻断）
    accounts = _verify_accounts(accounts)

    # T4/G1：发现层交叉命中补齐（同名跨平台数，供三因子融合 A 因子）
    try:
        accounts = _augment_cross_matches(accounts)
    except Exception:
        log.debug("[身份归因] cross 补齐跳过（不影响主链）")

    # T4：三因子置信度融合锚点构建（新增字段，不覆盖原字段；异常降级原输出）
    anchors = _build_identity_anchors(accounts, username, max_results)
    via = "+".join(used_sources) if used_sources else "capability-chain"
    log.info(f"[身份归因] '{username}' → {len(anchors)} 个账号锚点（via {via}）")
    return anchors
