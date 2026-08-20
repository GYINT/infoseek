#!/usr/bin/env python3
"""Infoseek 测试聚合运行器（v1.0.1 PATCH / P1-1）

背景：tests/ 下测试文件为「脚本风格」（顶层直接执行 + sys.exit），
pytest 收集时会因顶层 SystemExit 崩溃（INTERNALERROR），
因此提供本聚合入口：逐个以子进程直跑，汇总 PASS/FAIL/SKIP。

用法：
    python tests/run_tests.py            # 标准回归（15 套件，不含深度测试）
    python tests/run_tests.py -v         # 详细输出
    python tests/run_tests.py <子串>     # 只跑文件名含子串的套件
    python tests/test_deep_v101.py       # 深度测试（边界/压力/模拟案例，单独运行）

退出码：全通过 → 0；存在失败/崩溃 → 1。
"""
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).parent
ROOT = TESTS_DIR.parent
PY = sys.executable


def main() -> int:
    verbose = '-v' in sys.argv
    filters = [a for a in sys.argv[1:] if not a.startswith('-')]

    files = sorted(TESTS_DIR.glob('test_*.py'))
    # 深度测试（边界/压力/模拟案例）默认跳过，按需单独运行
    files = [f for f in files if f.name != 'test_deep_v101.py']
    if filters:
        files = [f for f in files if any(k in f.name for k in filters)]

    results = []
    t0 = time.time()
    for f in files:
        t1 = time.time()
        proc = subprocess.run([PY, str(f)], cwd=str(ROOT),
                              capture_output=True, text=True,
                              timeout=600, encoding='utf-8', errors='replace')
        dt = time.time() - t1
        tail = proc.stdout.strip().splitlines()[-3:]
        tail = [l.strip() for l in tail if l.strip()]
        if proc.returncode == 0:
            status = 'PASS'
        elif 'Skip' in proc.stdout or 'SKIP' in proc.stdout:
            status = 'SKIP'
        else:
            status = 'FAIL'
        results.append((f.name, status, dt, tail))
        print(f"[{status:4s}] {f.name:38s} {dt:5.1f}s")
        if verbose or status != 'PASS':
            for l in tail:
                print(f"         {l[:90]}")
            if status == 'FAIL' and proc.stderr.strip():
                print(f"         ERR: {proc.stderr.strip().splitlines()[-1][:90]}")

    # 汇总
    passed = [r for r in results if r[1] == 'PASS']
    failed = [r for r in results if r[1] != 'PASS']
    print(f"\n=== 聚合结果: {len(passed)} PASS / {len(failed)} 非PASS "
          f"({time.time()-t0:.0f}s) ===")
    if failed:
        for name, status, dt, _ in failed:
            print(f"  [{status}] {name} ({dt:.1f}s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == '__main__':
    sys.exit(main())
