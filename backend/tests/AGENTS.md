# AGENTS.md

本文件约束 `backend/tests/`。

## Role

`tests/` 是后端 pytest 测试目录，覆盖鉴权、Apps/Settings、Uploads、Runs/SSE、runtime config/sandbox/parser、Prompt Templates、NL compile、Prompt Assistant、workflow lint、Trace 和恢复行为。

## Rules

- `runtime_mock.py` 只用于测试，不得被生产代码导入。
- 新测试优先复用 `conftest.py` fixture 和 `auth_helpers.py`，避免重复初始化数据库、用户、client 或 MockRuntime。
- 行为改动应加最小覆盖测试；不要为了通过测试弱化权限隔离、脱敏或 runtime sandbox 边界。
- 默认测试不要依赖本机真实 Claude/Codex 登录状态、真实 MCP 服务、真实网络或 runtime 生成目录；`test_real_ai_deploy_ask_user.py` 是显式 opt-in 的真实部署回归例外，只在 `MIRA_RUN_REAL_AI_DEPLOY_TEST=1` 时运行。
- 涉及 run 的测试要覆盖状态、steps、logs/events、SSE replay、cancel/waiting/resume/recovery 中受影响的路径。
- 涉及市场或 run_only 的测试要覆盖 owner 与非 owner 的可见性、克隆、运行、脱敏响应。
- Prompt seed、NL compile、Prompt Assistant 或 ask_user 改动要覆盖结构化返回、waiting/resume、失败路径和变量名。
- Migration/model/schema 改动需要测试新增字段的读写路径和兼容旧数据/seed 的路径。

## Verification

- 后端行为改动优先运行 `cd backend && uv run pytest -q`。
- 范围较小时可运行明确相关测试文件，并在回复中说明覆盖范围和未运行全量测试的原因。
