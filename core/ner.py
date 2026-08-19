#!/usr/bin/env python3
"""
core/ner.py — Infoseek 命名实体识别算法（v2.0.0 新增）

基于词典匹配 + 位置权重 + 跨语言别名展开的轻量 NER。

支持 5 类实体：ORG/PRODUCT/TECH/PERSON/METRIC
词典来源：core/entities.py（100+ 条目）
"""

import re
from typing import List, Dict, Optional

# v2.4.1 PATCH (DEF-E): EntityAliases 模块级单例 — 避免 extract_entities
# 每次都新建实例导致 priority_cache TTL 缓存形同虚设
_ENTITY_ALIASES_INSTANCE = None

def _get_aliases_mgr():
    global _ENTITY_ALIASES_INSTANCE
    if _ENTITY_ALIASES_INSTANCE is None:
        from entity_aliases import EntityAliases
        _ENTITY_ALIASES_INSTANCE = EntityAliases()
    return _ENTITY_ALIASES_INSTANCE


# 支持包内和独立调用两种模式
try:
    from .entities import get_all_entities
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from entities import get_all_entities


def _normalize(text: str) -> str:
    """文本归一化（统一小写 + 去空格）"""
    return re.sub(r'\s+', '', text.lower())


def _match_entity(text_norm: str, entity: dict) -> Optional[Dict]:
    """单条实体匹配（返回 span 范围）"""
    name_norm = _normalize(entity['name'])
    if name_norm in text_norm:
        idx = text_norm.find(name_norm)
        return {
            'entity_type': entity.get('category', 'UNKNOWN'),
            'entity_name': entity['name'],
            'span': (idx, idx + len(name_norm)),
            'match_method': 'name',
        }

    # 别名匹配
    for alias in entity.get('aliases', []):
        alias_norm = _normalize(alias)
        if len(alias_norm) < 2:
            continue
        if alias_norm in text_norm:
            idx = text_norm.find(alias_norm)
            return {
                'entity_type': entity.get('category', 'UNKNOWN'),
                'entity_name': entity['name'],
                'matched_alias': alias,
                'span': (idx, idx + len(alias_norm)),
                'match_method': 'alias',
            }
    return None


def extract_entities(text: str, entity_types: Optional[List[str]] = None) -> List[Dict]:
    """提取文本中的所有实体

    参数:
        text: 输入文本（中文/英文/混合）
        entity_types: 限制实体类型（默认 None = 全部 5 类）

    返回:
        实体列表 [{entity_type, entity_name, span, match_method, ...}, ...]
    """
    if not text:
        return []

    text_norm = _normalize(text)
    entities = get_all_entities()

    found = []
    for entity in entities:
        # 类型过滤
        if entity_types and entity.get('category') not in entity_types:
            continue
        match = _match_entity(text_norm, entity)
        if match:
            found.append(match)

    # 按 span 排序 + 去重（同一位置只保留一个）
    found.sort(key=lambda e: e['span'][0])
    deduped = []
    last_end = -1
    for e in found:
        if e['span'][0] >= last_end:
            deduped.append(e)
            last_end = e['span'][1]

    # v2.1.0 集成：自动调用 entity_tracker.record_hit 记录命中
    try:
        from entity_tracker import EntityTracker
        tracker = EntityTracker()
        for e in deduped:
            tracker.record_hit(e['entity_name'])
    except Exception:
        pass  # 静默失败，不影响 NER 主流程

    # v2.1.1 集成：同时识别 aliases.json 中的运行时别名
    # v2.2.0 升级：高频别名优先检索（hot > cold > static）
    # v2.4.1 PATCH (DEF-E): 用模块级单例 mgr 让 priority_cache 真正生效
    try:
        mgr = _get_aliases_mgr()
        text_norm = _normalize(text)
        for entity_dict in get_all_entities():
            # 已识别的实体跳过（保持 deduped 语义）
            if any(d['entity_name'] == entity_dict['name'] for d in deduped):
                continue
            # v2.2.0: 获取分级别名（static/hot/cold），按优先级匹配
            prioritized = mgr.get_prioritized_aliases(entity_dict['name'])
            matched_alias = None
            match_priority = None
            # 优先级：hot（高频）→ static（权威）→ cold（低频）
            for priority, alias_list in [('hot', prioritized.get('hot', [])),
                                         ('static', prioritized.get('static', [])),
                                         ('cold', prioritized.get('cold', []))]:
                for alias in alias_list:
                    alias_norm = _normalize(alias)
                    if len(alias_norm) < 2:
                        continue
                    if alias_norm in text_norm:
                        matched_alias = alias
                        match_priority = priority
                        break
                if matched_alias:
                    break
            if matched_alias:
                # v2.3.0 修复：span 重叠检查（防止子串误报，如 'pe' ⊂ 'openai'）
                idx = text_norm.find(_normalize(matched_alias))
                if idx >= 0:
                    overlap = False
                    for d in deduped:
                        ds, de = d['span']
                        if ds >= 0 and not (idx + len(_normalize(matched_alias)) <= ds or idx >= de):
                            overlap = True
                            break
                    if overlap:
                        continue
                deduped.append({
                    'entity_type': entity_dict.get('category', 'UNKNOWN'),
                    'entity_name': entity_dict['name'],
                    'matched_alias': matched_alias,
                    'span': (idx, idx + len(_normalize(matched_alias))) if idx >= 0 else (-1, -1),
                    'match_method': f'v220_alias_{match_priority}',
                })
    except Exception:
        pass

    return deduped


def extract_by_category(text: str, category: str) -> List[str]:
    """按类别提取（仅返回实体名称）"""
    entities = extract_entities(text)
    return [e['entity_name'] for e in entities if e.get('entity_type') == category]


def has_entity(text: str, entity_name: str) -> bool:
    """检测文本是否包含某个实体"""
    found = extract_entities(text)
    return any(e['entity_name'].lower() == entity_name.lower() for e in found)


def entity_coverage(text: str, reference_entities: List[str]) -> float:
    """计算 reference 实体在 text 中的覆盖率

    用于检测"同一主题不同源"对同一实体的覆盖一致性
    """
    if not reference_entities:
        return 1.0
    found = extract_entities(text)
    found_names = {e['entity_name'].lower() for e in found}
    covered = sum(1 for r in reference_entities if r.lower() in found_names)
    return covered / len(reference_entities)


# ═══════════════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    test_texts = [
        "OpenAI 发布了 GPT-4o 模型，运行在 Microsoft Azure 云上。",
        "宝钢与 ArcelorMittal 在钢卷分切工艺上展开合作。",
        "宁德时代 PE 估值 25-30 倍，CATL 股价上涨。",
        "Claude 3.5 Sonnet 与 Gemini Pro 比较。",
        "Hugging Face 与 PyTorch 社区推动 LLM 训练。",
    ]
    for text in test_texts:
        entities = extract_entities(text)
        print(f"\n文本: {text}")
        print(f"  实体 ({len(entities)}):")
        for e in entities:
            print(f"    - {e['entity_type']:12s} | {e['entity_name']:20s} | match={e['match_method']}")