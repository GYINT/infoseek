#!/usr/bin/env python3
"""Infoseek v1.0.1 深度测试：边界 + 压力 + 模拟案例（P0-1 修复后的全面验证）

维度：
- B: 边界（空输入 / 超长 / 特殊字符 / None / mock / 幂等）
- S: 压力（1000-5000 源批量 / 并发 / 重复调用 / 内存）
- C: 模拟案例（行业 / 竞品 / 财报 / 技术 / 舆情 5 类端到端）

用法:
    python tests/test_deep_v101.py          # 全量
    python tests/test_deep_v101.py boundary # 仅边界
    python tests/test_deep_v101.py stress   # 仅压力
    python tests/test_deep_v101.py case     # 仅模拟案例
"""
import sys, os, time, json, random, string
from pathlib import Path

INFOSEEK = Path(__file__).parent.parent
sys.path.insert(0, str(INFOSEEK / 'core'))
sys.path.insert(0, str(INFOSEEK / 'scripts'))

passed, failed = [], []

def check(name, cond, extra=''):
    if cond:
        passed.append(name); print(f"  [PASS] {name} {extra}")
    else:
        failed.append(name); print(f"  [FAIL] {name} {extra}")

def run_section(tag):
    """按 tag 过滤运行"""
    if len(sys.argv) > 1 and tag not in sys.argv[1:] and sys.argv[1] not in ('all',):
        return False
    return True

# ═══════════════════════════════════════════════════════════════
# B: 边界测试
# ═══════════════════════════════════════════════════════════════
def test_boundary():
    print("\n═══ B 边界测试 ═══")
    from infoseek_core_v2 import score_source, research, render_report
    from infoseek_pipeline import _filter_relevant, search_web
    from infoseek_helper import normalize_url
    from domain_router import detect_domain

    # B1 空 sources / 空主题不崩
    try:
        r = research('', sources=[], lite=True)
        check('B1 空主题+空源不崩', isinstance(r, dict) and 'report' in r)
    except Exception as e:
        check('B1 空主题+空源不崩', False, str(e)[:80])

    # B2 score_source 空源不崩
    try:
        sc = score_source({}, '主题')
        check('B2 空 source 评分不崩', isinstance(sc, dict) and sc['final_score'] == 0)
    except Exception as e:
        check('B2 空 source 评分不崩', False, str(e)[:80])

    # B3 超长主题（10KB）
    long_subject = '测试' * 5000
    try:
        sc = score_source({'title': 'AI', 'snippet': '大模型 技术'}, long_subject)
        check('B3 超长主题不崩', isinstance(sc, dict))
    except Exception as e:
        check('B3 超长主题不崩', False, str(e)[:80])

    # B4 特殊字符（emoji / 引号 / 控制字符）
    weird = {'title': '标题"引号"\U0001F600💥\t\n', 'snippet': '内容<>&\'', 'url': 'https://x.com/1?utm_source=a#frag'}
    try:
        sc = score_source(weird, '普通主题')
        n = normalize_url(weird['url'])
        check('B4 特殊字符不崩', isinstance(sc, dict) and 'utm_source' not in n)
    except Exception as e:
        check('B4 特殊字符不崩', False, str(e)[:80])

    # B5 normalize_url 幂等
    try:
        u = 'https://www.Example.com/path/?b=2&a=1&utm_source=x#frag'
        n1 = normalize_url(u); n2 = normalize_url(n1)
        check('B5 normalize 幂等', n1 == n2, f"n1={n1[:40]}")
    except Exception as e:
        check('B5 normalize 幂等', False, str(e)[:80])

    # B6 _filter_relevant 空列表
    try:
        r = _filter_relevant([], '查询')
        check('B6 filter 空列表', r == [])
    except Exception as e:
        check('B6 filter 空列表', False, str(e)[:80])

    # B7 detect_domain 空主题
    try:
        d = detect_domain('')
        check('B7 detect_domain 空主题', d.get('is_default') is True or d.get('domain') is None)
    except Exception as e:
        check('B7 detect_domain 空主题', False, str(e)[:80])

    # B8 重复调用幂等（两次 research 同输入同输出核心字段）
    try:
        SRC = [{'title': 'OpenAI 开源 GPT-5', 'snippet': 'OpenAI 宣布 GPT-5 开源', 'url': 'https://a.com/1'},
               {'title': 'OpenAI 闭源', 'snippet': '官方确认闭源', 'url': 'https://b.com/2'}]
        r1 = research('OpenAI', sources=SRC, lite=True)
        r2 = research('OpenAI', sources=SRC, lite=True)
        same = len(r1['conflicts']) == len(r2['conflicts']) and r1['report'] == r2['report']
        check('B8 重复调用确定性', same, f"conflicts={len(r1['conflicts'])}")
    except Exception as e:
        check('B8 重复调用确定性', False, str(e)[:80])

    # B9 None url / 缺字段源
    try:
        res = research('T', sources=[{'title': None, 'snippet': None, 'url': None}], lite=True)
        check('B9 全 None 源不崩', isinstance(res, dict))
    except Exception as e:
        check('B9 全 None 源不崩', False, str(e)[:80])

    # B10 矛盾检测空源
    try:
        from conflict_v3 import detect_conflicts_v3
        c = detect_conflicts_v3([], subject='')
        check('B10 空源矛盾检测', isinstance(c, dict) and c.get('conflicts') == [])
    except Exception as e:
        check('B10 空源矛盾检测', False, str(e)[:80])

