#!/usr/bin/env python3
"""perf_baseline_v101.py — 10k 源性能基准（B4 · v1.0.1）

测量：批量评分 / 冲突检测 / research 全流程 在 10k 源规模下的耗时与内存。
输出：dist/perf_baseline_v101.json（P50/P95 建议后续多轮采样）。

用法：
  python scripts/perf_baseline_v101.py          # 10k 全量
  python scripts/perf_baseline_v101.py --scale 3000   # 指定规模
"""
import argparse
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'core'))

REAL_ENTITIES = ['OpenAI', '腾讯', '阿里', '英伟达', '宁德时代', '华为', '百度',
                 '小米', '字节跳动', 'Meta', '苹果', '三星', '特斯拉', '比亚迪',
                 '大疆', '京东', '美团', '拼多多', '蔚来', '理想']


def make_sources(n: int, seed: int = 42) -> list:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ent = REAL_ENTITIES[i % len(REAL_ENTITIES)]
        verb = rng.choice(['开源', '闭源', '合作', '投资', '发布财报', '推出新品'])
        out.append({
            'title': f'{ent} {verb} 计划 2026 年 Q{i % 4 + 1}',
            'snippet': f'{ent} 宣布 {verb}，涉及 {rng.randint(1, 9)} 亿元规模，2026 年内落地',
            'url': f'https://bulk{i % 50}.com/{i}',
        })
    return out


def bench(fn, *args, **kw):
    tracemalloc.start()
    t0 = time.time()
    result = fn(*args, **kw)
    dt = time.time() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dt, peak / 1024 / 1024, result


def pct(vals: list, p: float) -> float:
    """百分位（p: 0-100）"""
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> int:
    ap = argparse.ArgumentParser(description='infoseek 10k 源性能基准（B4 · 多轮采样 P50/P95）')
    ap.add_argument('--scale', type=int, default=10000, help='源数量（默认 10000）')
    ap.add_argument('--rounds', type=int, default=1, help='采样轮数（默认 1；≥3 输出 P50/P95）')
    args = ap.parse_args()
    n = args.scale
    rounds = max(1, args.rounds)

    from infoseek_core_v2 import score_source, research
    from conflict_v3 import detect_conflicts_v3

    print(f'=== infoseek 性能基准（{n} 源 × {rounds} 轮 · v1.0.1 B4）===')
    sources = make_sources(n)

    score_ts, conflict_ts, research_ts = [], [], []
    n_conf_last = 0
    for r in range(rounds):
        dt, peak, _ = bench(lambda: [score_source(s, '行业 2026 竞争格局') for s in sources])
        score_ts.append(round(dt, 1))
        dt2, peak2, cres = bench(detect_conflicts_v3, sources, subject='行业 2026 竞争格局')
        conflict_ts.append(round(dt2, 1))
        n_conf_last = len(cres.get('conflicts', []))
        dt3, peak3, r3 = bench(research, '行业 2026 竞争格局', sources=sources[:2000], lite=True)
        research_ts.append(round(dt3, 1))
        print(f'  轮 {r+1}: 评分 {score_ts[-1]}s | 冲突 {conflict_ts[-1]}s | research {research_ts[-1]}s'
              f'（峰值 {peak/1024/1024:.0f} MB）')

    metrics = {
        'score_sec': score_ts,
        'conflict_sec': conflict_ts,
        'research_2k_sec': research_ts,
    }
    if len(score_ts) >= 3:
        metrics['score_p50'] = round(pct(score_ts, 50), 1)
        metrics['score_p95'] = round(pct(score_ts, 95), 1)
        metrics['conflict_p50'] = round(pct(conflict_ts, 50), 1)
        metrics['conflict_p95'] = round(pct(conflict_ts, 95), 1)
        metrics['research_p50'] = round(pct(research_ts, 50), 1)
        metrics['research_p95'] = round(pct(research_ts, 95), 1)
        print(f'\nP50/P95: 评分 {metrics["score_p50"]}/{metrics["score_p95"]}s · '
              f'冲突 {metrics["conflict_p50"]}/{metrics["conflict_p95"]}s · '
              f'research {metrics["research_p50"]}/{metrics["research_p95"]}s')

    baseline = {
        'version': '1.0.1',
        'generated_at': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        'scale': n,
        'rounds': rounds,
        'metrics': metrics,
        'conflicts_detected': n_conf_last,
        'note': 'P50/P95 需 rounds≥3；单轮时 metrics 为逐轮原始值',
    }
    out = ROOT / 'dist' / 'perf_baseline_v101.json'
    out.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n已写入 {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
