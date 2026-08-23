# AGENTS.md

本文件约束 `web/src/components/`。

## Role

`components/` 保存 Mira 前端可复用 UI 和业务组件，包括通用控件、编辑器、首页、预览/运行、设置和手机端组件。

## Structure

- `common/`：跨页面基础 UI、弹窗、菜单、选择器、输入和通用业务小组件。
- `editor/`：React Flow 画布、节点工具栏、自然语言输入条和节点视图。
- `home/`：首页应用卡片、模板、市场和最近运行相关组件。
- `preview/`：运行入口、进度、Console、Step 面板、历史回放、等待输入和结果展示。
- `settings/`：管理员 Settings UI。
- `mobile/`：手机端专用轻量组件；页面流程仍在 `pages/mobile/`。

## Rules

- 通用组件放 `common/`；页面或业务域专用组件放对应目录，不把所有逻辑塞进 shared UI。
- 组件不直接散落后端请求；优先调用 store、`lib/api.ts` 或调用方传入的 handler。
- Props 使用 `types.ts` 或明确局部类型；不要用宽泛 `any` 绕过契约。
- 复杂业务流程放 store/helper/page 容器，展示组件只承担 UI 和局部交互。
- `DecisionPromptPanel` 渲染后端返回的 decision_request context/groups/options；前端只处理标题摘要展示、选择、补充文本/附件、摘要态和多选互斥，不硬编码模型后续策略。
- 跨桌面和手机复用的基础能力放 `common/`；手机端专用 shell/sheet 放 `mobile/`。
- 视觉改动沿用现有 Tailwind、黑白灰和少量状态色，不引入新的设计系统。

## Verification

- 组件改动运行 `cd web && npm run typecheck`。
- 交互改动手动检查涉及页面、窄屏表现、loading/disabled/error 状态和键盘/焦点行为。
