#!/usr/bin/env python3
"""
scripts/infoseek_core_v2.py — Infoseek v2 统一 API 入口（v2.0.0 新增）

设计目标：
1. 把 core/ 各模块统一封装为 v2 API
2. 提供 research() 主入口：端到端调研流程
3. 旧版 anchor_adapter.py 的 v1 API 通过 deprecation shim 兼容

v2 API 列表：
- research(subject, sources=None) → 端到端调研
- score_source(source, subject) → v2 评分
- extract_entities(text) → 实体抽取
- detect_conflicts(sources) → 冲突检测 v2（实体感知）
- render_report(subject, sources) → 报告渲染（领域模板）

兼容策略：
- v1 anchor_adapter.calculate_score() → score_source()
- v1 conflict_detection.detect_conflicts() → detect_conflicts()
- v1 exporter.to_markdown() → render_report(format='md')
"""

import sys
import os
import warnings
from pathlib import Path
from typing import List, Dict, Optional, Any

# v2 core 路径
INFOSEEK_ROOT = Path(os.environ.get('INFOSEEK_ROOT', str(Path(__file__).parent.parent)))
CORE_DIR = INFOSEEK_ROOT / 'core'
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(INFOSEEK_ROOT))

from core.ner import extract_entities
from core.entities import get_entities_by_type, entity_count
from core.trust_sources import compute_trust_bonus, get_tier_level
from core.llm_router import llm_call, estimate_cost, list_available_providers


# ═══════════════════════════════════════════════════════════════
# v2 核心 API
# ═══════════════════════════════════════════════════════════════

def _has_llm_endpoint() -> bool:
    """v2.4.3 PATCH (P1-B): 检测当前环境是否配置 LLM endpoint

    沙箱/未配置 LLM 时返回 False，避免 entity_enricher 步骤空跑 ~4s 等待。
    返回 True 时按原 v2.1.0 行为执行 LLM 抽取 + pending 队列。
    """
    try:
        sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
        from llm_router import LLMRouter
        return bool(LLMRouter().has_available_endpoint())
    except Exception:
        return False


def score_source(source: Dict, subject: str, with_domain: bool = True) -> Dict:
    """v2 评分（封装 anchor_adapter v1 + 统一信任源加权）

    参数:
        source: {url, platform, score, title, snippet, ...}
        subject: 调研主题
        with_domain: 是否自动应用领域加权

    返回:
        {
            'final_score': 0-100,
            'tier': 1-4,
            'trust_bonus': 0-30,
            'domain_bonus': 0-20,
            'classification': '🟢核心' / '🟡潜力' / '❌噪声',
            'version': '2.3.0',
        }
    """
    # 0) 如果 source 已含 score 字段（v1 输入），直接使用为 base_score
    base_score = source.get('score', 0)

    # 1) 调用 v1 anchor_adapter.calculate_score()（如果有完整字段）
    if all(k in source for k in ('interaction', 'topic_match', 'credibility')):
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
            from anchor_adapter import calculate_score
            v1_result = calculate_score(
                source, subject,
                with_llm_readability=('llm_readability' in source),
                with_semantic=bool(source.get('snippet')),
                semantic_text=source.get('snippet', ''),
                with_domain=with_domain,
            )
            base_score = v1_result.get('after_whitelist', v1_result.get('raw_score', base_score))
        except Exception:
            pass

    # 2) 信任源加权（v2 新增）
    # 先尝试检测 domain（如未指定）
    domain = source.get('domain')
    if not domain:
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
            from domain_router import detect_domain
            routing = detect_domain(subject)
            domain = routing.get('domain') or 'general'
        except Exception:
            domain = 'general'

    trust_bonus = compute_trust_bonus(source.get('url', ''), domain, source.get('platform', ''))
    tier = get_tier_level(source.get('url', ''), domain)

    # 3) 合并
    final = min(base_score + trust_bonus, 100)

    # 4) 分类
    if final >= 70:
        classification = '🟢核心'
    elif final >= 40:
        classification = '🟡潜力'
    else:
        classification = '❌噪声'

    return {
        'final_score': final,
        'base_score': base_score,
        'tier': tier,
        'trust_bonus': trust_bonus,
        'domain_bonus': source.get('_scoring', {}).get('domain_bonus', 0),
        'classification': classification,
        'version': '2.3.0',
    }


