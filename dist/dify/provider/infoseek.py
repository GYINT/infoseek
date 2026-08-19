"""Dify Tool Provider（infoseek v1.0.0）
将 infoseek 远程 MCP（REST 桥 /tools/<name>）封装为 Dify 工具提供方。
"""
from dify_plugin import ToolProvider


class InfoseekProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict) -> None:
        if not credentials.get('api_endpoint'):
            raise ValueError(
                "缺少 api_endpoint（infoseek 远程托管地址，如 http://127.0.0.1:8765）")
