"""通用远程工具基座（v1.0.1 G8 补全）

Dify 插件以「REST 桥代理」模式运行：所有工具经 infoseek 远程 MCP 的
POST /tools/<tool_name> 调用（Bearer 鉴权），工具实现仅做参数透传 + 结果渲染。

- `call_remote_tool`：纯标准库 HTTP 调用（可脱离 dify_plugin SDK 本地测试）
- `BaseRemoteTool`：平台工具类的公共逻辑（与 dify_plugin 解耦，便于复用与测试）
"""
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict


def call_remote_tool(endpoint: str, token: str, tool_name: str,
                     params: Dict[str, Any]) -> Dict[str, Any]:
    """调用 infoseek REST 桥，返回渲染结果（纯函数，无 SDK 依赖）。

    返回: {'markdown': <渲染文本>, 'raw': <原始结果>} 或 {'error': <错误信息>}
    """
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/tools/{tool_name}",
        data=json.dumps(params).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {token}'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'error': f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}"}
    except Exception as e:
        return {'error': f"调用失败: {e}"}
    try:
        text = data['content'][0]['text']
        result = json.loads(text)
        markdown = result.get('report') or result.get('markdown') or text
        return {'markdown': markdown, 'raw': result}
    except Exception:
        return {'markdown': json.dumps(data, ensure_ascii=False, indent=2),
                'raw': data}


class BaseRemoteTool:
    """远程工具公共基类（Dify Tool 与 _invoke 由平台 SDK 提供，此处定义契约）"""

    TOOL_NAME: str = None  # 子类指定 REST 桥工具名

    def _endpoint(self) -> str:
        return (self.runtime.credentials.get('api_endpoint')
                or os.environ.get('INFOSEEK_ENDPOINT', 'http://127.0.0.1:8765'))

    def _token(self) -> str:
        return (self.runtime.credentials.get('api_token')
                or os.environ.get('INFOSEEK_AUTH_TOKEN', ''))

    def _clean_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}