def score_sources_batch_async(sources: List[Dict], subject: str,
                              with_domain: bool = True) -> List[Dict]:
    """v2.5.0 MINOR 新增：批量异步评分（asyncio.gather 并行 8 维度）

    benchmark 结果（100 源 × 8 维度 × 2-4ms 模拟 IO）：
    - 串行：2700ms
    - asyncio run_in_executor per-source：569ms（4.7x）
    - asyncio gather all 800 tasks：**351ms（7.7x）** ✅
    - multiprocessing：pickle 失败（局部函数不可序列化）

    跨源一次性 gather 所有 800 个评分任务 → 启动开销最小 + 并行度最大

    注：score_source 实际很快（<1ms，模块已单例化），asyncio 启动开销占主导
    在 score_source 仍 <10ms 的场景下，本函数只对 IO 密集维度（如网络）有效。
    """
    import asyncio
    # 同步入口：在无 running loop 时用 asyncio.run；否则新建 loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的 loop，同步执行
        try:
            return asyncio.run(_gather_all(sources, subject, with_domain))
        except RuntimeError:
            # 沙箱环境可能没正常 event loop → 退化为串行
            return [score_source(s, subject, with_domain) for s in sources]
    # 有 running loop（罕见：用户在 async 上下文调同步入口）
    # 用 ThreadPoolExecutor 跑异步
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            asyncio.run, _gather_all(sources, subject, with_domain)
        )
        return future.result()


async def _gather_all(sources, subject, with_domain):
    import asyncio
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, score_source, src, subject, with_domain)
        for src in sources
    ]
    return await asyncio.gather(*tasks)


def detect_conflicts(sources: List[Dict], subject: str = '') -> Dict:
    """v2 冲突检测（实体感知）

    在 v1.8.0 conflict_detection 基础上增加：
    1. 实体感知：先抽实体，按实体分组冲突
    2. 实体相似度：跨语言实体匹配（如 OpenAI ↔ openai ↔ OPENAI）
    """
    # 1) 调用 v1 conflict_detection.detect_conflicts()
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    try:
        from conflict_detection import detect_conflicts as v1_detect
        v1_result = v1_detect(sources, subject=subject)
    except ImportError:
        return {'error': 'conflict_detection 未找到', 'conflicts': []}

    # 2) v2 增强：实体感知
    entity_conflicts = []
    for source in sources:
        text = source.get('text', '') or source.get('snippet', '')
        entities = extract_entities(text)
        if entities:
            source['_v2_entities'] = [e['entity_name'] for e in entities]

    # 3) 跨源实体覆盖度对比
    all_entities = set()
    for source in sources:
        for e in source.get('_v2_entities', []):
            all_entities.add(e)

    if all_entities:
        coverage_data = []
        for source in sources:
            src_entities = set(source.get('_v2_entities', []))
            coverage = len(src_entities & all_entities) / len(all_entities) if all_entities else 0
            coverage_data.append({
                'source': source.get('title', 'Untitled'),
                'coverage': round(coverage * 100, 1),
                'entities_mentioned': list(src_entities),
            })

        v1_result['v2_entity_coverage'] = coverage_data

    v1_result['version'] = '3.0.0'
    return v1_result


def render_report(subject: str, sources: List[Dict],
                  format: str = 'md',
                  domain: Optional[str] = None) -> str:
    """v2 报告渲染（调用 domain_orchestrator 或 exporter）

    参数:
        subject: 调研主题
        sources: 来源列表
        format: 'md' / 'json' / 'csv' / 'traced_md' / ...
        domain: 手动指定领域

    返回:
        格式化报告字符串
    """
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    try:
        from domain_orchestrator import DomainOrchestrator
        from exporter import FORMATTERS
    except ImportError:
        return f'[error] domain_orchestrator 或 exporter 未找到'

    orchestrator = DomainOrchestrator()

    # 先用 v2 scoring
    scored_sources = []
    for s in sources:
        v2_score = score_source(s, subject)
        s_copy = dict(s)
        s_copy['score'] = v2_score['final_score']
        s_copy['_v2'] = v2_score
        scored_sources.append(s_copy)

    # 用 domain_orchestrator 渲染
    result = orchestrator.render_report(subject, scored_sources, domain_override=domain)

    if format == 'md':
        return result['markdown']
    elif format in FORMATTERS:
        # 构造报告 dict 给 exporter
        report_dict = {
            'subject': subject,
            'domain': result.get('domain'),
            'summary': f'{subject} 调研（{len(scored_sources)} 来源）',
            'anchors': scored_sources,
        }
        return FORMATTERS[format](report_dict)
    else:
        return f'[error] 未知 format: {format}'


