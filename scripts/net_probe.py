#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/net_probe.py — Infoseek 网络探测唯一真源（GA12 / v2.0.0）

收口此前两处重复实现（engine_router.probe_http / search_engine_health.probe_http），
并为「网络边界门控」（boundary_gate）提供 host 级可达性判定。

设计原则（见 references/P3-设计方案评估简报-GA11GA12.md §2）：
  - 不绕过网络边界（不做代理/VPN/反爬对抗）；
  - 不把受限能力伪装为可用（显式声明 + 留痕）；
  - 零第三方硬依赖（仅 urllib 标准库）；
  - 探测幂等 + TTL 缓存（进程内 + 磁盘），避免重复探测拖垮调研链。

核心 API：
  probe_http(url, timeout=5.0, *, reject_status=True) -> (ok, elapsed, err)
      与历史两处 probe_http 同构（reject_status=True 时严格 200）。
  probe_host(host, timeout=5.0, *, force=False, ttl=None) -> dict
      host 级可达性：能拿到任一 HTTP 响应（含 3xx/4xx）即视为「网络可达」；
      仅 DNS 失败 / 连接拒绝 / 超时 才判不可达。
  check_hosts(hosts, timeout=5.0, *, force=False) -> {host: result}
      批量并发探测（线程池）。
  reset_cache() / 边界测试用
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_UA = "Mozilla/5.0 (compatible; infoseek-net-probe/2.0)"

# ── TTL ────────────────────────────────────────────────────
_DEFAULT_TTL = 600


def _probe_ttl() -> int:
    """host 探测缓存 TTL：env INFOSEEK_NET_PROBE_TTL > 默认 600s。"""
    v = os.environ.get("INFOSEEK_NET_PROBE_TTL", "")
    if v:
        try:
            return max(0, int(v))
        except ValueError:
            pass
    return _DEFAULT_TTL


# ── 磁盘缓存目录（复用 state_dir，失败则退到 ~/.infoseek） ─
def _data_dir() -> Path:
    try:
        from core.state_dir import get_data_dir  # type: ignore
    except Exception:
        try:
            from state_dir import get_data_dir  # type: ignore
        except Exception:
            return Path(os.path.expanduser("~/.infoseek"))
    try:
        return Path(get_data_dir())
    except Exception:
        return Path(os.path.expanduser("~/.infoseek"))


_CACHE_PATH = _data_dir() / "net_probe_cache.json"

# 进程内缓存：{host: {"ts": float, "ok": bool, "elapsed": float, "err": str}}
_MEM: Dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════
# 基础探测
# ═══════════════════════════════════════════════════════════

def probe_http(url: str, timeout: float = 5.0, *,
               reject_status: bool = True) -> Tuple[bool, float, str]:
    """HTTP GET 连通探测，返回 (ok, elapsed_seconds, err)。

    reject_status=True（默认，保持历史语义）：仅 HTTP 200 判 ok；
    reject_status=False：拿到任意 HTTP 响应（含 3xx/4xx/5xx）即判 ok，
        用于 host 级可达性判定（握手成功 ⇒ 网络层可达）。
    """
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = (resp.status == 200) if reject_status else True
            return ok, round(time.time() - t0, 2), ""
    except urllib.error.HTTPError as e:
        # 已收到服务器响应：reject_status=False 时视为可达
        if not reject_status:
            return True, round(time.time() - t0, 2), ""
        return False, round(time.time() - t0, 2), f"HTTP {e.code}"
    except Exception as e:
        return False, round(time.time() - t0, 2), str(e)[:120]


def _normalize_host(host: str) -> str:
    """规整 host：去 scheme / 路径 / 端口外空白，小写。"""
    h = (host or "").strip().lower()
    for scheme in ("https://", "http://"):
        if h.startswith(scheme):
            h = h[len(scheme):]
    h = h.split("/", 1)[0].split("?", 1)[0]
    return h


