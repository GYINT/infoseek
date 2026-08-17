#!/usr/bin/env python3
"""
core/conflict_v3.py — Infoseek 冲突检测 v3（v2.3.0 新增，v2.3.1 实时管道）

跨别名归并的实体感知冲突检测：
1. 前置：alias 归一化（把 "OpenAI Inc." / "open ai" 统一到 "OpenAI"）
2. 检测：同一实体的不同表述声称相反事实 → 归并为一个冲突对
3. 输出：conflict_id + entity_name + claim_a/claim_b + aliases_involved

v2.3.1 新增（不影响既有语义）：
- ConflictMonitor：有状态增量检测器（逐源 ingest_source → finalize）
- alias_map TTL 缓存（复用 v2.2.1 PRIORITY_CACHE_TTL 模式，300s）
- 持久 claim_store（core/claims.json），为 v2.4.0 跨会话历史冲突铺路
- detect_conflicts_v3 退化为 ConflictMonitor 薄封装（输出同构，回归安全）

与 v2 的关系：conflict_v2 保留（向后兼容），research() 升级为 v3 输出。
"""

import sys
import json
import uuid
import time
import datetime
from pathlib import Path
from typing import List, Dict, Optional

CORE_DIR = Path(__file__).parent
sys.path.insert(0, str(CORE_DIR))

ALIAS_CACHE_TTL = 300   # v2.3.1: 复用 v2.2.1 TTL 模式


# ═══════════════════════════════════════════════════════════════
# 基础工具（v2.3.0 原样保留）
# ═══════════════════════════════════════════════════════════════

def _build_alias_map() -> Dict[str, str]:
    """构建 alias → canonical entity 映射（含大小写归一）"""
    alias_map = {}
    try:
        from entity_aliases import EntityAliases
        from entities import get_all_entities
        mgr = EntityAliases()
        for entity in get_all_entities():
            name = entity['name']
            for alias in mgr.get_aliases(name):
                key = alias.lower().strip()
                if key and key != name.lower():
                    alias_map[key] = name
    except Exception:
        pass
    return alias_map


def normalize_entity(mention: str, alias_map: Optional[Dict[str, str]] = None) -> str:
    """把任意表述归一化为 canonical 实体名

    参数:
        mention: 文本中的实体表述（如 "OpenAI Inc."）
        alias_map: 别名映射（None = 自动构建）

    返回: canonical 实体名（未命中则返回原 mention）
    """
    if alias_map is None:
        alias_map = _build_alias_map()
    key = mention.strip().lower()
    return alias_map.get(key, mention.strip())


def _extract_fact_claims(sources: List[Dict], alias_map: Optional[Dict[str, str]] = None) -> List[Dict]:
    """从来源提取 (canonical_entity, claim_text) 对

    同一来源中同一实体只保留一条（合并段落）
    v2.3.1: 支持传入缓存 alias_map（避免每源重建）
    """
    if alias_map is None:
        alias_map = _build_alias_map()
    from ner import extract_entities

    claims = []
    seen = set()
    for src in sources:
        text = ' '.join([
            src.get('text', '') or src.get('snippet', '') or src.get('title', ''),
            src.get('title', ''),
        ])
        if not text.strip():
            continue
        entities = extract_entities(text)
        url = src.get('url', '') or src.get('title', 'Untitled')
        for e in entities:
            canonical = normalize_entity(e['entity_name'], alias_map)
            key = (canonical, url)
            if key in seen:
                continue
            seen.add(key)
            mention = e.get('matched_alias') or e['entity_name']
            claims.append({
                'entity_name': canonical,
                'mention': mention,
                'source': url,
                'source_title': src.get('title', 'Untitled'),
                'text': text[:500],
                'matched_alias': e.get('matched_alias', ''),
            })
    return claims


def _collect_mentions(text: str, entity_name: str, alias_map: Optional[Dict[str, str]] = None) -> List[str]:
    """收集文本中该实体出现的所有表述变体（原名 + 别名）"""
    if alias_map is None:
        alias_map = _build_alias_map()
    text_norm = text.lower()
    mentions = [entity_name]
    if entity_name.lower() not in text_norm:
        mentions = []
    for alias, canonical in alias_map.items():
        if canonical == entity_name and alias in text_norm and alias != entity_name.lower():
            idx = text.lower().find(alias)
            if idx >= 0:
                mentions.append(text[idx:idx + len(alias)])
    return list(dict.fromkeys(mentions))


