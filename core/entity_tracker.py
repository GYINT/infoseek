#!/usr/bin/env python3
"""
core/entity_tracker.py — Infoseek 实体追踪器（v2.1.0 新增）

功能：
- record_hit(): NER 识别后记录一次命中
- apply_decay(): 90 天半衰期衰减 hit_count_30d
- get_hot_entities(): 返回 hit_count top N
- get_stale_entities(): 返回 90 天未使用的
- get_stats(): 综合统计

数据源：core/entities.py（v2.1.0 含元数据）
"""

import sys
from datetime import datetime, date
from typing import List, Dict, Optional
from pathlib import Path

CORE_DIR = Path(__file__).parent

# v2.4.2 PATCH (P2 顺手): EntityTracker 模块级单例化
# 测试环境 predict_heat × 200 每次都新建实例耗时 40ms；
# 单例后稳定在 <1ms（与单跑一致），并惠及所有用 EntityTracker 的接口
_TRACKER_INSTANCE = None

def get_tracker() -> 'EntityTracker':
    """获取 EntityTracker 单例（v2.4.2 新增）"""
    global _TRACKER_INSTANCE
    if _TRACKER_INSTANCE is None:
        _TRACKER_INSTANCE = EntityTracker()
    return _TRACKER_INSTANCE

def reset_tracker():
    """手动失效单例（测试/单元 reset 用）"""
    global _TRACKER_INSTANCE
    _TRACKER_INSTANCE = None
sys.path.insert(0, str(CORE_DIR))


def _today() -> date:
    return date.today()


def _date_diff_days(date_str: str, base: Optional[date] = None) -> int:
    """计算 days since"""
    if not date_str:
        return 999
    try:
        d = datetime.fromisoformat(date_str).date()
        base = base or _today()
        return (base - d).days
    except Exception:
        return 999


class EntityTracker:
    """v2.1.0 实体追踪器"""

    def __init__(self,
                 decay_days: int = 90,
                 half_life_factor: float = 0.5,
                 stale_threshold_days: int = 90):
        self.decay_days = decay_days
        self.half_life_factor = half_life_factor
        self.stale_threshold = stale_threshold_days

    def _all_entities(self) -> List[Dict]:
        """加载所有实体"""
        from entities import get_all_entities
        return get_all_entities()

    def _find_entity(self, name: str) -> Optional[Dict]:
        """按 name 查找实体"""
        for e in self._all_entities():
            if e['name'] == name:
                return e
        return None

    def record_hit(self, entity_name: str) -> bool:
        """NER 识别后调用：记录一次命中 + 更新 last_seen_at

        返回 True=成功，False=实体不存在
        """
        entity = self._find_entity(entity_name)
        if not entity:
            return False

        entity['hit_count_30d'] = entity.get('hit_count_30d', 0) + 1
        entity['last_seen_at'] = _today().isoformat()
        return True

    def record_hits_batch(self, names: List[str]) -> dict:
        """批量记录"""
        success = sum(1 for n in names if self.record_hit(n))
        return {
            'total': len(names),
            'success': success,
            'unknown': len(names) - success,
        }

    def apply_decay(self) -> dict:
        """应用 90 天半衰期衰减

        算法：
          age = days since last_seen
          if age > 90: factor = 0.5^((age-90)/90)
          hit_count *= factor
        """
        entities = self._all_entities()
        decayed_count = 0
        total_reduction = 0

        for e in entities:
            hit = e.get('hit_count_30d', 0)
            if hit == 0:
                continue

            age = _date_diff_days(e.get('last_seen_at', ''))
            if age <= self.decay_days:
                continue

            # 半衰期公式
            factor = self.half_life_factor ** ((age - self.decay_days) / self.decay_days)
            new_hit = max(0, int(hit * factor))

            if new_hit < hit:
                total_reduction += (hit - new_hit)
                decayed_count += 1
                e['hit_count_30d'] = new_hit

        return {
            'decayed_count': decayed_count,
            'total_reduction': total_reduction,
            'total_entities': len(entities),
        }

    def get_hot_entities(self, top_n: int = 10, min_hit: int = 1) -> List[Dict]:
        """返回 hit_count_30d 最高的 top N

        按 hit_count_30d 降序
        """
        entities = self._all_entities()
        hot = sorted(
            [e for e in entities if e.get('hit_count_30d', 0) >= min_hit],
            key=lambda x: -x['hit_count_30d'],
        )
        return [
            {
                'name': e['name'],
                'category': e.get('category', ''),
                'hit_count_30d': e['hit_count_30d'],
                'last_seen_at': e.get('last_seen_at', ''),
            }
            for e in hot[:top_n]
        ]

    def get_stale_entities(self, threshold_days: Optional[int] = None) -> List[Dict]:
        """返回 N 天未使用的冷条目"""
        threshold = threshold_days or self.stale_threshold
        entities = self._all_entities()
        stale = []
        for e in entities:
            age = _date_diff_days(e.get('last_seen_at', ''))
            if age > threshold:
                stale.append({
                    'name': e['name'],
                    'category': e.get('category', ''),
                    'age_days': age,
                    'last_seen_at': e.get('last_seen_at', ''),
                    'hit_count_30d': e.get('hit_count_30d', 0),
                })
        return stale

    def get_stats(self) -> Dict:
        """综合统计"""
        entities = self._all_entities()
        total = len(entities)
        total_hit = sum(e.get('hit_count_30d', 0) for e in entities)

        # 按 source 分类
        sources = {}
        for e in entities:
            s = e.get('source', 'manual')
            sources[s] = sources.get(s, 0) + 1

        # 热/冷条目统计
        hot = len([e for e in entities if e.get('hit_count_30d', 0) >= 5])
        stale = len(self.get_stale_entities())

        # 类别分布
        categories = {}
        for e in entities:
            cat = e.get('category', 'UNKNOWN')
            categories[cat] = categories.get(cat, 0) + 1

        return {
            'total_entities': total,
            'total_hit_count': total_hit,
            'avg_hit_count': round(total_hit / total, 2) if total else 0,
            'hot_entities': hot,
            'stale_entities': stale,
            'by_source': sources,
            'by_category': dict(sorted(categories.items(), key=lambda x: -x[1])),
        }


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m core.entity_tracker [stats | decay | hot | stale]")
        sys.exit(1)

    cmd = sys.argv[1]
    tracker = EntityTracker()

    if cmd == 'stats':
        import json
        stats = tracker.get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif cmd == 'decay':
        result = tracker.apply_decay()
        print(f"衰减: {result}")
    elif cmd == 'hot':
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for e in tracker.get_hot_entities(top_n=n):
            print(f"  {e['name']:25s} hit={e['hit_count_30d']:3d} ({e['category']})")
    elif cmd == 'stale':
        for e in tracker.get_stale_entities():
            print(f"  {e['name']:25s} age={e['age_days']}d ({e['category']})")
    else:
        print(f"未知命令: {cmd}")


if __name__ == '__main__':
    main()