def probe_host(host: str, timeout: float = 5.0, *,
               force: bool = False, ttl: Optional[int] = None) -> dict:
    """探测单个 host 的网络可达性（带 TTL 缓存）。

    返回 {"host", "ok", "elapsed", "err", "ts", "cached"}。
    """
    h = _normalize_host(host)
    if not h:
        return {"host": h, "ok": False, "elapsed": 0.0, "err": "empty host",
                "ts": time.time(), "cached": False}

    ttl = _probe_ttl() if ttl is None else ttl
    now = time.time()

    if not force:
        cached = _MEM.get(h)
        if cached and now - cached.get("ts", 0) <= ttl:
            return dict(cached, cached=True)
        disk = _load_disk().get(h)
        if disk and now - disk.get("ts", 0) <= ttl:
            _MEM[h] = disk
            return dict(disk, cached=True)

    # 真实探测：先 https，握手拿到响应（含错误码）即可；https 失败且非超时/拒绝再退 http
    ok, elapsed, err = _reach(f"https://{h}/", timeout)
    if not ok and _may_retry_http(err):
        ok2, elapsed2, err2 = _reach(f"http://{h}/", min(timeout, 4.0))
        if ok2:
            ok, elapsed, err = True, elapsed2, ""
        else:
            err = err or err2

    res = {"host": h, "ok": bool(ok), "elapsed": float(elapsed),
           "err": err, "ts": now, "cached": False}
    _MEM[h] = res
    _save_disk(h, res)
    return dict(res)


def _reach(url: str, timeout: float) -> Tuple[bool, float, str]:
    """host 可达性：任意 HTTP 响应（含 3xx/4xx）⇒ 可达；仅网络层错误 ⇒ 不可达。"""
    return probe_http(url, timeout, reject_status=False)


def _may_retry_http(err: str) -> bool:
    """https 失败时是否值得退 http：SSL/协议错误可退；DNS/拒绝/超时不退（http 同样不通）。"""
    e = (err or "").lower()
    if not e:
        return False
    network_dead = ("timed out" in e or "timeout" in e or "refused" in e
                    or "name or service not known" in e or "getaddrinfo" in e
                    or "no route" in e or "unreachable" in e)
    return not network_dead


def check_hosts(hosts: List[str], timeout: float = 5.0, *,
                force: bool = False, workers: int = 8) -> Dict[str, dict]:
    """批量并发探测多个 host，返回 {host: result}。"""
    uniq = []
    for h in hosts:
        nh = _normalize_host(h)
        if nh and nh not in uniq:
            uniq.append(nh)
    if not uniq:
        return {}
    results: Dict[str, dict] = {}
    # 全部命中缓存时不起线程
    if not force:
        miss = []
        now = time.time()
        disk = _load_disk()
        for h in uniq:
            c = _MEM.get(h)
            if c and now - c.get("ts", 0) <= _probe_ttl():
                results[h] = dict(c, cached=True)
            elif disk.get(h) and now - disk[h].get("ts", 0) <= _probe_ttl():
                _MEM[h] = disk[h]
                results[h] = dict(disk[h], cached=True)
            else:
                miss.append(h)
    else:
        miss = uniq
    if miss:
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(miss)))) as ex:
            futs = {ex.submit(probe_host, h, timeout, True): h for h in miss}
            for fut in futs:
                h = futs[fut]
                try:
                    results[h] = fut.result()
                except Exception as e:  # 探测自身异常不炸调用方（fail-safe）
                    results[h] = {"host": h, "ok": False, "elapsed": 0.0,
                                  "err": f"probe_error:{type(e).__name__}",
                                  "ts": time.time(), "cached": False}
    return results


# ═══════════════════════════════════════════════════════════
# 磁盘缓存（崩溃 / 跨进程复用）
# ═══════════════════════════════════════════════════════════

def _load_disk() -> Dict[str, dict]:
    try:
        if _CACHE_PATH.exists():
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_disk(host: str, res: dict) -> None:
    try:
        data = _load_disk()
        data[host] = res
        # 控制缓存体积：仅保留最近 200 条
        if len(data) > 200:
            keep = sorted(data.items(), key=lambda kv: kv[1].get("ts", 0))[-200:]
            data = dict(keep)
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception:
        pass  # 缓存写失败不影响判定


def reset_cache() -> None:
    """清空进程内缓存（测试隔离用；不清磁盘）。"""
    _MEM.clear()


def clear_disk_cache() -> None:
    """删除磁盘缓存（测试 / 强制刷新用）。"""
    _MEM.clear()
    try:
        if _CACHE_PATH.exists():
            _CACHE_PATH.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="infoseek host 网络可达性探测（GA12）")
    ap.add_argument("hosts", nargs="*", help="待探测 host（可多个）")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--force", action="store_true", help="忽略缓存强制探测")
    args = ap.parse_args()
    targets = args.hosts or ["www.wikidata.org", "cn.bing.com", "api.github.com"]
    out = check_hosts(targets, timeout=args.timeout, force=args.force)
    print(json.dumps(out, ensure_ascii=False, indent=2))
