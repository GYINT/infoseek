#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/identity_confidence_fusion.py — 身份归因三因子置信度融合（v1.6.0 · P1/T4）

在 infoseek 已有发现层（Maigret/Sherlock）与验证层（AccountTrustScorer）基础之上，
将 A+B+C 三因子做联合置信度计算（此前三因子独立字段简单透传，未融合）：

  A 多平台交叉命中  cross_platform_matches  发现层产出（同名账号在多个平台的命中数）
  B 账号信任分      trust_score            AccountTrustScorer 四维人因评分（0-100）
  C 站点权威        site_rank / tier       maigret rank 数值 + core/trust_sources 4 级白名单

融合方法（weighted_sum）：
  confidence_final = Σ w_i · norm_i ，缺失因子动态重归一化剩余权重（保证 Σw=1）

向后兼容承诺（不破坏原有能力）：
  - 新增输出字段（confidence_final / fusion / verdict_final / confidence_label*）
  - 绝不覆盖原 confidence / trust_score / verdict / verdict_cn / trust_confidence
  - 任意因子缺失或模块异常 → 降级（fusion_degradation 标注），不抛异常中断链路

可配置：
  INFOSEEK_FUSION_ENABLED   0 关闭融合（输出无融合字段）；默认 1
  INFOSEEK_FUSION_WEIGHTS   逗号分隔三权重，如 '0.35,0.40,0.25'；非法/缺省回退默认

用法：
    from identity_confidence_fusion import fuse_anchor, fuse_confidence
    res = fuse_anchor({...发现账号字段...})          # 单账号：原字段透传+新增融合字段
    out = fuse_confidence(cross=3, trust_score=72,  # 三因子原始值
                          site_rank=180, url="https://github.com/alice")
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("infoseek.identity_fusion")

# ── 默认权重（A 多平台交叉 : B 信任分 : C 站点权威）──
DEFAULT_WEIGHTS: Dict[str, float] = {"a": 0.35, "b": 0.40, "c": 0.25}

# ── 交叉命中归一化分段（n → 归一值；缺失/0 → 中性 0.4，不惩罚单体存在）──
CROSS_STEPS: List[Tuple[int, float]] = [
    (0, 0.40), (1, 0.50), (2, 0.70), (3, 0.85), (4, 0.95), (5, 1.00),
]
CROSS_MAX = 5.0

# ── 站点权威 tier → 归一值（core/trust_sources 4 级白名单语义）──
TIER_TO_NORM = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.25}

# ── rank 数值（maigret Alexa 类排名，越小越权威）→ 归一值 分段对数衰减 ──
RANK_STEPS: List[Tuple[int, float]] = [
    (10, 1.0), (100, 0.75), (1000, 0.5), (10000, 0.25), (float("inf"), 0.1),
]
NORM_NEUTRAL = 0.5     # 因子缺失时的中性分
A_NEUTRAL = 0.4

