#!/usr/bin/env python3
"""mcp_tools_search.py — Infoseek MCP 搜索/抓取工具（G11 拆分 v1.0.1）"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_tools_common import INFOSEEK_ROOT


# ══ 以下函数由 G11 拆分脚本从 infoseek_mcp_server.py 提取（v1.0.1）══

def tool_search_anchors(args: Dict) -> Dict:
    """锚点发现（v1.0.1 PATCH: 接入 pipeline.search_web 真实搜索链）

    此前为壳实现（仅返回提示文案），现调用 infoseek_pipeline.search_web
    执行 DDG/Bing/Jina/Wikipedia 多引擎并行搜索，并对结果做主题相关性过滤。
    """
    subject = args['subject']
    depth = args.get('depth', 2)
    max_results = args.get('max_results', 8)

    try:
        from infoseek_pipeline import search_web
        results = search_web(subject, max_results=max_results)
        anchors = []
        for r in results:
            anchors.append({
                'title': r.get('title', ''),
                'url': r.get('url', ''),
                'engine': r.get('engine', ''),
                'snippet': r.get('snippet', ''),
            })
        return {
            "subject": subject,
            "depth": depth,
            "sources": args.get('sources', ['web']),
            "anchors_count": len(anchors),
            "anchors": anchors,
            "status": "ok",
            "message": f"多引擎搜索完成，发现 {len(anchors)} 个候选锚点（已按主题相关性过滤）。",
            "next_steps": [
                "1. 用 score_source 五维评分筛选（≥70 入采集队列）",
                "2. 用 fetch_content 抓取正文",
                "3. 用 fuse_analysis 做跨源融合"
            ],
        }
    except Exception as e:
        return {
            "subject": subject,
            "depth": depth,
            "anchors_count": 0,
            "anchors": [],
            "status": "error",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "message": "搜索链执行失败，请检查网络或搜索配置。",
        }


def tool_fetch_content(args: Dict) -> Dict:
    """内容采集（v1.5.0+ 基础，v1.7.3 v1 增强，v1.8.0 v2 增强，v1.9.0 v3 多层递归）

    v1.9.0 v3 新增:
      - chain_strategy="recursive": 多层递归追踪（_fetch_chain_v3）
      - max_chain_depth: 1-3 递归深度（默认 1）
      - 见 _fetch_chain_v3() 防环 + 深度折扣

    v1.8.0 v2 保留:
      - chain_strategy="discover": 仅发现链接
      - chain_strategy="fetch": 逐个抓取摘要（1 层）
      - chain_strategy="graph": 生成 dot 引用图
      - chain_limit: 链式追踪最大 URL 数
      - subject: 引用相关性评分

    v1.7.3 v1 保留:
      - follow_links: 是否启用链式追踪
      - max_depth: 1-3 深度

    v1.0.1 PATCH (P0-2): 实现 L1 静态正文抓取（此前不抓正文，返回空内容）。
      - follow_links=False（默认）时也抓取页面正文，返回 content 字段
      - 正文提取：<title> + <h1>-<h3> + <p> 段落拼接（去 script/style）
    """
    import re as re_mod
    import urllib.request

    url = args['url']
    fmt = args.get('format', 'md')
    # v1.2.x L3/L4: 客户端可请求 extraction_level 1/2/3/4（钳制到合法范围）
    req_level = args.get('extraction_level', 1)
    try:
        req_level = int(req_level)
    except (TypeError, ValueError):
        req_level = 1
    req_level = max(1, min(4, req_level))
    max_retries = args.get('max_retries', 3)
    follow_links = args.get('follow_links', False)
    max_depth = args.get('max_depth', 1)
    chain_strategy = args.get('chain_strategy', 'discover')
    chain_limit = args.get('chain_limit', 5)
    subject = args.get('subject', '')
    max_chain_depth = args.get('max_chain_depth', 1)  # v1.9.0 新增

    extraction_strategy = [
        "Level 1: 静态页面 fetch",
        "Level 2: 反爬兜底（浏览器渲染）",
        "Level 3: 凭证辅助（API key）",
        "Level 4: 多媒体处理（截图/OCR）"
    ]

    related_links = []
    citation_graph = None
    chain_tracking_error = None

    # v1.0.1 PATCH (P0-2): L1 静态正文抓取（无论是否 follow_links 都执行）
    content = ""
    page_title = ""
    fetch_error = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Infoseek/1.0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            html = raw.decode("utf-8", errors="ignore")
            page_title = _extract_title_from_html(html)
            content = _extract_main_text(html)
    except Exception as e:
        fetch_error = f"{type(e).__name__}: {str(e)[:100]}"

    # v1.2.x L3: 请求级别 ≥3 且 L1 正文不足 → 凭证辅助抓取（KeyManager 注入，失败降级）
    extraction_level = 1
    if req_level >= 3 and len(content.strip()) < 100:
        host = ''
        try:
            host = url.split('//', 1)[1].split('/', 1)[0].split(':')[0]
        except IndexError:
            host = ''
        cred_html = _fetch_with_credential(url, host)
        if cred_html:
            content = cred_html
            page_title = page_title or _extract_title_from_html(cred_html) or page_title
            extraction_level = 3
            fetch_error = None

    # v1.0.1 C2 (L2): L1/L3 失败或正文过短 → playwright 无头渲染增强（可选，失败静默降级 L1）
    if len(content.strip()) < 100:
        render_html = _fetch_render_with_playwright(url)
        if render_html:
            content = render_html
            page_title = page_title or _extract_title_from_html(render_html) or page_title
            extraction_level = 2
            fetch_error = None

    if follow_links and max_depth > 0:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Infoseek/1.9.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                base_title = _extract_title_from_html(html)
                raw_links = re_mod.findall(r"href=[\\'](https?://[^\\']+)[\\']", html)
                seen = set()
                for link in raw_links:
                    if link in seen or link == url:
                        continue
                    seen.add(link)
                    related_links.append({"url": link, "depth": 1, "title": ""})
                    if len(related_links) >= chain_limit:
                        break
                if chain_strategy == "fetch":
                    related_links = _fetch_chain_v2(related_links, chain_limit, subject)
                elif chain_strategy == "graph":
                    citation_graph = _build_citation_graph(url, base_title, related_links)
                elif chain_strategy == "recursive":
                    # v1.9.0 v3 多层递归
                    related_links = _fetch_chain_v3(
                        url,
                        current_depth=0,
                        max_chain_depth=max_chain_depth,
                        seen=set(),
                        subject=subject,
                        chain_limit=chain_limit,
                        depth_discount=0.7,
                    )
                    # 同时生成 dot 引用图（递归结果）
                    citation_graph = _build_citation_graph(url, base_title, related_links)
        except Exception as e:
            chain_tracking_error = f"{type(e).__name__}: {str(e)[:100]}"
            related_links = []
            citation_graph = None

    result = {
        "url": url,
        "format": fmt,
        "max_retries": max_retries,
        "title": page_title,
        "content": content,  # v1.0.1 PATCH (P0-2): L1 静态正文；C2: L2 渲染增强；L3 凭证辅助
        "content_length": len(content),
        "extraction_level": extraction_level,  # 1=L1 静态 / 2=L2 渲染 / 3=L3 凭证 / 4=L4 多媒体
        "fetch_error": fetch_error,
        "extraction_strategy": extraction_strategy,
        "chain_tracking_v3": {  # v1.9.0 改名
            "enabled": follow_links,
            "strategy": chain_strategy,
            "max_depth": max_depth,
            "max_chain_depth": max_chain_depth,  # v1.9.0 新增
            "chain_limit": chain_limit,
            "subject": subject or "(none)",
            "discovered_count": len(related_links),
            "discovered_links": related_links[:chain_limit],
            "citation_graph_dot": citation_graph,
            "error": chain_tracking_error,
            "version": "1.9.0",
        }
    }

    # v1.2.x L4: 请求级别 ≥4 且命中多媒体 URL → 附加统一 multimodal chunk
    if req_level >= 4:
        media = _probe_media(url)
        if media:
            result['media'] = media
            result['multimodal'] = True
            result['extraction_level'] = 4
    return result


def _fetch_render_with_playwright(url: str, timeout: int = 15) -> str:
    """L2 抓取（v1.0.1 C2 落地）：playwright 无头浏览器渲染 → 提取正文。

    可选依赖 + 可选浏览器：playwright 库缺失 / chromium 未安装 / 启动失败 →
    返回空串（调用方自动降级 L1，零侵入）。JS 渲染 / SPA / 反爬页面走此路径。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Infoseek/1.0.1")
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # 等 JS 渲染
                html = page.content()
            finally:
                browser.close()
        return _extract_main_text(html, max_chars=12000)
    except Exception:
        return ""