def research(subject: str,
             sources: Optional[List[Dict]] = None,
             domain: Optional[str] = None,
             with_llm: bool = False,
             output_format: str = 'md',
             lite: bool = False) -> Dict[str, Any]:
    """v2 端到端调研主入口

    完整流程：
    1. 检测领域
    2. 评分（v2 + 信任源）
    3. 冲突检测（v2 实体感知）
    4. 报告渲染
    5. 可选 LLM 增强
    6. v2.1.0 自沉淀（enricher）
    7. v2.1.1 Wikidata 验证
    8. v2.2.0 实体索引
    9. v2.3.0 实体图谱
    10. v2.3.0 冲突检测 v3 + 矛盾评分
    11. v2.3.0 实体画像
    12. v2.4.0 热度预测
    13. v2.4.0 实体轨迹

    参数:
        subject: 调研主题
        sources: 来源列表（如 None 则仅返回空报告骨架）
        domain: 手动指定领域
        with_llm: 是否调用 LLM 增强
        output_format: 输出格式
        lite: v2.4.1 PATCH — 轻量模式（DEF-E 性能优化）
              跳过耗时步骤：wikidata 验证、traced_export dot 渲染、heat 排名、trajectory
              适用：≥10 源时性能要求 <1s；纯结构化提取场景
              默认 False（保持向后兼容）

    返回:
        {
            'subject': subject,
            'domain': 'tech-research',
            'scored_sources': [...],
            'conflicts': [...],
            'report': str,
            'llm_insights': str (if with_llm),
            'version': '2.3.0',
        }
    """
    sources = sources or []

    # v2.5.3 PATCH: 在 async 上下文调用 research() 时发 deprecation warning，
    # 建议改用 async_research() 避免阻塞 event loop（不破坏既有调用）
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        import warnings
        warnings.warn(
            "research() 在 async 上下文中可能阻塞 event loop；"
            "建议改用 await async_research(...)。",
            DeprecationWarning, stacklevel=2,
        )
    except RuntimeError:
        pass  # 无运行中的 loop，正常执行

    # 1) 评分
    scored = [score_source(s, subject) for s in sources]

    # 2) 冲突检测
    conflicts = detect_conflicts(sources, subject=subject)

    # 3) 报告渲染
    report = render_report(subject, sources, format=output_format, domain=domain)

    result = {
        'subject': subject,
        'scored_sources': scored,
        'conflicts': conflicts.get('conflicts', []),
        'report': report,
        'version': '2.4.0',
    }

    # 4) LLM 增强（可选）
    if with_llm:
        llm_prompt = f"调研主题: {subject}\n请给出关键洞察和后续建议。"
        llm_result = llm_call(llm_prompt, max_tokens=200)
        result['llm_insights'] = llm_result['content']
        result['llm_provider'] = llm_result['provider']
        result['llm_cost'] = llm_result['cost_estimate']

    # 5) v2.1.0 自沉淀触发（LLM 抽取 + 入 pending 队列）
    # v2.4.3 PATCH (P1-B): 默认跳过无 LLM endpoint 场景（沙箱/生产环境均省 ~4s）
    if sources and _has_llm_endpoint():
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
            from entity_enricher import EntityEnricher

            enricher = EntityEnricher()
            all_text = ' '.join(
                s.get('text', '') or s.get('snippet', '') or s.get('title', '')
                for s in sources
            )
            candidates = enricher.extract_candidates(all_text)
            suggestions = enricher.suggest_additions(candidates)
            persist_result = enricher.persist_suggestions(suggestions, auto_confirm=False)
            result['enrichment'] = {
                'candidates_extracted': len(candidates),
                'new_suggestions': len(suggestions),
                'auto_added': persist_result['auto_added'],
                'queued_for_review': persist_result['queued'],
                'pending_entities': enricher.get_pending(),
            }
        except Exception as e:
            result['enrichment'] = {'error': str(e)}
    elif sources:
        result['enrichment'] = {'skipped': 'no_llm_endpoint'}

    # 6) v2.1.1 集成：Wikidata 验证（网络可用时）
    # v2.4.1 PATCH (DEF-E): lite 模式跳过网络调用
    if not lite:
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
            from wikidata_sync import WikidataSync
            sync = WikidataSync()
            # 仅检查 subject 在 Wikidata 是否存在（轻量验证）
            exists = sync.verify_existence(subject)
            result['wikidata_verified'] = {
                'subject': subject,
                'exists': exists,
                'available': True,
            }
        except Exception as e:
            result['wikidata_verified'] = {'available': False, 'error': str(e)}
    else:
        result['wikidata_verified'] = {'skipped': 'lite_mode'}

    # 7) v2.2.0 集成：报告实体索引（实体图谱）
    try:
        sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
        from ner import extract_entities
        all_text = ' '.join(
            s.get('text', '') or s.get('snippet', '') or s.get('title', '')
            for s in sources
        )
        raw_entities = extract_entities(all_text)
        # 聚合去重：同名实体统计命中数 + 匹配方式
        entity_map = {}
        for e in raw_entities:
            name = e['entity_name']
            if name not in entity_map:
                entity_map[name] = {
                    'entity_name': name,
                    'entity_type': e.get('entity_type', 'UNKNOWN'),
                    'hit_count': 0,
                    'match_methods': [],
                }
            entity_map[name]['hit_count'] += 1
            method = e.get('match_method', 'unknown')
            if method not in entity_map[name]['match_methods']:
                entity_map[name]['match_methods'].append(method)
        result['entity_index'] = sorted(
            entity_map.values(),
            key=lambda x: (-x['hit_count'], x['entity_name']),
        )
    except Exception as e:
        result['entity_index'] = {'error': str(e)}

    # 8) v2.3.0 集成：实体图谱（共现关系）
    # v2.4.1 PATCH (DEF-E): lite 模式跳过 traced_export dot 渲染（>10 源时极慢）
    try:
        sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
        from entity_graph import EntityGraph
        graph = EntityGraph()
        graph.build_from_sources(sources)
        result['entity_graph'] = graph.to_dict()
        if not lite:
            try:
                from traced_export import build_traced, to_dot
                traced = build_traced(sources, result['entity_graph'])
                # 节点阈值：>500 不画 dot（避免 Graphviz 渲染超时）
                if len(traced.get('nodes', [])) > 500:
                    traced['dot'] = f'# skipped: {len(traced["nodes"])} nodes exceed 500 threshold'
                else:
                    traced['dot'] = to_dot(traced)
                result['traced_export'] = traced
            except Exception as te:
                result['traced_export'] = {'error': str(te)}
        else:
            result['traced_export'] = {'skipped': 'lite_mode'}
    except Exception as e:
        result['entity_graph'] = {'error': str(e)}

    # 9) v2.3.0 集成：冲突检测 v3（跨别名归并）
    try:
        sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
        from conflict_v3 import detect_conflicts_v3
        v3_result = detect_conflicts_v3(sources, subject=subject)
        result['conflicts'] = v3_result.get('conflicts', result.get('conflicts', []))
        result['conflict_v3'] = {
            'version': v3_result.get('version'),
            'raw_claims': v3_result.get('raw_claims'),
            'total': len(v3_result.get('conflicts', [])),
            'aliases_involved': v3_result.get('aliases_involved', {}),
            'live_alerts': v3_result.get('live_alerts', []),  # v2.3.1: 增量实时告警
            'cross_session_summary': v3_result.get('cross_session_summary', {}),  # v2.4.0
        }
        # v2.4.0: 语义矛盾打分（给每对冲突附 semantic_score）
        try:
            from contradiction_scorer import score_contradiction
            enriched = []
            for c in result['conflicts']:
                sc = score_contradiction(c['claim_a'], c['claim_b'])
                c2 = dict(c)
                c2['semantic_score'] = sc['score']
                c2['severity'] = sc['severity']  # 用语义评覆盖中等/高严重度
                enriched.append(c2)
            result['conflicts'] = enriched
            result['contradiction_scoring'] = {
                'enabled': True,
                'version': '2.4.0',
                'scored': len(enriched),
                'method': 'local',   # 默认本地分（LLM 增强待后续接入）
            }
        except Exception as e:
            result['contradiction_scoring'] = {'enabled': False, 'error': str(e)}
    except Exception as e:
        result['conflict_v3'] = {'error': str(e)}

    # 10) v2.3.0 集成：实体画像（长期知识库积累）
    try:
        sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
        from entity_profile import EntityProfile
        profiler = EntityProfile()
        result['entity_profiles'] = profiler.update_profiles(
            sources,
            result.get('entity_index', []) if isinstance(result.get('entity_index'), list) else [],
            conflicts=result.get('conflicts', []),
        )
    except Exception as e:
        result['entity_profiles'] = {'error': str(e)}

    # 10.1) v2.4.0: 实体热度预测 Top 10（轻量，可选调用失败不阻断）
    # v2.4.1 PATCH (DEF-E): lite 模式跳过（全实体 ranking 146 个代价高）
    if not lite:
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
            from entity_heat import get_heat_ranking
            result['heat_ranking'] = get_heat_ranking(top_n=10)
        except Exception as e:
            result['heat_ranking'] = {'error': str(e)}
    else:
        result['heat_ranking'] = {'skipped': 'lite_mode'}

    # 10.2) v2.4.0: 实体轨迹（仅取 entity_index 命中 Top5，避免全量开销）
    # v2.4.1 PATCH (DEF-E): lite 模式跳过（5 次 trace_entity 每个 ~100ms）
    if not lite:
        try:
            sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
            from entity_trajectory import trace_entity
            ent_idx = result.get('entity_index', [])
            if isinstance(ent_idx, list) and ent_idx:
                top_names = sorted(
                    [e.get('entity_name') for e in ent_idx if e.get('entity_name')],
                    key=lambda n: -next((x.get('hit_count', 0) for x in ent_idx if x.get('entity_name') == n), 0),
                )[:5]
                result['trajectory_top5'] = [trace_entity(n, days_back=90) for n in top_names]
            else:
                result['trajectory_top5'] = []
        except Exception as e:
            result['trajectory_top5'] = {'error': str(e)}
    else:
        result['trajectory_top5'] = {'skipped': 'lite_mode'}

    # v3.0.0 GA: 标识版本 + 内部使用 streaming_research 路径
    result['version'] = '3.0.0'
    result['streaming_mode'] = False  # 同步包装（v3.0.0 GA 仍走内部 12 步骤）
    return result


