# AGENTS.md

本文件约束 `backend/seeds/`。

## Role

`seeds/` 保存初始化数据：内置 gallery 模板、prompt template 默认内容和 gallery 封面素材。

## Rules

- `gallery.json` 同步为 `system_gallery` 只读源应用；字段必须与 App schema、graph validation、uploads 和 clone 逻辑保持一致。
- Gallery 模板通过 `GET /api/apps?gallery=true` 返回，不混入普通 `market=true` 列表；用户编辑模板前必须克隆为自己的草稿。
- `assets/` 中的 gallery 封面素材由 seed 逻辑导入为 upload id；不要在 graph 或 app cover 中写本机绝对路径。
- supported models 由管理员在 Settings 中维护，不在 seed 中硬编码真实可用模型列表。
- `prompts/*.md` 是 Prompt Templates 的源码事实来源；后端启动/seed 同步会覆盖数据库同名模板，Settings 保存 Prompt Template 时必须同步写回同名 seed 文件。
- 修改 prompt seed 时必须确认变量名与 `app/services/prompts.py` 和调用方一致。
- 开发阶段修改任何 seed 后，必须同步开发数据库和 `deploy` 数据库；如果某个数据库不可用，必须在回复中说明未同步原因。
- 用户提问使用 Codex App Server 原生 `requestUserInput`，prompt seed 只描述业务判断规则，不定义工具名或传输协议。
- 不在 seed 中放真实凭证、私有 token、机器相关路径或用户私有数据。

## Verification

- 文档或 seed-only 改动运行 `git diff --check`。
- Prompt 行为改动优先运行 `cd backend && uv run pytest -q tests/test_prompt_templates.py tests/test_condition_node.py tests/test_nlcompile.py`。