def _get_host_credential(host: str) -> str:
    """L3 凭证获取（v1.2.x）：按 host 从 KeyManager 查找凭证。

    明文禁令：凭证仅内存返回给 playwright 注入，不写入日志/文件/状态。
    KeyManager 缺失 / 无匹配凭证 → 返回 ""（调用方自动降级 L1/L2）。
    """
    if not host:
        return ""
    try:
        import sys as _s
        from pathlib import Path as _P
        _root = _P(__file__).parent.parent
        if str(_root) not in _s.path:
            _s.path.insert(0, str(_root))
        from core.key_manager import get_key
        candidates = [host]
        if '.' in host:
            candidates.append(host.split('.')[0])
        candidates.append('default')
        for provider in candidates:
            try:
                v = get_key(provider)
                if v:
                    return v
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _fetch_with_credential(url: str, host: str, timeout: int = 15) -> str:
    """L3 抓取（v1.2.x）：playwright 带凭证渲染 → 提取正文。

    凭证注入两种形态：
      - `Authorization: Bearer <cred>`（KeyManager 存 API key 时）
      - `Cookie: <name>=<value>` 前缀（登录源，解析为 playwright cookie）
    凭证缺失 / playwright 不可用 / 启动失败 → 返回 ""（降级 L1/L2，零侵入）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    cred = _get_host_credential(host)
    if not cred:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                extra_headers = {}
                cookie = None
                if cred.startswith('Cookie:'):
                    cookie = cred[7:].strip()
                else:
                    extra_headers['Authorization'] = f'Bearer {cred}'
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Infoseek/1.0.1",
                    extra_http_headers=extra_headers,
                )
                if cookie and host:
                    try:
                        name = cookie.split('=', 1)[0].strip()
                        value = cookie.split('=', 1)[1].split(';', 1)[0].strip()
                        ctx.add_cookies([{
                            'name': name, 'value': value,
                            'domain': host, 'path': '/', 'url': f'https://{host}/',
                        }])
                    except Exception:
                        pass
                page = ctx.new_page()
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # 等 JS 渲染
                html = page.content()
                ctx.close()
            finally:
                browser.close()
        return _extract_main_text(html, max_chars=12000)
    except Exception:
        return ""


_MEDIA_EXT = {
    'image': ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.avif', '.ico'),
    'video': ('.mp4', '.webm', '.mov', '.avi', '.mkv', '.m3u8', '.flv', '.wmv'),
    'audio': ('.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma', '.opus'),
}


def _classify_media(url: str, content_type: str = '') -> Optional[str]:
    """L4 多媒体分类（v1.2.x）：优先 Content-Type（image/video/audio 前缀），
    回退 URL 扩展名（去 query）。非媒体 → None。
    """
    if content_type:
        ct = content_type.lower().split(';')[0].strip()
        for kind in ('image', 'video', 'audio'):
            if ct.startswith(kind):
                return kind
    path = (url.split('?', 1)[0]).lower()
    for kind, exts in _MEDIA_EXT.items():
        if path.endswith(exts):
            return kind
    return None


def _probe_media(url: str) -> Optional[Dict]:
    """L4 多媒体探测（v1.2.x）：分类 + 元信息（format/size/content-type）。

    网络不可达时仅按 URL 扩展名分类（format 由扩展名推断）；
    whisper 转录为可选能力：未启用/不可用 → transcript_available=False（降级不崩）。
    """
    try:
        kind = _classify_media(url)
        if not kind:
            return None
        meta = {'format': None, 'size_bytes': None, 'content_type': None}
        try:
            import urllib.request
            req = urllib.request.Request(
                url, method='HEAD',
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Infoseek/1.0.1'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ct = resp.headers.get('Content-Type', '')
                kind2 = _classify_media(url, ct)
                if kind2:
                    kind = kind2
                meta['content_type'] = ct or None
                meta['format'] = (ct.split('/')[-1].split(';')[0] if ct else None)
                try:
                    cl = resp.headers.get('Content-Length')
                    meta['size_bytes'] = int(cl) if cl else None
                except (TypeError, ValueError):
                    meta['size_bytes'] = None
        except Exception:
            pass
        if not meta['format']:
            fname = url.split('?', 1)[0].rsplit('/', 1)[-1]
            meta['format'] = fname.rsplit('.', 1)[-1].lower() if '.' in fname else None
        # whisper 转录占位：可选依赖，缺失时明确标记不可用（降级）
        transcript = None
        available = False
        try:
            import whisper  # noqa: F401  # 可选依赖
            if kind in ('video', 'audio'):
                available = True
        except ImportError:
            available = False
        return {
            'media_type': kind,
            'metadata': meta,
            'transcript': transcript,
            'transcript_available': available,
            'note': 'whisper 转录为可选能力；未启用时 transcript=None（降级）。',
        }
    except Exception:
        return None


def _extract_title_from_html(html: str) -> str:
    import re as re_mod
    m = re_mod.search(r"<title>([^<]+)</title>", html, re_mod.IGNORECASE)
    if m:
        return m.group(1).strip()[:100]
    m = re_mod.search(r"<h1[^>]*>([^<]+)</h1>", html, re_mod.IGNORECASE)
    if m:
        return m.group(1).strip()[:100]
    return "(no title)"


def _extract_main_text(html: str, max_chars: int = 8000) -> str:
    """L1 静态正文提取（v1.0.1 PATCH / P0-2）

    策略：去 script/style/nav 标签 → 收集 h1-h3 + p 文本 → 去空白合并。
    返回纯文本（截断到 max_chars）。无正文时返回空串。
    """
    import re as re_mod
    if not html:
        return ""
    try:
        # 1. 去掉脚本/样式/导航块
        html = re_mod.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>", " ", html)
        # 2. 段落/标题标签内文本（含属性）
        blocks = re_mod.findall(r"(?is)<(h1|h2|h3|p|li)[^>]*>(.*?)</\1>", html)
        parts = []
        for _tag, body in blocks:
            text = re_mod.sub(r"(?s)<[^>]+>", "", body)
            text = re_mod.sub(r"&nbsp;|&#160;", " ", text)
            text = re_mod.sub(r"&amp;", "&", text)
            text = re_mod.sub(r"&lt;", "<", text)
            text = re_mod.sub(r"&gt;", ">", text)
            text = re_mod.sub(r"\s+", " ", text).strip()
            if text:
                parts.append(text)
        joined = "\n".join(parts)
        if len(joined) > max_chars:
            joined = joined[:max_chars] + "\n...[truncated]"
        return joined
    except Exception:
        return ""


def _fetch_chain_v2(links: list, limit: int, subject: str = "") -> list:
    import urllib.request
    import urllib.error
    import re as re_mod
    try:
        from anchor_adapter import _jaccard_similarity
        has_jaccard = True
    except ImportError:
        has_jaccard = False

    if not links:
        return []
    results = []
    for link_dict in links[:limit]:
        link_url = link_dict["url"]
        try:
            req = urllib.request.Request(link_url, headers={"User-Agent": "Infoseek/1.8.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")[:50000]
                title = _extract_title_from_html(html)
                text = re_mod.sub(r"<[^>]+>", " ", html)
                text = re_mod.sub(r"\s+", " ", text).strip()[:300]
                relevance = 0
                if has_jaccard and subject:
                    relevance = _jaccard_similarity(text, subject)
                results.append({
                    "url": link_url,
                    "depth": link_dict.get("depth", 1),
                    "title": title,
                    "snippet": text + ("..." if len(text) >= 300 else ""),
                    "relevance_score": relevance,
                })
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            results.append({
                "url": link_url,
                "depth": link_dict.get("depth", 1),
                "title": "(fetch failed)",
                "snippet": "",
                "relevance_score": 0,
                "error": f"{type(e).__name__}: {str(e)[:60]}",
            })
        except Exception as e:
            results.append({
                "url": link_url,
                "depth": link_dict.get("depth", 1),
                "title": "(fetch error)",
                "snippet": "",
                "relevance_score": 0,
                "error": f"{type(e).__name__}: {str(e)[:60]}",
            })
    if subject:
        results.sort(key=lambda x: -x["relevance_score"])
    return results


def _fetch_chain_v3(
    seed_url: str,
    current_depth: int = 0,
    max_chain_depth: int = 1,
    seen: set = None,
    subject: str = "",
    chain_limit: int = 5,
    depth_discount: float = 0.7,
    budget_remaining: int = 50,
) -> list:
    """链式抓取 v3：多层递归追踪（v1.9.0 新增）

    算法：
      1. 若 current_depth > max_chain_depth: return []
      2. 若 seed_url in seen: return []（防环）
      3. fetch seed → extract links (top chain_limit)
      4. 对每个 link 递归调用：
         - current_depth + 1
         - 应用 depth_discount^depth 到 relevance_score
         - tag with depth marker
      5. seen.add(seed_url)
      6. 返回扁平化的链式结果

    防环：seen 集合全局
    评分折扣：每个深度 × 0.7（深 1 层保留 70%，深 2 层 49%）
    预算控制：budget_remaining 默认 50，每抓一个 URL -1
    """
    import urllib.request
    import urllib.error
    import re as re_mod

    if seen is None:
        seen = set()
    if current_depth > max_chain_depth:
        return []
    if seed_url in seen:
        return []
    if budget_remaining <= 0:
        return []

    seen.add(seed_url)
    budget_remaining -= 1

    results = []

    # 当前层：fetch + Jaccard
    html = ""
    try:
        req = urllib.request.Request(seed_url, headers={"User-Agent": "Infoseek/1.9.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")[:50000]
            title = _extract_title_from_html(html)
            text = re_mod.sub(r"<[^>]+>", " ", html)
            text = re_mod.sub(r"\\s+", " ", text).strip()[:300]

            try:
                sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
                from anchor_adapter import _jaccard_similarity
                relevance = _jaccard_similarity(text, subject) if subject else 0
            except (ImportError, Exception):
                relevance = 0

            # 深度折扣
            relevance = int(relevance * (depth_discount ** current_depth))

            results.append({
                "url": seed_url,
                "depth": current_depth,
                "title": title,
                "snippet": text + ("..." if len(text) >= 300 else ""),
                "relevance_score": relevance,
                "is_seed": current_depth == 0,
            })
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return []
    except Exception:
        return []

    # 递归下一层
    if current_depth < max_chain_depth and html:
        raw_links = re_mod.findall(r"""href=["']([^"']+)["']""", html)
        raw_links = [l for l in raw_links if l.startswith("http")]  # v1.9.0 PATCH: 仅保留绝对 URL
        seen_in_layer = set()
        layer_count = 0
        for link in raw_links:
            if link in seen or link in seen_in_layer or link == seed_url:
                continue
            seen_in_layer.add(link)
            if layer_count >= chain_limit:
                break

            sub_results = _fetch_chain_v3(
                link,
                current_depth + 1,
                max_chain_depth,
                seen,
                subject,
                chain_limit,
                depth_discount,
                budget_remaining,
            )
            results.extend(sub_results)
            layer_count += 1
            budget_remaining -= sum(1 for r in sub_results if r.get('is_seed', False))

    return results


def _build_citation_graph(root_url: str, root_title: str, refs: list) -> str:
    if not refs:
        return ""
    lines = [
        "digraph citations {",
        "  rankdir=LR;",
        '  node [shape=box, style=rounded, fontname="Helvetica"];',
        f'  "ROOT: {root_title[:50]}" [style=filled, fillcolor=lightblue];',
    ]
    for ref in refs:
        short_url = ref["url"][:60]
        label = ref.get("title", short_url)[:50]
        lines.append(f'  "{short_url}" [label="{label}"];')
        lines.append(f'  "ROOT: {root_title[:50]}" -> "{short_url}";')
    lines.append("}")
    return "\n".join(lines)

