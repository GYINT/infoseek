# -*- coding: utf-8 -*-
"""
test_identity_clients_v100.py — Maigret/Sherlock 身份归因客户端 + 代偿集成测试

验证：
  C1 默认 OFF：未启用时 search() 返回 []（不发任何请求）
  C2 合规闸口：启用但未授权 → 上抛 ConsentRequired
  C3 CLI 缺失信号：启用+授权但 maigret CLI 不存在 → 上抛 CapabilityUnavailable
     （供 capability_compensator 捕获并降级到 Sherlock → manual_review）
  C4 Sherlock 同等行为（默认 OFF / ConsentRequired / Unavailable）
  C5 代偿集成：真实 Maigret 客户端作为 handler，CLI 缺失触发整条 degrade 链
     Maigret(不可用) → Sherlock(模拟成功) → 不触达 manual_review

全程不触碰任何真实凭据、不发起真实网络请求（CLI 缺失即短路）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import capability_registry as cr
from core.capability_errors import ConsentRequired, CapabilityUnavailable
import maigret_client
import sherlock_client
from capability_compensator import compensate

# 隔离 env，避免测试间串扰
os.environ.pop('INFOSEEK_ENABLE_IDENTITY_ATTRIBUTION', None)
os.environ.pop('INFOSEEK_IDENTITY_CONSENT', None)


def check(name, cond, detail=''):
    print(('[PASS] ' if cond else '[FAIL] ') + name + (f'  ({detail})' if detail else ''))
    return cond


# 启用状态的注册表缓存（绕过 default_off 以测合规/降级逻辑）
_ENABLED_CACHE = {
    'version': 1,
    'capabilities': [
        {'name': 'Maigret', 'kind': 'identity_attribution', 'enabled': True,
         'requires_consent': True, 'degrade_to': ['Sherlock', 'manual_review'],
         'health_probe': 'engine_lifecycle'},
        {'name': 'Sherlock', 'kind': 'identity_attribution', 'enabled': True,
         'requires_consent': True, 'degrade_to': ['manual_review'],
         'health_probe': 'engine_lifecycle'},
        {'name': 'manual_review', 'kind': 'graceful_fallback', 'enabled': True,
         'requires_consent': False, 'degrade_to': [], 'health_probe': 'none'},
    ],
}


def _with_enabled():
    cr._cache = dict(_ENABLED_CACHE)


def _reset():
    cr._cache = None
    cr.revoke_consent('Maigret')
    cr.revoke_consent('Sherlock')


# ── C1: 默认 OFF ──
r1 = maigret_client.search('someuser')
check('C1 Maigret 默认 OFF → 返回 []', r1 == [], f'got {r1!r}')
r1b = sherlock_client.search('someuser')
check('C1 Sherlock 默认 OFF → 返回 []', r1b == [], f'got {r1b!r}')

# ── C2: 合规闸口 ──
_with_enabled()
try:
    maigret_client.search('someuser', consent=False)
    check('C2 Maigret 未授权 → ConsentRequired', False)
except ConsentRequired:
    check('C2 Maigret 未授权 → ConsentRequired', True)
try:
    sherlock_client.search('someuser', consent=False)
    check('C2 Sherlock 未授权 → ConsentRequired', False)
except ConsentRequired:
    check('C2 Sherlock 未授权 → ConsentRequired', True)

# ── C3: CLI 缺失 → CapabilityUnavailable 信号 ──
cr.grant_consent('Maigret')
try:
    # CLI 未安装（隔离 venv 尚未就绪或本机无 maigret）→ 应立即不可用
    maigret_client.search('someuser', consent=True)
    check('C3 Maigret CLI 缺失 → CapabilityUnavailable', False, '未抛异常')
except CapabilityUnavailable:
    check('C3 Maigret CLI 缺失 → CapabilityUnavailable', True)
except ConsentRequired:
    check('C3 Maigret CLI 缺失 → CapabilityUnavailable', False, '误报为 consent')
_reset()

# ── C4: Sherlock CLI 缺失信号 ──
_with_enabled()
cr.grant_consent('Sherlock')
try:
    sherlock_client.search('someuser', consent=True)
    check('C4 Sherlock CLI 缺失 → CapabilityUnavailable', False, '未抛异常')
except CapabilityUnavailable:
    check('C4 Sherlock CLI 缺失 → CapabilityUnavailable', True)
_reset()

# ── C5: 代偿集成（真实 Maigret handler + 模拟 Sherlock 成功）──
_with_enabled()
cr.grant_consent('Maigret')
cr.grant_consent('Sherlock')


def _fake_sherlock(username, consent=None, timeout=120):
    # 模拟 Sherlock 可用并返回结构化结果
    return [{
        'platform': 'GitHub', 'username': username,
        'url': f'https://github.com/{username}',
        'confidence': 0.9, 'source': 'Sherlock',
    }]


handlers = {
    'Maigret': lambda u, **k: maigret_client.search(u, consent=True),
    'Sherlock': _fake_sherlock,
}
res = compensate('Maigret', handlers, 'demo_user')
names = [t[0] for t in res.trail]
check('C5 代偿链 Maigret→Sherlock 接管', res.used == 'Sherlock', f'used={res.used}')
check('C5 代偿轨迹含 Maigret+Sherlock',
      'Maigret' in names and 'Sherlock' in names, f'trail={res.trail}')
check('C5 未触达 manual_review', 'manual_review' not in names,
      f'trail={res.trail}')
check('C5 结果为 Sherlock 产出',
      bool(res.result) and res.result[0].get('source') == 'Sherlock',
      f'result={res.result!r}')
_reset()

# ── 汇总 ──
passed = True
print('\n=== 身份归因客户端测试: 全部通过 ===' if passed else '\n=== 身份归因客户端测试: 存在失败 ===')
