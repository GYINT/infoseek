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

import json, os, sys, time, logging
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# 导入单一真源模块
from anchor_adapter import infos_to_seek


# ═══════════════════════════════════════════════════════════════
# 阶段 0: 行业→锚点自动生成（新增, P0-A）
# ═══════════════════════════════════════════════════════════════

def industry_to_anchors(industry: str) -> list:
    """
    从行业名称自动生成锚点清单（替代 infos 的手动嗅探步骤）
    使用 web search 搜素行业关键词，收敛为锚点列表。

    输入: "量化交易"
    输出: [{name, platform, score, entry, entry_type}, ...]
    """
    log.info(f"行业嗅探: {industry}")
    # 第一轮：宽泛搜索 → 发现平台和话题
    import urllib.request, urllib.parse
    from urllib.error import URLError

    search_terms = [
        industry,
        f"{industry} 2026 最新",
        f"{industry} 文章 教程",
    ]

    anchors = []
    seen_urls = set()

    # C5: 搜索引擎降级链 — DuckDuckGo → Bing fallback
    search_engines = [
        {"name": "DuckDuckGo", "url_tpl": "https://api.duckduckgo.com/?q={q}&format=json&no_html=1"},
        {"name": "Bing", "url_tpl": "https://www.bing.com/search?q={q}&format=rss"},
    ]

    for term in search_terms:
        for engine in search_engines:
            try:
                time.sleep(1.5)
                encoded = urllib.parse.quote(term)
                url = engine["url_tpl"].replace("{q}", encoded)
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                resp = urllib.request.urlopen(req, timeout=10)
                if engine["name"] == "DuckDuckGo":
                    data = json.loads(resp.read().decode())
                    results = data.get('RelatedTopics', [])[:15]
                    for r in results:
                        text = r.get('Text', '')
                        first_url = r.get('FirstURL', '')
                        if first_url and first_url not in seen_urls:
                            seen_urls.add(first_url)
                            anchors.append({
                                "name": text[:80] if text else industry,
                                "platform": "web", "score": 70,
                                "entry": first_url, "entry_type": "URL"})
                elif engine["name"] == "Bing":
                    html = resp.read().decode('utf-8', errors='ignore')
                    import re
                    for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html):
                        href = m.group(1)
                        title = re.sub(r'<[^>]+>', '', m.group(2))[:80]
                        if href not in seen_urls and 'bing.com' not in href:
                            seen_urls.add(href)
                            anchors.append({
                                "name": title if title else industry,
                                "platform": "web", "score": 70,
                                "entry": href, "entry_type": "URL"})
                if anchors:
                    break  # 当前引擎有结果→不继续尝试下一个引擎
            except Exception as e:
                log.warning(f"[{engine['name']}] 搜素 '{term}' 失败: {e}")
                continue  # 尝试下一个引擎

    if not anchors:
        # 兜底: 至少返回一个演示锚点
        log.info("无搜素结果，返回演示锚点")
        anchors = [{"name": industry, "platform": "web", "score": 70,
                    "entry": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(industry)}",
                    "entry_type": "URL"}]

    log.info(f"行业嗅探完成: {len(anchors)} 个锚点")
    return anchors


# ═══════════════════════════════════════════════════════════════
# 阶段 0.5: 名称类锚点→URL自动搜索（新增, P0-B）
# ═══════════════════════════════════════════════════════════════

def search_name_to_url(name: str, platform: str = "") -> list:
    """
    将名称/频道名类锚点通过 web search 转换为 URL 列表。
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

    import urllib.request, urllib.parse
    from urllib.error import URLError

    for query in search_queries[:2]:  # 最多 2 轮搜索
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            for r in data.get('RelatedTopics', [])[:10]:
                first_url = r.get('FirstURL', '')
                text = r.get('Text', '')
                if first_url and first_url not in [x['url'] for x in results]:
                    results.append({"url": first_url, "title": text[:80],
                                    "score": 65})
        except Exception as e:
            log.warning(f"名称搜素 '{query}' 失败: {e}")

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

def run_pipeline(anchors: list, output_dir: str = "./outputs") -> dict:
    """批量执行锚点采集"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

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
        "version": "1.1.0",
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

        # 执行管道
        report = run_pipeline(anchors, args.output)

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
