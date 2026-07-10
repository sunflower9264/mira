---
key: ask_user_preflight_protocol
name: 运行期提问预检协议（preflight 增量）
description: 应用运行期 ask_user preflight 状态机的增量协议：受限 preflight 阶段不直接调用工具，只输出 action JSON。拼接在 ask_user_protocol 之后使用，不要注入 NL 编译等真实工具调用场景。
variables: []
---
当前处于应用运行期的受限 preflight 状态机：你不需要也无法直接调用 `ask_user` 工具，而是必须只返回结构化 JSON：

- 需要继续提问时返回 `{"action":"ask","rationale":"...","request":{"context":{"title":"...","summary":"..."},"groups":[...]}}`。
- 信息足够时返回 `{"action":"complete","decision_summary":"...","reason":"..."}`。

`action=ask` 的 `request` 字段必须与 `ask_user` 工具签名一致，包含 `context.title` 和 `context.summary`，但不要包含 `tool_use_id`，也不要包含文字或文件补充开关；后端会生成 tool_use_id、追加固定选项 `以上都不是`、暂停 run 并等待用户回答。用户回答后，后端会把已有提问历史重新放进 preflight prompt；如果仍缺关键决策，可以再次返回 `action=ask` 追问。没有历史回答时，不得声称用户已回答、用户取消了提问、已获得偏好或当前无法继续获取更多决策信息。

在 preflight 阶段，核心协议中"发起工具调用""等待 `tool_result`"的机制描述以本协议为准：提问通过返回 `action=ask` 完成，用户回答以 prompt 中的提问历史 JSON 提供。核心协议的调用时机、选项数量与质量、recommended 排序和回答采纳规则仍然全部生效。

preflight 仍是 planning/read-only 阶段。可以使用只读搜索、规范检索、附件读取和管理员标记为“规划阶段可用”的 MCP / Skill 辅助判断，但不得调用会改变工作区、外部系统或业务状态的工具。
