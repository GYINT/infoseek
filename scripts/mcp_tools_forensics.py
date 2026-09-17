#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""account_forensics 工具处理器（v1.0.0 · FakeDetect 账号取证消费口）

与 identity_attribution 构成「发现→取证」双子工具：
  - identity_attribution : 已知用户名 → 发现跨平台账号（轻量人因验证）
  - account_forensics    : 显式投喂账号数据集 → 深度取证（L1统计+L2图结构+L3ML+时序同步）

合规红线（双闸口，显式报错而非静默返回空，Agent 可感知合规状态）：
  - INFOSEEK_ENABLE_FAKE_DETECT=1 显式启用（env 闸）
  - consent=true 用户显式授权（涉个人行为画像）
  - 注册表 FakeDetect 经 enabled ∩ consent 判定

输入:  {dataset: {meta:[{id,followers,following,posts,er,...}], likes:{id:[...]},
                  growth:{id:[...]}, edges:[[a,b]], groups?} | 文件路径,
        target_accounts?: [id,...], consent: bool}
输出:  {status, verdicts, coord_clusters, sync_groups, summary(四层命中),
        degradation, blindspots, sufficiency, meta}
"""
import os
import sys
from pathlib import Path
from typing import Dict, Optional

_FD_REL = os.path.join("extensions", "fake_detect")
_ROOT = Path(__file__).resolve().parent.parent


def _fd_env() -> str:
    return "INFOSEEK_ENABLE_FAKE_DETECT"


def _ensure_fd_path() -> bool:
    """注入 extensions/fake_detect 到 sys.path；重依赖存在性检查。"""
    p = str(_ROOT / _FD_REL)
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        return True
    except Exception:
        return False


def _is_enabled() -> Dict:
    """综合启用判定：env 闸 ∩ 注册表（FakeDetect is_effective_enabled）。
    返回 (ok, reason)。"""
    if not os.environ.get(_fd_env()):
        return False, "account_forensics 未启用：需设置 INFOSEEK_ENABLE_FAKE_DETECT=1（合规 opt-in）"
    try:
        sys.path.insert(0, str(_ROOT / "scripts"))
        sys.path.insert(0, str(_ROOT))
        from core.capability_registry import is_effective_enabled
        if is_effective_enabled("FakeDetect"):
            return True, ""
        return False, "FakeDetect 未授权：需 grant_consent('FakeDetect')（涉个人行为画像 consent 闸）"
    except Exception:
        return False, "注册表查询失败（FakeDetect 授权状态不可判定）"


def tool_account_forensics(args: Dict) -> Dict:
    """account_forensics 工具实现：dataset → 深度取证 Report。"""
    args = args or {}
    dataset = args.get("dataset")
    consent = bool(args.get("consent", False))
    target = args.get("target_accounts") or []

    if dataset is None:
        return {"status": "failed", "reason": "dataset 不能为空（显式投喂账号数据）",
                "degradation": "invalid_input"}

    # 合规闸：显式报错（不静默返回空），Agent 可感知缺失项
    # 顺序：env 闸 → 用户授权（args.consent）→ 注册表授权（grant_consent）
    if not os.environ.get(_fd_env()):
        return {"status": "blocked",
                "reason": "account_forensics 未启用：需设置 INFOSEEK_ENABLE_FAKE_DETECT=1（合规 opt-in）",
                "degradation": "disabled"}
    if not consent:
        return {"status": "blocked",
                "reason": "账号取证涉个人行为画像：需 consent=true 显式授权",
                "degradation": "no_consent"}
    ok, reason = _is_enabled()
    if not ok:
        return {"status": "blocked", "reason": reason, "degradation": "no_consent"}
    if not _ensure_fd_path():
        return {"status": "blocked",
                "reason": "FakeDetect 重依赖缺失：请按 extensions/fake_detect/requirements.txt 安装"
                          "（numpy/pandas/scipy/networkx/scikit-learn）",
                "degradation": "deps_missing"}

    try:
        import pandas as pd
        from data_adapter import from_raw, load_dataset
        from fake_detect_engine import detect

        if isinstance(dataset, str):
            ds = load_dataset(source=dataset)
        elif isinstance(dataset, dict) and dataset.get("meta") is not None:
            ds = from_raw(meta_df=pd.DataFrame(dataset["meta"]),
                          likes=dataset.get("likes"), growth=dataset.get("growth"),
                          edges=dataset.get("edges"), groups=dataset.get("groups"))
        elif isinstance(dataset, dict) and dataset.get("source"):
            ds = load_dataset(source=dataset["source"])
        else:
            return {"status": "failed",
                    "reason": "dataset 格式不支持：需 {meta:[...], likes?, growth?, edges?} 或文件路径",
                    "degradation": "invalid_input"}
    except Exception as e:
        return {"status": "failed", "reason": f"数据接入失败: {e}", "degradation": "invalid_input"}

    rep = detect(ds)
    # 目标账号过滤（可选）
    if target:
        verdicts = {k: v for k, v in rep.get("verdicts", {}).items() if k in set(map(str, target))}
    else:
        verdicts = rep.get("verdicts", {})

    return {
        "status": rep.get("status", "failed"),
        "verdicts": verdicts,
        "verdict_count": len(verdicts),
        "coord_clusters": rep.get("coord_clusters", []),
        "sync_groups": rep.get("sync_groups", []),
        "summary": rep.get("summary", {}),
        "sufficiency": rep.get("sufficiency"),
        "degradation": rep.get("degradation", "none"),
        "degradation_detail": rep.get("degradation_detail", []),
        "blindspots": rep.get("blindspots", []),
        "meta": rep.get("meta", {}),
        "quality": rep.get("quality", ""),
    }