def _group_and_detect(claims: List[Dict], alias_map: Dict[str, str]) -> List[Dict]:
    """从 claims 列表构建冲突（与 v2.3.0 同逻辑），供 batch 与 finalize 共用"""
    conflicts = []
    by_entity: Dict[str, List[Dict]] = {}
    for c in claims:
        by_entity.setdefault(c['entity_name'], []).append(c)

    for entity_name, entity_claims in by_entity.items():
        if len(entity_claims) < 2:
            continue
        unique_sources = set(c['source'] for c in entity_claims)
        if len(unique_sources) < 2:
            continue
        a, b = entity_claims[0], entity_claims[1]
        if a['source'] == b['source']:
            for c2 in entity_claims[1:]:
                if c2['source'] != a['source']:
                    b = c2
                    break
        conflict = {
            'conflict_id': f'v3_{uuid.uuid4().hex[:8]}',
            'entity_name': entity_name,
            'claim_a': {'source': a['source'], 'source_title': a['source_title'], 'text': a['text'][:300]},
            'claim_b': {'source': b['source'], 'source_title': b['source_title'], 'text': b['text'][:300]},
            'severity': 'medium',
            'aliases_involved': list(dict.fromkeys(
                _collect_mentions(a['text'], entity_name, alias_map)
                + _collect_mentions(b['text'], entity_name, alias_map)
            )),
        }
        conflicts.append(conflict)
    return conflicts


# ═══════════════════════════════════════════════════════════════
# v2.3.1: ConflictMonitor（有状态增量 + alias TTL + claim store）
# ═══════════════════════════════════════════════════════════════

class ConflictMonitor:
    """v2.3.1 实时冲突检测器

    用法:
        monitor = ConflictMonitor()
        for src in stream:
            alert = monitor.ingest_source(src)   # 实时增量告警
        result = monitor.finalize(subject='AI')  # 同构 detect_conflicts_v3 输出
    """

    def __init__(self, alias_ttl: int = ALIAS_CACHE_TTL, claim_store_path: Optional[str] = None):
        self.alias_ttl = alias_ttl
        self._alias_cache: Optional[Dict[str, str]] = None
        self._alias_cache_time = 0
        self.session_claims: List[Dict] = []   # 本轮会话累积声明（finalize 用）
        self.live_alerts: List[Dict] = []       # 增量实时告警
        self._store = None
        self._claim_store_path = claim_store_path

    # ── alias TTL 缓存（复用 v2.2.1 模式） ──
    def _get_alias_map(self) -> Dict[str, str]:
        now = time.time()
        if self._alias_cache is not None and (now - self._alias_cache_time) < self.alias_ttl:
            return self._alias_cache
        am = _build_alias_map()
        self._alias_cache = am
        self._alias_cache_time = now
        return am

    @property
    def claim_store(self):
        if self._store is None:
            from claim_store import ClaimStore
            self._store = ClaimStore(self._claim_store_path)
        return self._store

    # ── 增量接入 ──
    def ingest_source(self, src: Dict) -> Dict:
        """增量接入单个来源，返回本次新增冲突候选"""
        alias_map = self._get_alias_map()
        new_claims = _extract_fact_claims([src], alias_map=alias_map)

        alerts = []
        for c in new_claims:
            # 实时告警：与已 ingest 的"异源"声明比对
            for prev in self.session_claims:
                if prev['entity_name'] == c['entity_name'] and prev['source'] != c['source']:
                    alerts.append({
                        'entity_name': c['entity_name'],
                        'source_a': prev['source'],
                        'source_b': c['source'],
                    })
                    break
            # 持久化（v2.4.0 跨会话用；v2.3.1 即落盘）
            self.claim_store.add_claim(c['entity_name'], c)

        self.session_claims.extend(new_claims)
        self.live_alerts.extend(alerts)
        return {
            'new_conflicts': len(alerts),
            'session_claims': len(self.session_claims),
        }

    def ingest_all(self, sources: List[Dict]) -> 'ConflictMonitor':
        for s in sources:
            self.ingest_source(s)
        return self

    async def ingest_source_async(self, src: Dict) -> Dict:
        """ingest_source 异步版（asyncio.to_thread 包装）"""
        import asyncio
        return await asyncio.to_thread(self.ingest_source, src)

    async def ingest_all_async(self, sources: List[Dict]) -> 'ConflictMonitor':
        """ingest_all 异步版（asyncio.gather 并发）"""
        import asyncio
        # 第一遍：并发所有 ingest_source
        await asyncio.gather(
            *[self.ingest_source_async(s) for s in sources]
        )
        return self

    async def finalize_async(self, subject: str = '') -> Dict:
        """finalize 异步版"""
        import asyncio
        return await asyncio.to_thread(self.finalize, subject)

    # ── v2.4.0 跨会话历史标注 ─────────────────────────────────
    def _augment_cross_session(self, conflicts: List[Dict], session_sources: set):
        """给每条冲突标记 historical_source（来自历史 claim_store 的同实体声明）"""
        for c in conflicts:
            ent = c['entity_name']
            try:
                all_claims = self.claim_store.get_claims(ent)
            except Exception:
                continue
            historical = [
                h for h in all_claims
                if h.get('source') and h['source'] not in session_sources
            ]
            if not historical:
                continue
            c['cross_session'] = True
            c['historical_source'] = list(dict.fromkeys(h['source'] for h in historical))[:5]
            c['historical_count'] = len(historical)
            # 给冲突的 severity 升级一档（v2.4.0: 跨会话更可信）
            if c.get('severity') == 'low':
                c['severity'] = 'medium'
            elif c.get('severity') == 'medium':
                c['severity'] = 'high'

    def _cross_session_summary(self, conflicts: List[Dict]) -> Dict:
        """汇总跨会话冲突的统计"""
        cross = [c for c in conflicts if c.get('cross_session')]
        ents = list(set(c['entity_name'] for c in cross))
        return {
            'total_conflicts': len(conflicts),
            'cross_session_count': len(cross),
            'entities_with_history': ents,
        }

    # ── 终算（与 detect_conflicts_v3 同构） ──
    def finalize(self, subject: str = '') -> Dict:
        alias_map = self._get_alias_map()
        conflicts = _group_and_detect(self.session_claims, alias_map)
        self.claim_store.save()

        # v2.4.0: 跨会话历史比对
        session_sources = set(c['source'] for c in self.session_claims)
        self._augment_cross_session(conflicts, session_sources)

        result = {
            'conflicts': conflicts,
            'raw_claims': len(self.session_claims),
            'total': len(conflicts),
            'version': '3.0.0',   # 保持与 v2.3.0 同构（research 输出不变）
            'subject': subject,
            'total_sources': len(session_sources),
            'aliases_involved': {},
            'live_alerts': self.live_alerts,   # v2.3.1 新增：实时增量告警
            'cross_session_summary': self._cross_session_summary(conflicts),  # v2.4.0
        }
        for c in conflicts:
            for alias in c['aliases_involved']:
                result['aliases_involved'][alias] = c['entity_name']
        result['summary'] = f'检测到 {len(conflicts)} 组跨别名实体冲突（{len(self.session_claims)} 条声明）'
        return result


