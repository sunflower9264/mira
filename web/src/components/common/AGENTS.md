# AGENTS.md

本文件约束 `web/src/components/common/`。

## Role

`common/` 存放跨页面复用的基础 UI 和小型通用业务组件：弹窗、菜单、导航、选择器、输入、发布按钮、App Tools 控件、封面编辑、工作流预检提示和 decision_request 决策面板。

## Rules

- 保持组件通用，不引入页面专属 store 状态；复杂流程由调用方或 store 负责。
- 通用 HTTP 调用只在已有模式明确需要时出现，例如上传、封面、发布或通用选择器；不要把页面业务流程塞进 common。
- `PillInputBar` 选中附件后立即调用 uploads API。图片在输入框上方显示缩略图，上传中用灰色蒙层和转圈，完成后露出原图；非图片仍用文件名 chip。决策多题切换必须传 `onAttachmentUpdate`，按附件 id 回写，不能只改当前正在显示的那一题。
- `DecisionPromptPanel` 只渲染后端返回的 context/groups/options 和前端本地选择态；`以上都不是` 的追加归后端，组件只处理标题摘要展示、多选互斥、补充文本/附件、题目切换和摘要态。
- `PromptTokenEditor` 只把系统工具、Skill 和 MCP 显示为标签；不要加入节点字段、JSON 路径或祖先结果引用 token，运行时由同一 RunAgent 会话与 workspace 保留上下文。
- `AppToolsInlineSelect` / `AppToolsSummary` 读取 Settings 中启用的 Tools，用 `disabled_tool_ids` 表达 App 级排除；不要把 Tools 转成节点级配置。
- 模型相关控件只使用 Settings 的 `supported_models`；不要从 Codex config、auth 或 App Server 状态推断模型。
- 弹窗、菜单、抽屉和按钮要保留 loading、disabled、确认/取消、遮罩关闭、键盘和焦点行为。
- 图标优先使用现有组件或 `lucide-react`；不要新增重复内联 SVG。
- 视觉保持现有 Tailwind 风格，避免引入新的全局样式或设计系统。

## Verification

- 运行 `cd web && npm run typecheck`。
- 修改弹窗、菜单、选择器或决策面板时，手动检查打开/关闭、提交/取消、错误态、窄屏和键盘交互。