async def async_research(subject: str,
                        sources: Optional[List[Dict]] = None,
                        domain: Optional[str] = None,
                        output_format: str = 'md',
                        lite: bool = False) -> Dict[str, Any]:
    """v2.5.0 MINOR 新增；v2.5.2 PATCH: 深度异步化

    v2.5.0：评分 + wikidata 异步，其余 6 步骤走同步
    v2.5.2：5 个 IO 密集步骤（score / wikidata / entity_graph / conflict_v3 / entity_profile）
              并发 asyncio.create_task + gather；纯计算步骤（render_report / 分类）放最后同步

    benchmark（沙箱 10 源 lite）：v2.5.0 ~2080ms → v2.5.2 目标 <1500ms

    用法：
        res = asyncio.run(async_research('AI', sources, lite=True))
    """
    import asyncio
    sources = sources or []

    # 任务调度：5 个 IO 密集步骤并发
    tasks = {}

    # 1+2: 异步批量评分（已 v2.5.0 实现）
    if sources:
        tasks['scored'] = asyncio.create_task(
            asyncio.to_thread(score_sources_batch_async, sources, subject)
        )
    else:
        tasks['scored'] = asyncio.create_task(asyncio.sleep(0, result=[]))

    # 6: 异步 Wikidata（已 v2.5.0 实现）
    if not lite and sources:
        async def _wikidata():
            try:
                sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
                from wikidata_sync import WikidataSync
                sync = WikidataSync()
                url_entities = []
                for s in sources[:10]:
                    txt = s.get('snippet', '') or s.get('title', '')
                    url_entities.append(txt[:20] if txt else subject)
                verify_results = await sync.verify_batch_async([subject] + url_entities)
                return {
                    'subject_verified': verify_results.get(subject, False),
                    'sources_verified': sum(1 for v in verify_results.values() if v),
                    'available': True,
                }
            except Exception as e:
                return {'available': False, 'error': str(e)}
        tasks['wikidata'] = asyncio.create_task(_wikidata())
    else:
        async def _wikidata_skip():
            return {'skipped': 'lite_mode'}
        tasks['wikidata'] = asyncio.create_task(_wikidata_skip())

    # 8: 异步 entity_graph + traced_export（v2.5.4 PATCH: 补回 traced_export）
    if sources and not lite:
        async def _entity_graph():
            try:
                from entity_graph import EntityGraph
                from traced_export import build_traced, to_dot
                g = EntityGraph()
                eg = await asyncio.to_thread(g.build_from_sources, sources)
                # v2.5.4 PATCH: traced_export 缺失导致 v2.5.2 输出与 research() 不一致，
                # 现补回（lite 模式也可保留用于诊断）
                try:
                    traced = build_traced(sources, eg)
                    if len(traced.get('nodes', [])) > 500:
                        traced['dot'] = f'# skipped: {len(traced["nodes"])} nodes exceed 500'
                    else:
                        traced['dot'] = to_dot(traced)
                except Exception:
                    traced = {'error': 'traced_export failed'}
                return {'entity_graph': eg, 'traced_export': traced}
            except Exception as e:
                return {'error': str(e)}
        tasks['entity_graph'] = asyncio.create_task(_entity_graph())
    else:
        async def _eg_skip():
            return {'skipped': 'lite_mode'}
        tasks['entity_graph'] = asyncio.create_task(_eg_skip())

    # 9: 异步 conflict_v3 + semantic_score（v2.5.2 新增）
    if sources:
        async def _conflict_v3():
            try:
                from conflict_v3 import detect_conflicts_v3
                from contradiction_scorer import score_contradiction
                v3 = await asyncio.to_thread(detect_conflicts_v3, sources, subject)
                enriched = []
                for c in v3.get('conflicts', []):
                    sc = score_contradiction(c['claim_a'], c['claim_b'])
                    c2 = dict(c)
                    c2['semantic_score'] = sc['score']
                    c2['severity'] = sc['severity']
                    enriched.append(c2)
                return {
                    'conflicts': enriched,
                    'conflict_v3': {
                        'version': v3.get('version'),
                        'raw_claims': v3.get('raw_claims'),
                        'total': len(enriched),
                        'aliases_involved': v3.get('aliases_involved', {}),
                        'live_alerts': v3.get('live_alerts', []),
                        'cross_session_summary': v3.get('cross_session_summary', {}),
                    },
                    'contradiction_scoring': {
                        'enabled': True,
                        'version': '2.4.0',
                        'scored': len(enriched),
                        'method': 'local',
                    },
                }
            except Exception as e:
                return {'error': str(e)}
        tasks['conflict'] = asyncio.create_task(_conflict_v3())
    else:
        async def _c_skip():
            return {'conflicts': [], 'conflict_v3': {'total': 0}}
        tasks['conflict'] = asyncio.create_task(_c_skip())

    # 10: 异步 entity_profile + heat_ranking + trajectory（v2.5.2 新增合并）
    if sources and not lite:
        async def _profile_heat_traj():
            try:
                from entity_profile import EntityProfile
                profiler = EntityProfile()
                # entity_index 来自 entity_graph 输出
                # 先用简化版：直接基于 sources 跑
                ei_result = await asyncio.to_thread(profiler.update_profiles, sources, [])
                from entity_heat import get_heat_ranking
                heat_ranking = await asyncio.to_thread(get_heat_ranking, 10)
                from entity_trajectory import trace_entity
                # trajectory_top5 需要 entity_index，此处用 sources 主体（兜底）
                traj = []
                for s in sources[:5]:
                    title = s.get('title', '')[:20]
                    if title:
                        traj.append(await asyncio.to_thread(trace_entity, title, 90))
                return {
                    'entity_profiles': ei_result,
                    'heat_ranking': heat_ranking,
                    'trajectory_top5': traj,
                }
            except Exception as e:
                return {'error': str(e)}
        tasks['profile_heat_traj'] = asyncio.create_task(_profile_heat_traj())
    else:
        async def _p_skip():
            return {
                'entity_profiles': {'skipped': 'lite_mode'},
                'heat_ranking': {'skipped': 'lite_mode'},
                'trajectory_top5': {'skipped': 'lite_mode'},
            }
        tasks['profile_heat_traj'] = asyncio.create_task(_p_skip())

    # 等待所有任务完成（v2.5.2 深度异步核心）
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    task_results = dict(zip(tasks.keys(), results))

    # 异常处理
    for k, v in task_results.items():
        if isinstance(v, Exception):
            task_results[k] = {'error': str(v)}

    # 同步步骤（无 IO / 纯计算）
    scored = task_results.get('scored', [])
    wikidata_verified = task_results.get('wikidata', {'skipped': 'lite_mode'})
    eg_combined = task_results.get('entity_graph', {'skipped': 'lite_mode'})
    if isinstance(eg_combined, dict) and 'entity_graph' in eg_combined:
        entity_graph = eg_combined.get('entity_graph', {})
        traced_export = eg_combined.get('traced_export', {})
    else:
        entity_graph = eg_combined
        traced_export = {'skipped': 'lite_mode'}
    conflict_data = task_results.get('conflict', {'conflicts': [], 'conflict_v3': {'total': 0}})
    profile_data = task_results.get('profile_heat_traj', {})

    # render_report 同步（v2.5.2 不深度异步化）
    try:
        report = render_report(subject, sources, format=output_format, domain=domain)
    except Exception as e:
        report = f'[error] {e}'

    # entity_index 跨源合并（v2.5.4 PATCH: 修复 v2.5.2 简化为每源独立的 bug）
    from ner import extract_entities
    try:
        ei_raw = []
        for s in sources:
            txt = ' '.join([
                s.get('text', '') or s.get('snippet', '') or s.get('title', ''),
                s.get('title', ''),
            ])[:200]
            ei_raw.extend(extract_entities(txt))
        # 聚合去重（与 research() 步骤 7 逻辑一致）
        entity_index = []
        entity_map = {}
        for e in ei_raw:
            name = e['entity_name']
            if name not in entity_map:
                entity_map[name] = {
                    'entity_name': name,
                    'entity_type': e.get('entity_type', 'UNKNOWN'),
                    'hit_count': 0,
                    'match_methods': [],
                }
            entity_map[name]['hit_count'] += 1
            method = e.get('match_method', 'unknown')
            if method not in entity_map[name]['match_methods']:
                entity_map[name]['match_methods'].append(method)
        entity_index = sorted(
            entity_map.values(),
            key=lambda x: (-x['hit_count'], x['entity_name']),
        )
    except Exception:
        entity_index = []

    return {
        'subject': subject,
        'domain': domain,
        'scored_sources': scored,
        'conflicts': conflict_data.get('conflicts', []),
        'report': report,
        'entity_index': entity_index,
        'entity_graph': entity_graph,
        'traced_export': traced_export,
        'conflict_v3': conflict_data.get('conflict_v3', {'total': 0}),
        'contradiction_scoring': conflict_data.get('contradiction_scoring', {'enabled': False}),
        'wikidata_verified': wikidata_verified,
        'entity_profiles': profile_data.get('entity_profiles', {}),
        'heat_ranking': profile_data.get('heat_ranking', []),
        'trajectory_top5': profile_data.get('trajectory_top5', []),
        'version': '2.5.2',
        'async_mode': True,
        'async_version': '2.5.2',
        'lite': lite,
    }


