#!/usr/bin/env python3
"""同步 SKILL.md frontmatter → manifest.yaml（v1.5.0 双绑契约）"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        raise ValueError("SKILL.md 缺少 YAML frontmatter")
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def sync():
    skill_md = ROOT / "SKILL.md"
    manifest = ROOT / "manifest.yaml"
    fm = extract_frontmatter(skill_md)
    required = ["name", "version", "description", "license"]
    missing = [k for k in required if k not in fm]
    if missing:
        print(f"❌ frontmatter 缺少必填字段: {missing}")
        return 1

    content = "# Auto-generated from SKILL.md frontmatter\n\n"
    for k in ["name", "version", "description", "license"]:
        content += f"{k}: {fm[k]}\n"

    manifest.write_text(content, encoding="utf-8")
    print(f"✅ 已更新 manifest.yaml（{len(content)} bytes）")
    return 0


if __name__ == "__main__":
    sys.exit(sync())
