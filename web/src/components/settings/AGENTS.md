# AGENTS.md

本文件约束 `web/src/components/settings/`。

## Role

`settings/` 是管理员设置 UI，维护 Codex 配置、支持模型、MCP、Skills、Instructions、Prompt Templates 和 Codex 初始化引导。

## Rules

- Settings 入口和写操作只面向管理员；不要绕过 `selectIsAdmin`、路由 gate 或后端 admin 权限。
- Codex config 正文、Codex auth、supported models、MCP、Skills、Instructions 和 Prompt Templates 都是全局数据，不是单个 App 的私有配置。
- 支持模型列表由管理员手动维护；前端不要从 Codex config、auth 或 App Server 状态自动发现/补入模型。
- MCP 和 Skills 在 Settings 中分开管理，但对 App 运行入口合并为统一 Tools 库存；`planning_enabled` 控制是否进入 NL compile、Prompt Assistant 和运行期 Plan 的 planning/read-only 阶段。
- Skill zip 列表只展示摘要和元数据；`SKILL.md` 正文通过按需预览读取，不塞进列表状态。
- Prompt Templates 通过 Settings 保存时同时更新数据库内容和同名 `backend/seeds/prompts/` seed 文件。
- 大文本、列表、上传和保存流程要保留 loading、error、dirty、disabled 状态，避免用户误以为已保存。
- 大文本区域和列表滚动样式沿用当前紧凑深色/浅色 UI，不引入新编辑器依赖。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查管理员/普通用户可见性、Codex 初始化、保存/刷新、MCP/Skill 启用和规划可用切换、Skill 预览、Prompt Templates 编辑和错误提示。
