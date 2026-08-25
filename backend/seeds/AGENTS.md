# AGENTS.md

本文件约束 `backend/seeds/`。这里保存启动时同步的内置 gallery 数据、封面素材和 Prompt Template 源文件。

## 目录内容

- `gallery.json`：内置 `system_gallery` 应用及 graph 定义。
- `assets/`：gallery 封面；seed 时导入为 Upload，并把 App cover 保存为 upload id。
- `prompts/*.md`：带 front matter 的 Prompt Templates，当前包括 condition、graph layout、NL compile plan/apply、NL compile 节点提示词整理、output repair/rendering、页面 Prompt Assistant 和 Codex status smoke。

## 同步语义

- `app.main:lifespan` 通过 `services/prompts.py:seed_prompt_templates()` 和 `services/apps.py:seed_gallery()` 同步 seed。
- `gallery.json` 生成只读 `system_gallery` 源应用；`gallery=true` 返回模板，普通 `market=true` 不包含源模板，编辑前必须克隆。
- Gallery cover 与附件只保存 Upload 引用；seed、graph 和 App cover 不写宿主机绝对路径。
- Prompt Template 的 `key`、`variables` 与 `services/prompts.py` 调用必须严格一致。
- Settings 保存 Prompt Template 会同步写回同名 seed 文件，因此数据库与 seed 不应长期分叉。
- supported models 由管理员随 Codex config 维护，不在 seed 中维护 provider 列表。
- 用户决策使用 Codex 原生 `requestUserInput`；prompt 只规定业务判断和输出，不定义自造工具或第二传输协议。
- 页面提示词创作使用 `prompt_assistant`，NL compile apply 后处理使用 `nlcompile_prompt_refiner`；不得重新合并成同时承担创作和压缩的单一模板。

## 修改规则

- Gallery graph 必须通过当前 graph validation、workflow lint、output contract 和 Tool allow-list 规则。
- 一个 workflow 最多一个 `user_input` 和一个 `output`；`output` 为 HTML-only 唯一终点。
- 不在 seed 中放真实凭证、token、用户私有数据、机器路径或 runtime 生成文件。
- 不保留已经删除的 provider、旧 runtime 选择或节点级提问配置。
- 修改 Prompt Template 时只使用 front matter 声明的变量；不要依赖未注册占位符。
- 开发阶段修改 seed 后，同步开发数据库和 `deploy` 数据库；不可用时在交付中说明。

## 推荐阅读顺序

1. 目标 seed 文件。
2. `app/services/prompts.py` 或 `app/services/apps.py` 的同步逻辑。
3. `app/services/graph_validation.py`、`workflow_lint.py` 和相关测试。

## 验证

- 至少运行 `git diff --check`。
- Prompt 改动运行相关 `test_prompt_templates.py`、`test_condition_node.py`、`test_nlcompile.py`、`test_prompt_assistant.py` 或 `test_run_executor.py`。
- Gallery 改动验证 seed 可重复同步、graph 可执行且 owner/market/gallery 行为不变。
