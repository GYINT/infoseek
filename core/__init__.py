"""
core/ — Infoseek 核心层（v3.0.0 升级）

与 scripts/ 适配层（MCP/HTTP/CLI）分离，本目录只放：
- anchor_score_v2: v2 评分算法（核心）
- entities: 跨语言实体词典（100+）
- ner: 命名实体识别算法
- trust_sources: 统一信任源白名单
- llm_router: 多模型路由
- conflict_v2: 增强冲突检测（实体感知）

模块命名规范：
- v2 API 统一以 _v2 后缀（如 anchor_score_v2）
- 不向后兼容的方法须明确 deprecation warning
"""

__version__ = "2.0.0"
__all__ = [
    "anchor_score_v2",
    "entities",
    "ner",
    "trust_sources",
    "llm_router",
    "conflict_v2",
]