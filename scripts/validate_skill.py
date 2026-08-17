#!/usr/bin/env python3
"""Infoseek SKILL.md 验证脚本（v1.5.0 契约，适配 v3.x 编号章节）"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FIELDS = ["name", "version", "description", "license"]

# 必填章节：v1.5 标准名 → v3.x 编号标题实际名
SECTION_MAP = {
    "概念": ["这是什么", "概念"],
    "快速上手": ["快速上手"],
    "工作流": ["工作流"],
    "核心算法": ["工作机制要点", "核心算法"],
    "输出格式": ["核心能力", "输出格式"],
    "触发词": ["触发词"],
}


def main() -> int:
    print("=" * 60)
    print("Infoseek SKILL.md 验证")
    print("=" * 60)
    errors = 0
    warnings = 0

    skill_md = ROOT / "SKILL.md"
    if not skill_md.exists():
        print(f"❌ SKILL.md 不存在: {skill_md}")
        return 1
    text = skill_md.read_text(encoding="utf-8")
    print(f"✅ SKILL.md 存在 ({len(text)} bytes)")

    # frontmatter
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        print("❌ frontmatter 缺失或未闭合")
        return 1
    fm_text = m.group(1)
    print("✅ frontmatter 闭合")
    for field in REQUIRED_FIELDS:
        if re.search(rf"^{field}:", fm_text, re.MULTILINE):
            print(f"✅ frontmatter 含 {field}:")
        else:
            print(f"❌ frontmatter 缺 {field}")
            errors += 1

    # description 三段式
    desc = re.search(r"^description:\s*(.+)$", fm_text, re.MULTILINE)
    if desc and "不适用" in desc.group(1):
        print("✅ description 含'不适用'边界")
    else:
        print("⚠️  description 建议含'不适用'边界")
        warnings += 1

    # 必填章节（语义匹配 v3.x 编号标题）
    for sec, alts in SECTION_MAP.items():
        found = any(re.search(rf"^## (\d+\.\s*)?{re.escape(a)}", text, re.MULTILINE) for a in alts)
        if found:
            print(f"✅ 必填章节: {sec} 内容存在")
        else:
            print(f"❌ 必填章节缺失: {sec}")
            errors += 1

    # manifest 双绑
    manifest = ROOT / "manifest.yaml"
    if manifest.exists():
        mf = manifest.read_text(encoding="utf-8")
        for field in REQUIRED_FIELDS:
            fm_val = re.search(rf"^{field}:\s*(.+)$", fm_text, re.MULTILINE)
            mf_val = re.search(rf"^{field}:\s*(.+)$", mf, re.MULTILINE)
            if fm_val and mf_val and fm_val.group(1).strip() == mf_val.group(1).strip():
                print(f"✅ manifest 与 frontmatter {field} 一致")
            else:
                print(f"❌ manifest 与 frontmatter {field} 不一致")
                errors += 1
    else:
        print("⚠️  manifest.yaml 缺失")
        warnings += 1

    # 嵌套污染
    if (ROOT / "infoseek").exists():
        print("❌ 嵌套污染: infoseek/infoseek/")
        errors += 1
    else:
        print("✅ 无 2 层嵌套污染")

    print("=" * 60)
    print(f"汇总: {errors} errors, {warnings} warnings")
    if errors:
        print("❌ Validation FAILED.")
        return 1
    print("✅ Validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
