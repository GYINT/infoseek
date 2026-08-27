#!/usr/bin/env python3
"""
scripts/sherlock_client.py — Sherlock 身份归因客户端（M0.3，默认 OFF）

Sherlock（sherlock-project）：用户名 → 平台存在性快速枚举（~400 站点，
亚分钟级、低依赖、支持 --tor）。定位 Maigret 的"快扫前置"，
与 Maigret 同族（identity_attribution），经注册表 degrade_to 形成替代链。

设计同 maigret_client：默认 OFF + 合规闸 + subprocess CLI + 错误分类复用 lifecycle。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List

log = logging.getLogger("infoseek.sherlock")

try:
    from core.capability_errors import CapabilityError, ConsentRequired, CapabilityUnavailable
    from core.capability_registry import (
        get_capability, grant_consent, is_effective_enabled, is_enabled,
        requires_consent,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "core"))
    from capability_errors import CapabilityError, ConsentRequired, CapabilityUnavailable
    from capability_registry import (
        get_capability, grant_consent, is_effective_enabled, is_enabled,
        requires_consent,
    )

CAP_NAME = "Sherlock"


def _resolve_cli() -> List[str]:
    """定位 sherlock 可执行：PATH → 隔离 venv（INFOSEEK_SHERLOCK_VENV）。

    返回命令片段（list），调用方以 cli + [username, ...] 拼接，
    兼容 Windows 控制台脚本（sherlock.exe）与模块式回退。
    """
    cli = shutil.which("sherlock")
    if cli:
        return [cli]
    venv = os.environ.get("INFOSEEK_SHERLOCK_VENV")
    if venv:
        scripts = __import__("pathlib").Path(venv) / ("Scripts" if sys.platform == "win32" else "bin")
        for name in ("sherlock", "sherlock.exe"):
            cand = scripts / name
            if cand.exists():
                return [str(cand)]
        py = scripts / ("python.exe" if sys.platform == "win32" else "python")
        if py.exists():
            return [str(py), "-m", "sherlock"]
    return ["sherlock"]


def _consent_gate(consent: bool) -> None:
    if requires_consent(CAP_NAME):
        if consent:
            grant_consent(CAP_NAME)
        elif not is_effective_enabled(CAP_NAME):
            raise ConsentRequired(CAP_NAME)


def search(username: str,
           consent: bool = False,
           timeout: int = 120,
           tor: bool = False) -> List[Dict]:
    """对公开用户名做存在性快速枚举（默认不递归、低开销）。

    返回账号列表：{platform, url, username, confidence, source}
    未启用 / 未授权 / CLI 缺失 → 返回 []（安全降级）
    """
    if not is_enabled(CAP_NAME):
        log.debug(f"[{CAP_NAME}] 未启用（默认 OFF），跳过")
        return []
    # 合规闸口（未授权上抛 ConsentRequired，由 search_web / pipeline 捕获降级）
    _consent_gate(consent)

    cli = _resolve_cli()
    cmd = cli + [username, "--json"]
    if tor:
        cmd.append("--tor")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise CapabilityUnavailable(CAP_NAME, "CLI 未安装（隔离 venv 未配置）")
    except subprocess.TimeoutExpired:
        raise CapabilityError(f"{CAP_NAME} 超时（>{timeout}s）", code=0)
    except Exception as e:
        raise CapabilityError(f"{CAP_NAME} 执行异常: {e}", code=0, cause=e)

    if proc.returncode != 0:
        log.warning(f"[{CAP_NAME}] CLI 返回 {proc.returncode}: {(proc.stderr or '')[:200]}")

    return _parse_output(proc.stdout)


def _parse_output(stdout: str) -> List[Dict]:
    if not stdout or not stdout.strip():
        return []
    out: List[Dict] = []
    try:
        data = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return out
    # sherlock --json 结构：{ "username": { "site1": {"url":..., "status":"Claimed"}, ... } }
    if isinstance(data, dict):
        for site, info in data.items():
            if not isinstance(info, dict):
                continue
            status = info.get("status", "")
            url = info.get("url", "")
            if status in ("Claimed", "Available") and url:
                out.append({
                    "platform": site,
                    "url": url,
                    "username": "",
                    "fullname": "",
                    "site_rank": 0,
                    "confidence": 0.85 if status == "Claimed" else 0.4,
                    "source": CAP_NAME,
                })
    return out


def search_web(username: str, **kw) -> List[Dict]:
    try:
        return search(username, **kw)
    except (ConsentRequired, CapabilityUnavailable, CapabilityError):
        return []
