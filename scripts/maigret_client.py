#!/usr/bin/env python3
"""
scripts/maigret_client.py — Maigret 身份归因客户端（M0.3，默认 OFF）

Maigret（soxoj/maigret，Sherlock fork）：用户名 → 全平台账号足迹发现
（3000+ 站点，递归搜索、profile 解析、关系图谱、ML 降误报）。

设计要点：
  - 默认 OFF：须经注册表 enabled ∩ consent 双闸口才运行（合规优先）
  - 懒加载 + subprocess 调用 CLI（隔离重依赖于主进程；不 import maigret 进 infoseek）
  - 错误分类复用 engine_lifecycle.classify（经 core.capability_errors.CapabilityError.code）
  - 无 key 需求；仅处理公开数据；opt-in
  - 结果回写 infoseek 锚点矩阵"身份归因平面 B"

安全/合规：
  - requires_consent=true → 未授权抛 ConsentRequired，绝不静默运行
  - 不递归、不抓个人页（默认 --no-extracting，仅存在性 + 基础 profile）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

log = logging.getLogger("infoseek.maigret")

try:
    from core.capability_errors import CapabilityError, ConsentRequired, CapabilityUnavailable
    from core.capability_registry import (
        get_capability, grant_consent, is_effective_enabled, is_enabled,
        requires_consent,
    )
except ImportError:  # 直接运行时兜底
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "core"))
    from capability_errors import CapabilityError, ConsentRequired, CapabilityUnavailable
    from capability_registry import (
        get_capability, grant_consent, is_effective_enabled, is_enabled,
        requires_consent,
    )

CAP_NAME = "Maigret"


def _resolve_cli() -> List[str]:
    """定位 maigret 可执行：PATH → 隔离 venv（INFOSEEK_MAIGRET_VENV）。

    返回命令片段（list，如 ['/venv/Scripts/maigret'] 或 ['/venv/python.exe','-m','maigret']），
    调用方以 cli + [username, ...] 拼接，避免 Windows 模块式回退出现含空格的单字符串。
    """
    cli = shutil.which("maigret")
    if cli:
        return [cli]
    venv = os.environ.get("INFOSEEK_MAIGRET_VENV")
    if venv:
        scripts = __import__("pathlib").Path(venv) / ("Scripts" if sys.platform == "win32" else "bin")
        for name in ("maigret", "maigret.exe"):
            cand = scripts / name
            if cand.exists():
                return [str(cand)]
        # 退回模块式调用（隔离 venv 的 python -m maigret）
        py = scripts / ("python.exe" if sys.platform == "win32" else "python")
        if py.exists():
            return [str(py), "-m", "maigret"]
    return ["maigret"]


def _consent_gate(consent: bool) -> None:
    if requires_consent(CAP_NAME):
        if consent:
            grant_consent(CAP_NAME)
        else:
            # 即便注册表声明启用，未授权也禁止运行
            if not is_effective_enabled(CAP_NAME):
                raise ConsentRequired(CAP_NAME)


def search(username: str,
           consent: bool = False,
           max_sites: int = 500,
           recursive: bool = False,
           timeout: int = 180,
           no_extract: bool = True) -> List[Dict]:
    """对公开用户名做身份归因发现（默认存在性 + 基础 profile，不递归/不抓个人页）。

    返回账号列表：{platform, url, username, fullname, site_rank, confidence, source}
    未启用 / 未授权 / CLI 缺失 → 返回 []（安全降级，不抛异常中断 pipeline）
    """
    # 1) 默认 OFF 闸口
    if not is_enabled(CAP_NAME):
        log.debug(f"[{CAP_NAME}] 未启用（默认 OFF），跳过")
        return []
    # 2) 合规闸口（未授权上抛 ConsentRequired，由 search_web / pipeline 捕获降级）
    _consent_gate(consent)

    cli = _resolve_cli()
    cmd = cli + [username, "--json"]
    if not recursive:
        cmd.append("--no-extracting")   # 不递归抓个人页，仅存在性
    if max_sites and max_sites > 0:
        cmd += ["-a", str(max_sites)]    # 限定站点数（默认 top 500），控耗时
    cmd += ["--skip-existing", "--print-found-only"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise CapabilityUnavailable(CAP_NAME, "CLI 未安装（隔离 venv 未配置）")
    except subprocess.TimeoutExpired:
        raise CapabilityError(f"{CAP_NAME} 超时（>{timeout}s）", code=0)
    except Exception as e:
        raise CapabilityError(f"{CAP_NAME} 执行异常: {e}", code=0, cause=e)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # maigret 偶发非零退出但仍有部分结果；尝试解析 stdout
        log.warning(f"[{CAP_NAME}] CLI 返回 {proc.returncode}: {err[:200]}")

    return _parse_output(proc.stdout)


def _parse_output(stdout: str) -> List[Dict]:
    """解析 maigret --json 输出（容错：NDJSON / JSON dict 均尝试）。"""
    if not stdout or not stdout.strip():
        return []
    out: List[Dict] = []
    text = stdout.strip()
    # 优先尝试整体 JSON（maigret 输出为单 dict，含 sites 列表）
    try:
        data = json.loads(text)
        sites = data.get("sites") if isinstance(data, dict) else None
        if sites:
            for s in sites:
                if s.get("status") in ("found", True) or s.get("url"):
                    out.append(_norm(s))
            return out
    except json.JSONDecodeError:
        pass
    # 回退逐行 NDJSON
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(s, dict) and (s.get("status") == "found" or s.get("url")):
            out.append(_norm(s))
    return out


def _norm(s: Dict) -> Dict:
    return {
        "platform": s.get("site_name") or s.get("site") or s.get("platform", ""),
        "url": s.get("url") or s.get("username_url") or "",
        "username": s.get("username") or "",
        "fullname": s.get("fullname") or "",
        "site_rank": s.get("rank") or s.get("site_rank") or 0,
        "confidence": 0.9 if s.get("status") == "found" else 0.5,
        "source": CAP_NAME,
    }


# 模块级便捷封装（供 pipeline 调用；consent 默认 False 安全）
def search_web(username: str, **kw) -> List[Dict]:
    try:
        return search(username, **kw)
    except (ConsentRequired, CapabilityUnavailable, CapabilityError):
        return []