# ═══════════════════════════════════════════════════════════════
# S: 压力测试
# ═══════════════════════════════════════════════════════════════
def test_stress():
    print("\n═══ S 压力测试 ═══")
    from infoseek_core_v2 import score_source, research, render_report

    # S1 1000 源批量评分耗时（用 NER 词典内真实实体名，保证冲突检测可验证）
    rng = random.Random(42)
    ENTITIES = ['OpenAI', '腾讯', '阿里巴巴', '英伟达', '宁德时代', '百度',
                '字节跳动', 'Meta', '华为', '小米']
    ACTIONS = ['开源', '闭源', '合作', '投资']
    big = []
    for i in range(1000):
        ent = ENTITIES[i % len(ENTITIES)]
        big.append({'title': f'{ent} 发布 {rng.choice(ACTIONS)}',
                    'snippet': f'{ent} 宣布 {rng.choice(ACTIONS)} 计划 2026',
                    'url': f'https://bulk{i%50}.com/{i}'})
    t0 = time.time()
    for s in big:
        score_source(s, '行业 调研 2026')
    dt = time.time() - t0
    check('S1 1000 源评分 <15s', dt < 15, f"{dt:.1f}s")

    # S2 3000 源评分耗时（更大批量）
    big3k = big * 3
    t0 = time.time()
    for s in big3k:
        score_source(s, '行业 调研 2026')
    dt = time.time() - t0
    check('S2 3000 源评分 <45s', dt < 45, f"{dt:.1f}s")

    # S3 1000 源 conflict 检测耗时（真实实体名 → 应检出矛盾）
    from conflict_v3 import detect_conflicts_v3
    t0 = time.time()
    c = detect_conflicts_v3(big, subject='行业 2026')
    dt = time.time() - t0
    conflicts = len(c.get('conflicts', []))
    check('S3 1000 源冲突检测 <20s 且检出矛盾', dt < 20 and conflicts > 0,
          f"{dt:.1f}s conflicts={conflicts}")

    # S4 1000 源 research lite 耗时
    t0 = time.time()
    r = research('行业 调研 2026', sources=big, lite=True)
    dt = time.time() - t0
    check('S4 1000 源 research lite <30s', dt < 30, f"{dt:.1f}s report={len(r.get('report',''))}")

    # S5 50 并发评分（线程）
    import concurrent.futures
    def score_one(i):
        return score_source(big[i], '行业 调研 2026')['final_score']
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        scores = list(ex.map(score_one, range(200)))
    dt = time.time() - t0
    check('S5 200 源 8 并发评分 <10s', dt < 10, f"{dt:.1f}s all_valid={all(s >= 0 for s in scores)}")

    # S6 5000 次 URL 标准化
    from infoseek_helper import normalize_url
    t0 = time.time()
    for i in range(5000):
        normalize_url(f'https://www.Example{i%50}.com/path/?b=2&a=1&utm_source=x#frag')
    dt = time.time() - t0
    check('S6 5000 次 URL 标准化 <5s', dt < 5, f"{dt:.1f}s")

    # S7 长文本摘要（100KB）
    from infoseek_core_v2 import render_report
    huge_src = [{'title': f'长文{i}', 'snippet': '内容' * 10000, 'url': f'https://h.com/{i}'} for i in range(5)]
    t0 = time.time()
    md = render_report('长文测试', huge_src)
    dt = time.time() - t0
    check('S7 100KB 源渲染 <10s', dt < 10, f"{dt:.1f}s len={len(md)}")

