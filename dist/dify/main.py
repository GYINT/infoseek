"""Dify 插件入口（infoseek v1.0.0）
SDK 依 manifest/provider/tools 元数据自动发现 Provider 与 Tool 类。
"""
from dify_plugin import Plugin

plugin = Plugin()

if __name__ == '__main__':
    plugin.run()