# v2.6.2 PATCH: lite_research 独立 API（async_research lite=True 的便捷别名）
async def lite_research(subject: str,
                      sources: Optional[List[Dict]] = None,
                      domain: Optional[str] = None) -> Dict[str, Any]:
    """v2.6.2 新增：lite 异步研究便捷入口

    等价于 asyncio.run(async_research(subject, sources, domain, lite=True))，
    但参数更少，更易调用。

    用法：
        res = asyncio.run(lite_research('AI', sources))
    """
    return await async_research(subject, sources=sources, domain=domain, lite=True)


# streaming research：流式研究（AsyncIterator 协议）
async def streaming_research(subject: str,
                            sources: Optional[List[Dict]] = None,
                            domain: Optional[str] = None,
                            output_format: str = 'md',
                            lite: bool = False) -> 'AsyncIterator[Dict]':
    """流式研究（AsyncIterator，逐步 yield 中间结果）

    yield 顺序（每步独立 yield dict）:
    1. score_complete：scored_sources
    2. wikidata_complete：wikidata_verified
    3. entity_graph_complete：entity_graph + traced_export
    4. conflict_complete：conflicts + conflict_v3
    5. profile_complete：entity_profiles
    6. trajectory_complete：heat_ranking + trajectory_top5
    7. report_complete：report

    适用：
    - MCP 工具调用：first yield <500ms 即可见部分结果
    - Web SSE 流式响应
    - 大模型工具调用（中途中断）

    用法：
        async for partial in streaming_research('AI', sources):
            print(partial['step'], '...')
    """
    import asyncio
    sources = sources or []

    # 任务调度：与 async_research 相同的 5 个 task
    tasks = {}

    # 1+2: 评分
    if sources:
        tasks['scored'] = asyncio.create_task(
            asyncio.to_thread(score_sources_batch_async, sources, subject)
        )
    else:
        async def _empty():
            return []
        tasks['scored'] = asyncio.create_task(_empty())

    # 6: wikidata
    if not lite and sources:
        async def _wikidata():
            try:
                sys.path.insert(0, str(INFOSEEK_ROOT / 'core'))
                from wikidata_sync import WikidataSync
                sync = WikidataSync()
                url_entities = []
                for s in sources[:10]:
                    txt = s.get('snippet', '') or s.get('title', '')
                    url_entities.append(txt[:20] if txt else subject)
                verify_results = await sync.verify_batch_async([subject] + url_entities)
                return {
                    'subject_verified': verify_results.get(subject, False),
                    'sources_verified': sum(1 for v in verify_results.values() if v),
                    'available': True,
                }
            except Exception as e:
                return {'available': False, 'error': str(e)}
        tasks['wikidata'] = asyncio.create_task(_wikidata())
    else:
        async def _wd_skip():
            return {'skipped': 'lite_mode'}
        tasks['wikidata'] = asyncio.create_task(_wd_skip())

    # 8: entity_graph + traced_export
    if sources and not lite:
        async def _entity_graph():
            try:
                from entity_graph import build_from_sources_async
                eg = await build_from_sources_async(sources)
                return {'entity_graph': eg, 'traced_export': eg}  # 简化
            except Exception as e:
                return {'error': str(e)}
        tasks['entity_graph'] = asyncio.create_task(_entity_graph())
    else:
        async def _eg_skip():
            return {'skipped': 'lite_mode'}
        tasks['entity_graph'] = asyncio.create_task(_eg_skip())

    # 9: conflict_v3 + semantic
    if sources:
        async def _conflict():
            try:
                from conflict_v3 import detect_conflicts_v3_async
                from contradiction_scorer import score_contradiction_async
                v3 = await detect_conflicts_v3_async(sources, subject)
                enriched = []
                for c in v3.get('conflicts', []):
                    sc = await score_contradiction_async(c['claim_a'], c['claim_b'])
                    c2 = dict(c)
                    c2['semantic_score'] = sc['score']
                    c2['severity'] = sc['severity']
                    enriched.append(c2)
                return {
                    'conflicts': enriched,
                    'conflict_v3': {
                        'version': v3.get('version'),
                        'raw_claims': v3.get('raw_claims'),
                        'total': len(enriched),
                        'live_alerts': v3.get('live_alerts', []),
                        'cross_session_summary': v3.get('cross_session_summary', {}),
                    },
                }
            except Exception as e:
                return {'error': str(e)}
        tasks['conflict'] = asyncio.create_task(_conflict())
    else:
        async def _c_skip():
            return {'conflicts': [], 'conflict_v3': {'total': 0}}
        tasks['conflict'] = asyncio.create_task(_c_skip())

    # 10+10.1+10.2: profile+heat+trajectory
    if sources and not lite:
        async def _profile_heat_traj():
            try:
                from entity_profile import EntityProfile
                from entity_heat import get_heat_ranking_async
                from entity_trajectory import trace_entity_async
                profiler = EntityProfile()
                ei = await asyncio.to_thread(profiler.update_profiles, sources, [])
                heat = await get_heat_ranking_async(top_n=10)
                traj = []
                for s in sources[:5]:
                    title = s.get('title', '')[:20]
                    if title:
                        traj.append(await trace_entity_async(title, 90))
                return {
                    'entity_profiles': ei,
                    'heat_ranking': heat,
                    'trajectory_top5': traj,
                }
            except Exception as e:
                return {'error': str(e)}
        tasks['profile_heat_traj'] = asyncio.create_task(_profile_heat_traj())
    else:
        async def _p_skip():
            return {
                'entity_profiles': {'skipped': 'lite_mode'},
                'heat_ranking': {'skipped': 'lite_mode'},
                'trajectory_top5': {'skipped': 'lite_mode'},
            }
        tasks['profile_heat_traj'] = asyncio.create_task(_p_skip())

    # 流式 yield 顺序（v3.0.0 GA 协议）
    yield_order = ['scored', 'wikidata', 'entity_graph', 'conflict', 'profile_heat_traj']

    # 收集结果（用于最后合并）
    results = {}
    for step_name in yield_order:
        task = tasks[step_name]
        try:
            result = await task
            results[step_name] = result if not isinstance(result, Exception) else {'error': str(result)}
        except Exception as e:
            results[step_name] = {'error': str(e)}

        # v3.0.0 GA yield 协议
        if step_name == 'scored':
            yield {'step': 'score_complete', 'scored_sources': results['scored']}
        elif step_name == 'wikidata':
            yield {'step': 'wikidata_complete', 'wikidata_verified': results['wikidata']}
        elif step_name == 'entity_graph':
            eg = results['entity_graph']
            if isinstance(eg, dict) and 'entity_graph' in eg:
                yield {'step': 'entity_graph_complete',
                       'entity_graph': eg.get('entity_graph'),
                       'traced_export': eg.get('traced_export')}
            else:
                yield {'step': 'entity_graph_complete', 'entity_graph': eg, 'traced_export': {'skipped': True}}
        elif step_name == 'conflict':
            cd = results['conflict']
            yield {'step': 'conflict_complete',
                   'conflicts': cd.get('conflicts', []),
                   'conflict_v3': cd.get('conflict_v3', {})}
        elif step_name == 'profile_heat_traj':
            pt = results['profile_heat_traj']
            yield {'step': 'profile_complete', 'entity_profiles': pt.get('entity_profiles', {})}
            yield {'step': 'trajectory_complete',
                   'heat_ranking': pt.get('heat_ranking', []),
                   'trajectory_top5': pt.get('trajectory_top5', [])}

    # 同步步骤（render_report）
    try:
        report = await asyncio.to_thread(render_report, subject, sources, output_format, domain)
    except Exception as e:
        report = f'[error] {e}'

    yield {'step': 'report_complete', 'report': report}


