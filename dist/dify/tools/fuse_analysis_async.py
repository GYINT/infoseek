"""fuse_analysis_async 工具（v1.0.1 G8 自动生成）：经 infoseek REST 桥 POST /tools/fuse_analysis_async 调用。"""
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _base import BaseRemoteTool, call_remote_tool


class FuseAnalysisAsyncTool(Tool, BaseRemoteTool):
    TOOL_NAME = 'fuse_analysis_async'

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        result = call_remote_tool(
            self._endpoint(), self._token(), self.TOOL_NAME,
            self._clean_params(tool_parameters))
        if 'error' in result:/n            yield self.create_text_message(result['error'])
            return
        yield self.create_text_message(result['markdown'])
