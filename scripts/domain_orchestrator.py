#!/usr/bin/env python3
"""
domain_orchestrator.py — Infoseek 领域调度器（v1.9.0）

整合 domain_router + domain profile + Jinja2 模板 + Anchor_Score 领域加权，
端到端完成"主题 → 领域 → 模板 → 评分 → 报告"全流程。

调用链：
  1. detect_domain(subject) → 选定 profile
  2. 加载 profile YAML（信任源 + 关键词模板）
  3. 加载 Jinja2 模板（每领域一个）
  4. 应用 profile 权重 → 给每个 source 打分
  5. 渲染模板 → 输出 Markdown 报告
  6. 返回渲染结果 + 领域元数据

CLI 用法:
  python domain_orchestrator.py "<subject>" < sources.json > out.md
"""

import os
import sys
import json
import yaml
from pathlib import Path
from typing import Optional

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE', str(Path.home() / 'infoseek')))
INFOSEEK_ROOT = Path(os.environ.get('INFOSEEK_ROOT', str(Path(__file__).parent.parent)))


class DomainOrchestrator:
    """领域调度器（v1.9.0 主推①）"""

    def __init__(self, profile_dir: str = None, template_dir: str = None):
        self.profile_dir = Path(profile_dir) if profile_dir else INFOSEEK_ROOT / 'domains'
        self.template_dir = Path(template_dir) if template_dir else self.profile_dir / 'templates'
        self.profiles = self._load_all_profiles()
        self.templates = self._load_all_templates()

    def _load_all_profiles(self) -> dict:
        """加载所有领域 profile YAML"""
        profiles = {}
        for f in sorted(self.profile_dir.glob('*.yaml')):
            if f.stem == 'README':
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    profiles[f.stem] = {
                        'name': f.stem,
                        'raw': fp.read(),
                        'path': str(f),
                    }
            except Exception:
                pass
        return profiles

    def _load_all_templates(self) -> dict:
        """加载所有 Jinja2 模板（v2.0.0：模板并入 domains/templates.yaml）

        存储演进：
          v1.x：domains/templates/*.md.j2 独立文件（平台禁止 .j2 类型）
          v2.0：domains/templates.yaml 块标量合并（同文保真，兼容回退旧目录）
        """
        templates = {}
        yaml_file = self.profile_dir / 'templates.yaml'
        if yaml_file.exists():
            try:
                with open(yaml_file, 'r', encoding='utf-8') as fp:
                    data = yaml.safe_load(fp) or {}
                for name, raw in (data.get('templates') or {}).items():
                    if isinstance(raw, str):
                        templates[name] = {
                            'name': f'{name}.md',
                            'raw': raw,
                            'path': f'{yaml_file.name}#{name}',
                        }
            except Exception:
                pass
        # 回退：旧版独立 .md.j2 目录（兼容历史部署）
        if not templates and self.template_dir.exists():
            for f in sorted(self.template_dir.glob('*.md.j2')):
                try:
                    with open(f, 'r', encoding='utf-8') as fp:
                        template_text = fp.read()
                    templates[f.stem.replace('.md', '')] = {
                        'name': f.stem,
                        'raw': template_text,
                        'path': str(f),
                    }
                except Exception:
                    pass
        return templates

    def detect(self, subject: str) -> dict:
        """检测领域（包装 domain_router.detect_domain）"""
        sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
        try:
            from domain_router import detect_domain
            return detect_domain(subject)
        except ImportError:
            return {'domain': None, 'score': 0, 'is_default': True, 'profile_path': None}

    def apply_to_scoring(self, source: dict, subject: str) -> dict:
        """应用领域权重给单个源打分（包装 anchor_adapter.calculate_score）

        返回合并 dict：原 source 字段 + 评分结果
        """
        sys.path.insert(0, str(INFOSEEK_ROOT / 'scripts'))
        try:
            from anchor_adapter import calculate_score
        except ImportError:
            return source

        domain_result = self.detect(subject)
        profile = None
        if domain_result.get('profile_path'):
            profile = {
                'name': domain_result['domain'],
                'raw': open(domain_result['profile_path'], encoding='utf-8').read(),
            }

        score_result = calculate_score(
            source, subject,
            with_domain=bool(profile),
            domain_profile=profile,
        )

        # 合并：保留原字段 + 评分结果
        merged = dict(source)
        merged['_scoring'] = score_result
        merged['final_score'] = score_result.get('after_whitelist', score_result.get('raw_score', 0))
        return merged

    def render_report(self, subject: str, sources: list,
                      min_score: int = 40,
                      domain_override: str = None) -> dict:
        """按领域模板渲染最终报告

        参数:
            subject: 调研主题
            sources: 来源列表 [{title, url, platform, score, snippet, ...}, ...]
            min_score: 最低分数阈值
            domain_override: 手动指定领域（默认 None = 自动检测）

        返回:
            {
                'subject': subject,
                'domain': 'tech-research',
                'template_used': '.../tech-research.md.j2',
                'markdown': '...',
                'qualified_count': N,
                'total_count': M,
                'is_default_template': False,
            }
        """
        # 1. 检测 / 覆盖领域
        if domain_override:
            domain = domain_override
            is_default = False
        else:
            detect_result = self.detect(subject)
            domain = detect_result.get('domain')
            is_default = detect_result.get('is_default', True)

        # 2. 过滤低分源
        qualified = [s for s in sources if s.get('score', 0) >= min_score]

        # 3. 应用领域打分（可选）
        scored = []
        for s in qualified:
            scored_source = self.apply_to_scoring(s, subject)
            scored.append(scored_source)

        # 4. 加载模板
        template_name = domain if domain and domain in self.templates else 'default'
        template_info = self.templates.get(template_name)
        is_default_template = template_name == 'default'

        # 5. 渲染 Markdown（用 final_score 替代 score 字段以兼容模板）
        rendered_sources = []
        for s in scored:
            rs = dict(s)
            if 'final_score' in rs and 'score' not in rs:
                rs['score'] = rs['final_score']
            rendered_sources.append(rs)

        # 6. 渲染模板
        if template_info:
            markdown = self._render_jinja2(template_info['raw'], {
                'subject': subject,
                'domain': domain,
                'sources': rendered_sources,
                'sources_count': len(rendered_sources),
                'is_default_template': is_default_template,
            })
        else:
            markdown = self._render_fallback(subject, domain, rendered_sources)

        return {
            'subject': subject,
            'domain': domain,
            'template_used': template_info['path'] if template_info else 'fallback',
            'markdown': markdown,
            'qualified_count': len(qualified),
            'total_count': len(sources),
            'is_default_template': is_default_template,
        }

    def _render_jinja2(self, template_text: str, context: dict) -> str:
        """使用 Jinja2 渲染模板（如未安装则回退到简单替换）"""
        try:
            from jinja2 import Template
            tmpl = Template(template_text)
            return tmpl.render(**context)
        except ImportError:
            return self._render_simple(template_text, context)

    def _render_simple(self, template_text: str, context: dict) -> str:
        """无 Jinja2 时的简易渲染（{{ var }} 形式）"""
        result = template_text
        result = result.replace('{{ subject }}', str(context.get('subject', '')))
        result = result.replace('{{ domain }}', str(context.get('domain', '')))
        # 简化：sources 列表只插入计数
        sources = context.get('sources', [])
        result = result.replace('{{ sources_count }}', str(len(sources)))
        # sources 列表：简易循环渲染
        if '{% for s in sources %}' in result:
            parts = result.split('{% for s in sources %}')
            if len(parts) == 2:
                head, rest = parts
                loop_body, tail = rest.split('{% endfor %}', 1)
                rendered_sources = ''.join(loop_body.replace('{{ s.title }}', str(s.get('title', '')))
                                          .replace('{{ s.score }}', str(s.get('score', 0)))
                                          .replace('{{ s.url }}', str(s.get('url', '')))
                                          .replace('{{ s.platform }}', str(s.get('platform', '')))
                                          .replace('{{ s.snippet }}', str(s.get('snippet', '')))
                                          for s in sources)
                result = head + rendered_sources + tail
        return result

    def _render_fallback(self, subject: str, domain: Optional[str], sources: list) -> str:
        """无模板时的兜底渲染"""
        lines = [f"# {subject}", ""]
        if domain:
            lines.append(f"> 领域：**{domain}**")
        lines.append(f"> 来源：{len(sources)} 条")
        lines.extend(["", "## 锚点列表", ""])
        for i, s in enumerate(sources, 1):
            lines.append(f"### {i}. {s.get('title', 'Untitled')} — {s.get('score', 0)}")
            lines.append(f"- **平台**: {s.get('platform', '')}")
            lines.append(f"- **链接**: {s.get('url', '')}")
            if s.get('snippet'):
                lines.append(f"\n> {s.get('snippet', '')[:300]}")
            lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python domain_orchestrator.py <subject> [domain_override]")
        sys.exit(1)

    subject = sys.argv[1]
    domain_override = sys.argv[2] if len(sys.argv) > 2 else None

    # 从 stdin 读取 sources
    try:
        sources_data = json.loads(sys.stdin.read() or '{}')
    except json.JSONDecodeError:
        sources_data = {}

    sources = sources_data.get('sources', [])

    orchestrator = DomainOrchestrator()
    result = orchestrator.render_report(subject, sources, domain_override=domain_override)
    print(result['markdown'])
    sys.stderr.write(f"\n[orchestrator] domain={result['domain']} template={result['template_used']}\n")


if __name__ == '__main__':
    main()