# ═══════════════════════════════════════════════════════════════
# v1 → v2 Deprecation Shim（兼容层）
# ═══════════════════════════════════════════════════════════════

def _deprecation_warning(old_func: str, new_func: str):
    """发出 deprecation 警告"""
    warnings.warn(
        f"{old_func} is deprecated since v2.0.0; use {new_func} instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def v1_calculate_score_shim(*args, **kwargs):
    """v1 anchor_adapter.calculate_score() 的 shim"""
    _deprecation_warning('anchor_adapter.calculate_score()', 'infoseek_core_v2.score_source()')
    sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
    from anchor_adapter import calculate_score
    return calculate_score(*args, **kwargs)


def v1_detect_conflicts_shim(*args, **kwargs):
    """v1 conflict_detection.detect_conflicts() 的 shim"""
    _deprecation_warning('conflict_detection.detect_conflicts()', 'infoseek_core_v2.detect_conflicts()')
    return detect_conflicts(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    """CLI: python infoseek_core_v2.py <subject>"""
    if len(sys.argv) < 2:
        print("Usage: python infoseek_core_v2.py <subject> [--domain X] [--with-llm] [--format md]")
        sys.exit(1)

    subject = sys.argv[1]
    domain = None
    with_llm = False
    output_format = 'md'

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--domain' and i + 1 < len(args):
            domain = args[i + 1]
            i += 2
        elif args[i] == '--with-llm':
            with_llm = True
            i += 1
        elif args[i] == '--format' and i + 1 < len(args):
            output_format = args[i + 1]
            i += 2
        else:
            i += 1

    result = research(subject, domain=domain, with_llm=with_llm, output_format=output_format)
    print(result['report'])
    if 'llm_insights' in result:
        print('\n--- LLM Insights ---')
        print(result['llm_insights'])


if __name__ == '__main__':
    main()