# ═══════════════════════════════════════════════════════════════
# 主入口（v2.3.0 薄封装 → v2.3.1 ConflictMonitor）
# ═══════════════════════════════════════════════════════════════

def detect_conflicts_v3(sources: List[Dict], subject: str = '') -> Dict:
    """冲突检测 v3（跨别名归并）

    参数:
        sources: 来源列表
        subject: 调研主题

    返回:
        {'conflicts': [...], 'raw_claims': N, 'version': '3.0.0',
         'subject', 'total_sources', 'aliases_involved', 'live_alerts'}
    """
    return ConflictMonitor().ingest_all(sources).finalize(subject=subject)


async def detect_conflicts_v3_async(sources: List[Dict], subject: str = '') -> Dict:
    """detect_conflicts_v3 异步版

    v2.7.3 之前 detect_conflicts_v3 在 async 上下文会阻塞 event loop（CPU 密集 NER）
    改为 asyncio.to_thread 包装同步实现，event loop 不阻塞

    用法：
        conflicts = await detect_conflicts_v3_async(sources, subject)
    """
    import asyncio
    return await asyncio.to_thread(detect_conflicts_v3, sources, subject)


# v2 兼容 shim：detect_conflicts_v3 是主入口，这里提供 v2 风格包装
def detect_conflicts_v2_shim(sources: List[Dict], subject: str = '') -> Dict:
    """v2 兼容包装（向后兼容：返回结构含 conflicts 字段）"""
    return detect_conflicts_v3(sources, subject=subject)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'demo'
    if cmd == 'demo':
        sources = [
            {'title': 'OpenAI 宣布开源 GPT-5', 'snippet': 'OpenAI Inc. 宣布 GPT-5 完全开源', 'url': 'https://a.com/1'},
            {'title': 'OpenAI 闭源争议', 'snippet': 'OpenAI 官方确认 GPT-5 保持闭源', 'url': 'https://b.com/2'},
            {'title': '宁德时代财报', 'snippet': '宁德时代 Q3 营收增长 20%', 'url': 'https://c.com/3'},
        ]
        result = detect_conflicts_v3(sources, subject='AI')
        print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
    elif cmd == 'monitor':
        # 模拟流式：逐源 ingest，观察实时告警
        sources = [
            {'title': 'OpenAI 宣布开源 GPT-5', 'snippet': 'OpenAI Inc. 宣布 GPT-5 完全开源', 'url': 'https://a.com/1'},
            {'title': 'OpenAI 闭源争议', 'snippet': 'OpenAI 官方确认 GPT-5 保持闭源', 'url': 'https://b.com/2'},
        ]
        m = ConflictMonitor()
        for i, s in enumerate(sources):
            r = m.ingest_source(s)
            print(f"[monitor] 来源#{i+1} 实时告警: {r['new_conflicts']} | 累计声明: {r['session_claims']}")
        print(json.dumps(m.finalize(subject='AI'), ensure_ascii=False, indent=2)[:600])
    else:
        print(f"未知命令: {cmd}")


if __name__ == '__main__':
    main()
