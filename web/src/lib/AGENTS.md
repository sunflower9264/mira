# AGENTS.md

本文件约束 `web/src/lib/`。

## Role

`lib/` 存放前端 HTTP API 客户端、SSE 客户端、auth token 持久化、Codex model helper、mobile route helper、workflow lint helper 和通用工具。

## Rules

- 所有 HTTP 请求通过 `api.ts`，保持相对路径 `/api/...`；错误处理沿用后端 `{detail}` 语义。
- Run SSE 只通过 `ws.ts`；修改事件解析时同步 `types.ts`、`useRunStore` 和后端 run event schema。
- Auth token/user 读写集中在 `auth.ts`，不要在组件里手写 localStorage/sessionStorage key。
- Mobile 路由转换在 `mobile.ts`，使用 viewport media query 语义，不用 UA 判断。
- API 函数签名必须与 `types.ts` 和后端 schema 对齐；新增接口先确认 owner/read-only/admin 权限语义。
- 模板列表使用 `GET /api/apps?gallery=true`；市场使用 `GET /api/apps?market=true`；最近运行使用 `GET /api/apps/recent-runs`。
- 克隆入口必须依据后端返回的 `can_clone`：模板导入走 gallery clone，普通市场克隆走 app clone。
- NL compile API 支持 active/resume/refine/apply/cancel；客户端 abort 不能替代后端 cancel。
- Prompt Assistant API 当前支持 generate/resume/cancel；generate 不传模型或推理强度，由后端使用 Codex 配置默认值。前端尚未封装 active 恢复接口，新增恢复时必须同步 store 和 StepTab。
- Run artifacts 只通过 `listRunArtifacts` 获取；调用方消费后端给出的 `integrity`、`mime` 和 `download_url`，不推导 runtime 路径，也不从 output HTML 扫描文件。
- Workflow lint helper 只用 error 阻断运行/发布，warning/info 作为提示；不要替代后端硬校验。
- 不在 lib 中引入 UI 依赖；UI 文案和状态展示交给组件/store。

## Verification

- 运行 `cd web && npm run typecheck`。
- API/SSE 契约变更需要同步后端测试或说明未运行原因；路由 helper 改动要手动检查桌面/手机跳转。