# ═══════════════════════════════════════════════════════════════
# C: 模拟案例（端到端，用 mock 源模拟真实调研）
# ═══════════════════════════════════════════════════════════════
def test_cases():
    print("\n═══ C 模拟案例 ═══")
    from infoseek_core_v2 import research, score_source

    cases = [
        # C1 行业调研
        ('C1 行业调研-新能源汽车 2026', '新能源汽车 2026 市场 规模 增速', [
            {'title': '新能源汽车 2026 年市场规模突破 3000 亿', 'snippet': '2026 年新能源汽车销量同比增长 40%，渗透率达 60%', 'url': 'https://auto.com/1'},
            {'title': '新能源汽车行业研究报告', 'snippet': '市场规模 3000 亿，CAGR 25%，竞争格局分散', 'url': 'https://research.com/2'},
            {'title': '2026 新能源汽车政策解读', 'snippet': '购置税减免政策延续至 2027 年', 'url': 'https://gov.com/3'},
        ]),
        # C2 竞品分析
        ('C2 竞品-OpenAI vs Anthropic', 'OpenAI Anthropic 竞品 对比 差异化', [
            {'title': 'OpenAI 发布 GPT-5 强化学习路线', 'snippet': 'GPT-5 采用 RL 后训练，推理能力大幅提升', 'url': 'https://openai.com/1'},
            {'title': 'Anthropic Claude 采用宪法 AI 对齐', 'snippet': 'Claude 强调安全对齐与可解释性', 'url': 'https://anthropic.com/2'},
            {'title': '两大模型对比：GPT-5 vs Claude 4', 'snippet': '各有优势，OpenAI 通用性强，Anthropic 安全性高', 'url': 'https://compare.com/3'},
        ]),
        # C3 财报扫描
        ('C3 财报-宁德时代季报', '宁德时代 季报 营收 利润 财报', [
            {'title': '宁德时代 Q3 营收增长 20%', 'snippet': 'Q3 营收 1000 亿，净利润 150 亿，毛利率提升', 'url': 'https://finance.com/1'},
            {'title': '宁德时代 Q3 营收下滑', 'snippet': '动力电池竞争加剧，营收同比下滑 5%', 'url': 'https://finance.com/2'},
        ]),
        # C4 技术追踪
        ('C4 技术-大模型微调技术', '大语言模型 微调 LoRA 技术', [
            {'title': 'LoRA 微调技术详解', 'snippet': 'LoRA 用低秩矩阵近似，参数量仅为原模型 0.1%', 'url': 'https://tech.com/1'},
            {'title': '全参数微调 vs LoRA 对比', 'snippet': '全参数微调效果更好但成本高，LoRA 更高效', 'url': 'https://tech.com/2'},
            {'title': '微调技术最新进展', 'snippet': 'QLoRA 进一步降低显存占用至 4bit', 'url': 'https://tech.com/3'},
        ]),
        # C5 舆情监控
        ('C5 舆情-GPT-5 开源争议', 'GPT-5 开源 争议 闭源', [
            {'title': 'OpenAI 宣布 GPT-5 完全开源', 'snippet': '社区强烈反响，认为开源利于生态', 'url': 'https://news.com/1'},
            {'title': 'OpenAI 官方辟谣：GPT-5 不开源', 'snippet': '官方声明从未承诺开源，仅开放 API', 'url': 'https://news.com/2'},
            {'title': '开源与闭源之争持续', 'snippet': '开发者社区意见分裂，争论安全性', 'url': 'https://news.com/3'},
        ]),
    ]

    for label, subject, srcs in cases:
        t0 = time.time()
        try:
            res = research(subject, sources=srcs, lite=True)
            dt = time.time() - t0
            report = res.get('report', '')
            # 断言：报告非空壳（长度 >300 且包含来源标题之一）
            any_title = any(s['title'][:8] in report for s in srcs)
            has_content = len(report) > 300 and any_title
            # 真矛盾案例（C3 财报增/降、C5 开源/闭源）应检出语义分>0 的冲突；
            # 其余案例允许候选冲突（severity=none）但不应有真矛盾
            if 'C3' in label or 'C5' in label:
                real_conflict = any(c.get('semantic_score', 0) > 0 for c in res.get('conflicts', []))
                conflict_ok = real_conflict
            else:
                conflict_ok = all(c.get('severity') in (None, 'none') for c in res.get('conflicts', []))
            check(label, has_content and conflict_ok,
                  f"report={len(report)} conflicts={len(res.get('conflicts', []))} {dt:.1f}s")
        except Exception as e:
            check(label, False, str(e)[:100])

# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode in ('all', 'boundary'):
        test_boundary()
    if mode in ('all', 'stress'):
        test_stress()
    if mode in ('all', 'case'):
        test_cases()

    print(f"\n=== 深度测试结果: {len(passed)} PASS / {len(failed)} FAIL ===")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL PASS")
