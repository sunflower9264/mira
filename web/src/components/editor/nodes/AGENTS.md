# AGENTS.md

本文件约束 `web/src/components/editor/nodes/`。

## Role

`nodes/` 存放 React Flow 节点视图：`user_input`、`asset`、`generate`、`condition`、`output` 和通用 `NodeCard`。

## Rules

- 节点组件只展示节点自身数据、handle 和局部状态；跨节点关系、保存、选择和边变更由 Canvas/store 维护。
- 节点字段必须与 `web/src/types.ts` 对齐：素材节点使用 `content` / `urls[]` / `uploads[]` / `upload`，LLM 节点使用 prompt、model、reasoning_effort 等字段。
- `generate` 可展示/编辑 `output_contract` 语义；`output` 保持 HTML 最终展示节点，不提供多类型输出切换。
- condition 分支 handle 和 label 必须与 branch key、edge `branch_key` 保持一致；不要把系统保留 default key 当成用户自定义分支。
- `output` 节点不能提供 source handle；它只能作为终点。
- 新增或修改节点字段时，同步 `types.ts`、store、后端 graph validation / node handler / schema 和相关测试。
- 保持节点尺寸、handle 位置、hover/selection 状态稳定，避免拖拽或流式状态导致布局跳动。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查节点渲染、连接点、分支 label、选择状态、拖拽、删除和 Step 面板编辑。
