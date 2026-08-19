"""research 工具（infoseek v1.0.0）
经 REST 桥 POST /tools/research_v3 调用（Bearer token 鉴权）。
"""
from collections.abc import Generator
from typing import Any
import json
import os
import urllib.error
import urllib.request

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage


class ResearchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        endpoint = (self.runtime.credentials.get('api_endpoint')
                    or os.environ.get('INFOSEEK_ENDPOINT', 'http://127.0.0.1:8765'))
        token = (self.runtime.credentials.get('api_token')
                 or os.environ.get('INFOSEEK_AUTH_TOKEN', ''))
        args = {k: v for k, v in tool_parameters.items()
                if v is not None and k not in ('api_endpoint', 'api_token')}
        req = urllib.request.Request(
            f"{endpoint.rstrip('/')}/tools/research_v3",
            data=json.dumps(args).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {token}'},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            yield self.create_text_message(
                f"调用失败 HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
            return
        except Exception as e:
            yield self.create_text_message(f"调用失败: {e}")
            return
        # REST 桥返回 {content: [{type: text, text: <JSON>}]}
        try:
            text = data['content'][0]['text']
            result = json.loads(text)
            markdown = result.get('report') or result.get('markdown') or text
        except Exception:
            markdown = json.dumps(data, ensure_ascii=False, indent=2)
        yield self.create_text_message(markdown)
