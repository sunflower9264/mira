# Mira Prompt Effectiveness Repair Design

## 目标

修复当前 seed prompt 与实际执行契约不一致所造成的效果问题，使 NL compile、Prompt Assistant、condition、output contract repair 和 output HTML 在真实 Codex 运行中更稳定、少做无效提问、保留已有语义，并能通过可重复的效果回归验证。

本次只处理提示词效果和可靠性，不处理安全边界。真实 AI 硬验收使用当前已启用的 Codex `gpt-5.5`；不修改 Claude 配置。

## 设计边界

- 不改变现有 HTTP API、前端 wire shape、数据库 schema 或运行历史格式。
- Prompt seed 仍以 `backend/seeds/prompts/*.md` 为源码事实来源。
- 内部 structured output schema、prompt 构造器和语义校验必须与 seed 同步，不能只改文案。
- 不建设通用 Prompt 版本管理、线上评分或 A/B 平台。
- 真实 AI 测试保持 opt-in，默认 pytest 不依赖真实网络和凭据。

## NL Compile Plan

### 提问策略

`nlcompile_plan` 不再因“新建 workflow”或“结构调整较大”本身触发 `ask_user`。只有同时满足以下条件时才提问：

1. 缺少的是业务目标、运行时输入、输出形式或关键分支等真实决策。
2. 不同答案会产生明显不同的用户可见结果或 graph 拓扑。
3. 无法从用户指令、当前 graph 和会话历史推断。

节点 ID、标题、数量、布局和普通实现细节由模型自行决定。可在应用运行时采集的信息应设计进 `user_input`，不能在编译阶段向应用创建者追问。

### Plan 字段

Seed 为全部 schema 字段提供明确写作规则：

- `goal_summary`：对象、动作和最终交付物。
- `assumptions`：只保留未确认且影响结果的假设，可以为空。
- `data_flow`：使用用户可见节点名称描述实际传递内容；纯元数据修改可以为空。
- `implementation_steps`：按顺序描述新增、更新、删除和连接哪些节点。
- `graph_changes`：使用“新增/更新/删除/连接 + 节点名称”，至少一项。
- `expected_inputs`：说明运行时输入或沿用的现有素材；至少一项。
- `expected_outputs`：说明最终内容、格式、artifact 和出口节点；至少一项。
- `acceptance_criteria`：能从 apply 后 graph 或实际运行逐项核对；至少一项。

服务层在解析后要求 `implementation_steps`、`graph_changes`、`expected_inputs`、`expected_outputs` 和 `acceptance_criteria` 非空。缺失时进入现有 structured repair，不接受格式正确但内容空泛的 plan。

## NL Compile Patch

### 非空结果

- `NL_COMPILE_PATCH_OUTPUT_SCHEMA.patches` 增加 `minItems: 1`。
- `extract_patches()` 显式拒绝空数组，使其进入现有最多三次修复流程。
- Seed 删除“信息不足时输出空 patch”，改为确认方案必须生成至少一个 patch；仍无法实施时让本次 apply 失败，不能返回伪 completed。

### 节点与连线协议

`PATCH_PROTOCOL` 增加紧凑而完整的内部 JSON 契约，首次生成和修复重试共用：

- `user_input`：`id/type/title/input_schema.label/input_schema.kind/input_schema.required`。
- `generate`：`id/type/title/prompt`，仅在方案需要时增加 `model`、`reasoning_effort`、`output_contract`。
- `output`：`id/type/title/prompt/source_node_id`，且必须有与 `source_node_id` 一致的入边。
- `condition`：`id/type/title/mode/prompt/branches`；binary 固定 `true/false`，cases 不声明保留 key `__default__`。
- `asset`：按 `text/url/file/drawing` 使用 `content/urls/uploads/upload`，不得编造 upload 引用。
- 普通边示例使用 `edge_source_handle: null`；condition 边示例使用真实 branch key。
- Patch 顺序为先新增节点，再更新节点，再新增边；ID 必须唯一。

### 布局职责

Patch prompt 只处理业务拓扑：禁止传递冗余边，并行结果只连接真正消费它的节点，只有业务上确实需要合并数据时才新增汇总节点。删除视觉交叉、直线推进和移动坐标等规则，并明确不得为了画布美观改变业务拓扑。

新节点由服务层先赋予确定性 fallback `position`，再调用现有 AI layout。即使 layout 失败，返回 graph 仍包含合法坐标。

