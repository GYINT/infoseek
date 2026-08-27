#!/usr/bin/env python3
"""
core/capability_registry.py — Infoseek 统一外部能力注册表（M0.2.5）

声明式能力管理：所有外部能力在 capabilities/registry.yaml 声明，
代码零改动接入。本模块负责加载 / 校验 / 查询 / 启用判定。

核心能力：
  - load_registry()            加载 YAML（PyYAML 缺失时回退内嵌默认）
  - get_capability(name)       取单条声明
  - list_capabilities(kind)    按族列举
  - is_enabled(name)           综合判定（registry.enabled ∩ env 覆盖 ∩ consent 占位）
  - requires_consent(name)     是否需合法用途授权
  - degrade_chain(name)        展开 degrade_to 链（去重、防环）
  - consent_granted(name)      运行期授权态（由调用方经合规闸口写入）

设计约束：
  - 零第三方硬依赖（PyYAML 缺失仅影响外部 YAML 加载，回退内嵌）
  - 与 engine_lifecycle 解耦：本模块只管"声明/启用"，健康/配额由 lifecycle 负责
  - 合规优先：requires_consent 能力在授权前 is_effective_enabled() 恒为 False
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

_CORE_DIR = Path(__file__).parent
_REGISTRY_PATH = _CORE_DIR.parent / "capabilities" / "registry.yaml"

# 内嵌默认（与 capabilities/registry.yaml 同步；PyYAML 缺失时回退）
_DEFAULT_REGISTRY = {
    "version": 1,
    "capabilities": [
        {"name": "QVeris", "kind": "structured_data_api", "enabled": True,
         "requires_consent": False, "auth_env": "QVERIS_API_KEY", "weight": 0.9,
         "cost_model": "credits", "health_probe": "engine_lifecycle",
         "degrade_to": ["Exa", "Tavily"]},
        {"name": "Maigret", "kind": "identity_attribution", "enabled": False,
         "requires_consent": True, "auth_env": "", "weight": 0.9,
         "cost_model": "none", "cli": "maigret", "health_probe": "engine_lifecycle",
         "degrade_to": ["Sherlock", "manual_review"],
         "venv_hint": "pip install maigret（隔离 venv 推荐）"},
        {"name": "Sherlock", "kind": "identity_attribution", "enabled": False,
         "requires_consent": True, "auth_env": "", "weight": 0.85,
         "cost_model": "none", "cli": "sherlock", "health_probe": "engine_lifecycle",
         "degrade_to": ["manual_review"],
         "venv_hint": "pip install sherlock-project（隔离 venv 推荐）"},
        {"name": "manual_review", "kind": "graceful_fallback", "enabled": True,
         "requires_consent": False, "auth_env": "", "weight": 0.0,
         "cost_model": "none", "health_probe": "none", "degrade_to": []},
    ],
}

_lock = threading.Lock()
_cache: Optional[Dict] = None
# 运行期授权态：cap_name -> bool（由合规闸口写入，非持久）
_consent_state: Dict[str, bool] = {}


def _load_raw() -> Dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        data = None
        try:
            if _REGISTRY_PATH.exists():
                try:
                    import yaml  # PyYAML（requirements.txt 已声明）
                    data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
                except ImportError:
                    data = None  # 回退内嵌
        except Exception:
            data = None
        _cache = data if (isinstance(data, dict) and data.get("capabilities")) else _DEFAULT_REGISTRY
        return _cache


def _index() -> Dict[str, Dict]:
    raw = _load_raw()
    return {c["name"]: c for c in raw.get("capabilities", [])}


def get_capability(name: str) -> Optional[Dict]:
    return _index().get(name)


def list_capabilities(kind: Optional[str] = None) -> List[Dict]:
    caps = list(_index().values())
    if kind:
        caps = [c for c in caps if c.get("kind") == kind]
    return caps


def requires_consent(name: str) -> bool:
    cap = get_capability(name)
    return bool(cap and cap.get("requires_consent"))


def grant_consent(name: str) -> None:
    """合规闸口：记录运行期授权（非持久，进程级）。"""
    with _lock:
        _consent_state[name] = True


def revoke_consent(name: str) -> None:
    with _lock:
        _consent_state.pop(name, None)


def consent_granted(name: str) -> bool:
    with _lock:
        return bool(_consent_state.get(name))


def _env_override(name: str) -> Optional[bool]:
    """INFOSEEK_ENABLE_<NAME> 显式覆盖（1/true/on 启用，0/false/off 禁用）。"""
    env = os.environ.get(f"INFOSEEK_ENABLE_{name.upper()}")
    if env is None:
        return None
    return env.lower() not in ("0", "false", "no", "off")


def is_enabled(name: str) -> bool:
    """声明层启用判定（不含 consent / 运行期健康）。"""
    cap = get_capability(name)
    if not cap:
        return False
    ov = _env_override(name)
    if ov is not None:
        return ov
    return bool(cap.get("enabled", False))


def is_effective_enabled(name: str) -> bool:
    """综合启用判定：声明启用 ∩ env 覆盖 ∩ （无需 consent 或 已授权）。"""
    if not is_enabled(name):
        return False
    if requires_consent(name) and not consent_granted(name):
        return False
    return True


def degrade_chain(name: str, _seen: Optional[set] = None) -> List[str]:
    """展开 degrade_to 链（去重、防自环），含起点。"""
    seen = _seen if _seen is not None else set()
    if name in seen:
        return []
    seen.add(name)
    chain = [name]
    cap = get_capability(name)
    if cap:
        for nxt in cap.get("degrade_to", []):
            if nxt in seen:
                continue
            chain.extend(degrade_chain(nxt, seen))
    return chain


def reload() -> None:
    """清空缓存（测试/热更新用）。"""
    global _cache
    with _lock:
        _cache = None


if __name__ == "__main__":
    import json
    print(json.dumps({
        "caps": [c["name"] for c in list_capabilities()],
        "maigret_enabled": is_enabled("Maigret"),
        "maigret_effective": is_effective_enabled("Maigret"),
        "maigret_degrade": degrade_chain("Maigret"),
    }, ensure_ascii=False, indent=2))
