# AGENTS.md

本文件约束 `web/src/components/common/`。

## Role

`common/` 存放跨页面复用的基础 UI 和小型通用业务组件：弹窗、菜单、导航、选择器、输入、发布按钮、App Agent/Tools 控件、封面编辑、工作流预检提示和 ask_user 决策面板。

## Rules

- 保持组件通用，不引入页面专属 store 状态；复杂流程由调用方或 store 负责。
- 通用 HTTP 调用只在已有模式明确需要时出现，例如上传、封面、发布或通用选择器；不要把页面业务流程塞进 common。
- `DecisionPromptPanel` 只渲染后端返回的 context/groups/options 和前端本地选择态；`以上都不是` 的追加归后端，组件只处理标题摘要展示、多选互斥、补充文本/附件、题目切换和摘要态。
- `PromptTokenEditor` 只把内部工具名和结构化字段显示为中文标签；存储、复制和提交必须保留原始标识符，标签在编辑时按整体删除。
- `AppToolsInlineSelect` / `AppToolsSummary` 读取 Settings 中启用的 Tools，用 `disabled_tool_ids` 表达 App 级排除；不要把 Tools 转成节点级配置。
- `AppAgentSelect` 和模型相关控件只使用 Settings 中启用 Agent 及其 `supported_models`；不要从 config/auth/CLI 状态推断模型。
- 弹窗、菜单、抽屉和按钮要保留 loading、disabled、确认/取消、遮罩关闭、键盘和焦点行为。
- 图标优先使用现有组件或 `lucide-react`；不要新增重复内联 SVG。
- 视觉保持现有 Tailwind 风格，避免引入新的全局样式或设计系统。

## Verification

- 运行 `cd web && npm run typecheck`。
- 修改弹窗、菜单、选择器或决策面板时，手动检查打开/关闭、提交/取消、错误态、窄屏和键盘交互。