# ── 融合置信度标签 ──
LABEL_STEPS: List[Tuple[float, str, str]] = [
    (0.75, "high", "高置信"),
    (0.50, "medium", "中等置信"),
    (0.25, "low", "低置信"),
    (0.0, "critical", "极低置信"),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _load_weights() -> Dict[str, float]:
    """读取融合权重（env INFOSEEK_FUSION_WEIGHTS='a,b,c'），非法回退默认。"""
    raw = os.environ.get("INFOSEEK_FUSION_WEIGHTS", "").strip()
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    parts = [p.strip() for p in raw.split(",")]
    try:
        vals = [float(p) for p in parts if p]
    except ValueError:
        log.warning("[融合] INFOSEEK_FUSION_WEIGHTS 非法，使用默认权重")
        return dict(DEFAULT_WEIGHTS)
    if len(vals) != 3 or any(v < 0 or v > 1 for v in vals) or sum(vals) <= 0:
        log.warning("[融合] INFOSEEK_FUSION_WEIGHTS 值域异常，使用默认权重")
        return dict(DEFAULT_WEIGHTS)
    return {"a": vals[0], "b": vals[1], "c": vals[2]}


def fusion_enabled() -> bool:
    """融合开关（env INFOSEEK_FUSION_ENABLED，缺省 1=开）。"""
    v = os.environ.get("INFOSEEK_FUSION_ENABLED", "1").strip().lower()
    return v not in ("0", "false", "off", "no")


# ═══════════════════════════════════════════════════════════════
# 单因子归一化
# ═══════════════════════════════════════════════════════════════

def norm_cross(cross: Optional[int]) -> Tuple[float, str]:
    """A 因子：多平台交叉命中数 → 0-1。缺失/0 → 0.4 中性（不惩罚单体存在）。"""
    if cross is None:
        return A_NEUTRAL, "missing"
    try:
        n = max(0, int(cross))
    except (TypeError, ValueError):
        return A_NEUTRAL, "missing"
    if n >= CROSS_MAX:
        return 1.0, "ok"
    for thr, val in CROSS_STEPS:
        if n <= thr:
            return val, "ok"
    return _clamp(n / CROSS_MAX), "ok"


def _match_tier(url: str) -> Optional[int]:
    """命中 core/trust_sources 4 级白名单 → 返回 tier（1-4）；未命中 → None。"""
    if not url:
        return None
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.trust_sources import build_pattern_index
        idx = build_pattern_index("general")
    except Exception as e:  # trust_sources 不可用时回退 rank 归一化
        log.debug(f"[融合] trust_sources 不可用: {e}")
        return None
    url_l = url.lower()
    for pat, (tier, _w) in idx.items():
        if pat and pat in url_l:
            return tier
    return None


def norm_site(url: str, site_rank: Optional[int]) -> Tuple[float, str]:
    """C 因子：站点权威 → 0-1。白名单 tier 优先；否则 rank 数值分段；双缺失 → 中性。"""
    tier = _match_tier(url)
    if tier is not None:
        return TIER_TO_NORM.get(tier, NORM_NEUTRAL), f"tier{tier}"
    if site_rank is None:
        return NORM_NEUTRAL, "missing"
    try:
        r = max(0, int(site_rank))
    except (TypeError, ValueError):
        return NORM_NEUTRAL, "missing"
    if r == 0:  # 0 = 无排名数据
        return NORM_NEUTRAL, "missing"
    for thr, val in RANK_STEPS:
        if r <= thr:
            return val, "rank"
    return 0.1, "rank"


def norm_trust(trust_score: Optional[float]) -> Tuple[float, str]:
    """B 因子：trust_score（0-100）→ 0-1；缺失 → None（触发动态重归一化）。"""
    if trust_score is None:
        return None, "missing"  # type: ignore[return-value]
    try:
        return _clamp(float(trust_score) / 100.0), "ok"
    except (TypeError, ValueError):
        return None, "missing"  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════
# 融合计算
# ═══════════════════════════════════════════════════════════════

def fuse_confidence(cross: Optional[int] = None,
                    trust_score: Optional[float] = None,
                    site_rank: Optional[int] = None,
                    url: str = "",
                    weights: Optional[Dict[str, float]] = None) -> Dict:
    """A+B+C 三因子联合置信度计算。

    缺失因子动态重归一化剩余权重（fusion_degradation 标注缺失因子），
    全部缺失 → 中性 0.5 + degradation=all-missing（不抛异常）。
    """
    w = weights or _load_weights()
    factors: Dict[str, Dict] = {}
    a, a_st = norm_cross(cross)
    factors["a_cross"] = {"raw": cross, "norm": round(a, 4), "status": a_st}
    b, b_st = norm_trust(trust_score)
    factors["b_trust"] = {"raw": trust_score,
                          "norm": (None if b is None else round(b, 4)),
                          "status": b_st}
    c, c_st = norm_site(url, site_rank)
    factors["c_site"] = {"raw": {"url": url, "site_rank": site_rank},
                         "norm": round(c, 4), "status": c_st}

    present = {"a": a}
    if b is not None:
        present["b"] = b
    else:
        log.debug("[融合] B 因子缺失（验证层未启用），动态重归一化")
    present["c"] = c

    w_sum = sum(w[k] for k in present)
    conf = sum(w[k] * v for k, v in present.items()) / max(w_sum, 1e-9)
    conf = round(_clamp(conf), 4)

    label_en, label_cn = _label(conf)
    degraded = [f for f, v in factors.items() if v["status"] == "missing"]
    return {
        "confidence_final": conf,
        "confidence_label": label_en,
        "confidence_label_cn": label_cn,
        "fusion": {
            "factors": factors,
            "weights": {k: round(w[k], 4) for k in w},
            "method": "weighted_sum",
        },
        "fusion_degradation": degraded if degraded else None,
    }


def _label(conf: float) -> Tuple[str, str]:
    for thr, en, cn in LABEL_STEPS:
        if conf >= thr:
            return en, cn
    return "critical", "极低置信"


def fuse_anchor(acc: Dict, username: str = "") -> Dict:
    """单账号融合入口：原字段透传 + 附加融合字段（不覆盖任何原字段）。

    输入：发现层/验证层账号条目（可含 cross_platform_matches / trust_score /
          site_rank / url / verdict 等）。
    输出：dict 副本，新增 confidence_final / confidence_label / confidence_label_cn /
          fusion / fusion_degradation / verdict_final。
    开关关闭或异常 → 原样返回（降级，不中断）。
    """
    if not fusion_enabled():
        return dict(acc)
    try:
        res = fuse_confidence(
            cross=acc.get("cross_platform_matches"),
            trust_score=acc.get("trust_score"),
            site_rank=acc.get("site_rank"),
            url=acc.get("url") or "",
        )
        out = dict(acc)
        out["confidence_final"] = res["confidence_final"]
        out["confidence_label"] = res["confidence_label"]
        out["confidence_label_cn"] = res["confidence_label_cn"]
        out["fusion"] = res["fusion"]
        out["fusion_degradation"] = res["fusion_degradation"]
        # verdict_final：融合置信度 × 原人因判定（unknown 保持 unknown 语义）
        v = acc.get("verdict")
        if v == "unknown":
            out["verdict_final"] = "unknown"
        else:
            map_v = {"real": "real", "likely_real": "likely_real",
                     "suspicious": "suspicious", "bot": "bot"}
            vf = map_v.get(v, res["confidence_label"])
            # B 因子强否决：信任分 < 40（suspicious 以下）且融合分虚高 → 降档提示
            ts = acc.get("trust_score")
            if ts is not None and float(ts) < 40 and vf not in ("unknown", "bot"):
                vf = "needs_review"
            out["verdict_final"] = vf
        return out
    except Exception as e:
        log.warning(f"[融合] fuse_anchor 异常，返回原条目: {e}")
        return dict(acc)


# ═══════════════════════════════════════════════════════════════
# CLI 自检
# ═══════════════════════════════════════════════════════════════

def _demo() -> int:
    cases = [
        ("真人高权威", {"cross": 4, "trust_score": 82,
                      "site_rank": 18, "url": "https://github.com/alice"}),
        ("水军低权威", {"cross": 0, "trust_score": 18,
                      "site_rank": 120000, "url": "https://weibo.com/bot1"}),
        ("B 缺失（验证层未启用）", {"cross": 2,
                                 "site_rank": None, "url": ""}),
        ("全部缺失", {}),
    ]
    for name, kw in cases:
        r = fuse_confidence(**kw)
        factors = {k: v.get("norm") for k, v in r["fusion"]["factors"].items()}
        print(f"[{name}] final={r['confidence_final']} label={r['confidence_label_cn']} "
              f"factors={factors} deg={r['fusion_degradation']}")
    return 0


if __name__ == "__main__":
    sys.exit(_demo())