## Prompt Assistant

- 当前目标节点 prompt 完整传入，不再静默截断；若完整请求超过 200 KiB，返回明确的 400，而不是丢失尾部约束。
- 相邻节点 prompt 使用 head + tail 摘要，并明确标注省略字符数。
- generate 节点上下文增加当前 `output_contract`。
- 编辑和格式清理时，未涉及段落逐字保留；变量、示例、代码块、字段、边界条件、输出格式和成功标准不得遗漏或重写。
- “长度与任务复杂度匹配”只适用于新建 prompt 或用户明确要求重写。
- 只有请求改变输出形态或下游机器契约确实需要时，才调整 `output_contract`。

## Condition Choice

`condition_choice` 从 `$valid_keys` 改为 `$branch_options_json`，调用方传入结构化的 `[{"key":"...","label":"..."}]`：

- 模型根据 label 的业务含义判断，只输出对应 exact key。
- label 缺失时回退为 key。
- cases 模式存在 `__default__` 出边时，追加“其它：以上分支均不匹配”的默认项。
- 匹配不到任何显式分支时允许选择 `__default__`，避免被迫误选。

## Output Contract Repair

Seed 增加 `$task_context`，调用方传入首次执行使用的完整 task prompt。修复规则改为：

- 只做满足契约所需的最小机械修复。
- 逐值保留事实、数字、名称、URL、文件路径、列表顺序、语言和 HTML 内容。
- 允许去除 fence/解释、补 wrapper、修正 JSON 语法，或在映射无歧义时调整字段结构。
- 禁止总结、翻译、美化内容或编造必填字段；原始信息不足时允许修复失败。

## Output HTML

在现有 HTML wrapper 规则上补充效果基线：

- 除非节点明确要求摘要，完整呈现上游相关名称、数字、状态、说明、链接和文件。
- 按内容选择 article、分节、列表、表格或 `pre/code`，不把所有内容机械做成卡片。
- 未指定视觉风格时使用克制、专业、响应式默认样式：system font、`body` 零 margin、合理 padding、`box-sizing`、长词换行和窄屏表格处理。
- 避免固定宽画布、巨型标题、伪交互控件和无内容空白。

## 测试与真实 AI 验收

### 确定性测试

- 空 `patches` 触发修复并最终失败，不能 completed。
- 空 graph 使用真实 `node_json/patch_json/edge_*` 创建合法节点与 condition 边。
- Plan 关键字段为空时进入 repair。
- 目标 prompt 的头、中、尾标记全部进入 Prompt Assistant 模型 prompt，当前 contract 可见。
- Condition prompt 同时包含 opaque key、中文 label 和实际默认分支。
- Repair prompt 包含 task context 和语义保真规则。
- Layout 失败时新增节点仍有合法 fallback position。
- Seed frontmatter 变量与实际占位符、调用方变量保持一致。

### 真实 Codex Eval

修正 `test_real_ai_backend.py` 的 Docker 回调：uvicorn 绑定 `0.0.0.0`，容器回调使用 `host.docker.internal:<port>`，测试客户端继续访问 `127.0.0.1`。测试仍复制 source DB 并使用临时 data/runtime，不修改开发数据库。

以下场景各运行三次：

1. 明确的小范围标题修改：直接 planned，不 ask，apply 只改目标字段。
2. 空 graph 的完整摘要应用：直接 planned，plan 字段具体，apply 生成非空合法 graph。
3. 模糊的推荐应用：进入 waiting，问题聚焦真实业务决策；resume 后方案吸收答案。
4. 长 prompt 最小编辑：头、中、尾约束全部保留，不改变现有 contract。
5. Opaque condition key + 中文 label：明确输入选择正确 key，未匹配输入选择 default。
6. Repair 语义保真：固定事实全部保留，unknown 不被补写，不增加新事实。

硬门槛：明确任务不必要提问率 0%，模糊任务提问率 100%，空 graph 创建成功率 100%，非空 patch 率 100%，长 prompt 约束保留率 100%，condition 准确率 100%，repair 事实保留率 100%。

## 文档与 Seed 同步

- 更新 `backend/README.md` 中相关 Prompt Template 行为说明。
- 修改 seed 后同步开发数据库和可用的 `deploy` 数据库；无法同步的数据库必须在最终结果中明确说明。
- 不修改现有无关工作树变更。
