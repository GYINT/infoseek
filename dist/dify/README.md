# Dify 插件包（infoseek v1.0.0）

对照 docs.dify.ai Manifest 规范（2026-08 核验）生成；Python 实现按 Dify SDK
契约（`Tool._invoke` + `create_text_message`）编写，调用 infoseek **REST 桥**
`POST /tools/research_v3`（需先 `python scripts/infoseek_host.py start`）。

**已补齐（v1.0.0）：** `icon.svg` / `icon.png` / `PRIVACY.md`（上架必备资产）；
其余 12 个规范工具可经 REST 桥调用（`tools/research.py` 为同构模板）。

**发布前待补项（如实声明）：**
1. 插件本地调试需安装 `dify_plugin` SDK（`pip install dify_plugin`）；
2. 若托管地址非默认，设置 `INFOSEEK_PUBLIC_URL` 后重新构建；
3. 按 Dify 平台导入校验结果微调。
