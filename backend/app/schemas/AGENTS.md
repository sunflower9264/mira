# AGENTS.md

本文件约束 `backend/app/schemas/`。

## Role

`schemas/` 定义 Pydantic 请求/响应模型，是后端 API 与前端 TypeScript 类型之间的主要 wire shape 契约。

## Rules

- 字段命名、可选性、literal 值和嵌套结构必须与 `web/src/types.ts`、`web/src/lib/api.ts` 保持一致。
- 不直接暴露 ORM model；响应通过 schema 和 serializer 控制输出、脱敏和兼容字段。
- 新增必填字段时考虑旧数据库、seed 数据、测试 fixture、前端创建路径和 market/run_only 脱敏响应。
- 校验规则优先放 schema 或 service；不要在多个 router 中复制校验。
- Decision/ask_user schema 要保持 NL compile、Prompt Assistant 和 run waiting 共用语义：context/groups/options/answers/tool_use_id 结构一致。
- Run/SSE、artifacts、trace、nlcompile、prompt assistant、settings 等契约变更必须同步前端类型和 API helper。

## Verification

- 契约改动运行相关 API pytest，并运行 `cd web && npm run typecheck`。
- 至少运行 `cd backend && uv run python -m compileall app scripts`。
