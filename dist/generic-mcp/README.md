# generic-mcp MCP 配置（infoseek v1.0.0）

- `infoseek-search`：本地 stdio 形态（路径为可移植占位 `${\{INFOSEEK_ROOT}}`/`${HOME}`，安装时展开为实际目录）。
- `infoseek-remote`：远程 SSE 形态（地址来自构建时 `INFOSEEK_PUBLIC_URL`，默认 `http://127.0.0.1:8765`；需先
  `python scripts/infoseek_host.py start`，并把 token 写入环境变量 `INFOSEEK_AUTH_TOKEN`）。
