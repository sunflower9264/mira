# AGENTS.md

本文件约束 `backend/tests/`。当前目录有 39 个 `test_*.py` 文件，覆盖 API、services、Codex runtime sandbox、工作流执行、产物完整性和维护脚本。

## 测试基础

- `conftest.py`：每个 `client` fixture 使用临时 SQLite、data/runtime、Prompt seed 副本，创建管理员并注入 `MockRuntime`。
- `auth_helpers.py`：普通用户、登录和鉴权辅助。
- `runtime_mock.py`：测试专用 Codex runtime；支持文本、失败、延迟、结构化输出、planning、原生决策请求、fork 和 workspace merge 模拟。
- 默认测试不需要真实 Codex 登录、Docker 外部服务或网络。

## 覆盖分组

- API/权限：`test_auth.py`、`test_apps_settings.py`、`test_uploads.py`、`test_runs_crud.py`、`test_runs_sse.py`。
- Graph/执行：`test_execution_plan.py`、`test_run_executor.py`、`test_condition_node.py`、`test_workflow_lint.py`、`test_workflow_data_interface.py`。
- 恢复/历史：`test_run_recovery.py`、`test_run_waiting_resume.py`、`test_run_history_names.py`、`test_run_trace.py`。
- Runtime：`test_runtime_config.py`、`test_runtime_parsers.py`、`test_runtime_sandbox.py`、`test_runtime_skill_mounts.py`。
- Skills：`test_skill_lifecycle.py`、`test_skill_dependencies.py`。
- Artifacts/Office/文本：`test_artifact_integrity.py`、`test_output_text_integrity.py`、`test_office_documents.py`、`test_mira_office_sandbox.py`。
- 截图工具：`test_capture_screenshots.py`、`test_mira_browser.py`、`test_ensure_runtimes.py`。
- Agent 辅助流程：`test_nlcompile.py`、`test_prompt_assistant.py`、`test_prompt_templates.py`、`test_graph_layout.py`。

## 真实 Runtime 测试

- `test_real_ai_backend.py` 默认跳过；仅在 `MIRA_RUN_REAL_AI_BACKEND_TEST=1` 且提供隔离源 DB/凭据时启动真实 Codex 后端测试。
- `test_real_ai_deploy_decision.py` 默认跳过；仅在 `MIRA_RUN_REAL_AI_DEPLOY_TEST=1` 且明确提供部署地址/凭据时创建真实 Run，并在结束时取消。
- 普通 `pytest` 不应意外触发真实 Codex、部署写入或真实工作流。

## 编写规则

- 生产代码不得导入 `runtime_mock.py`；新测试优先复用 fixture/helper。
- 测试行为，不复制实现；不要通过削弱权限、脱敏、artifact 完整性或 sandbox 边界来让测试通过。
- Run 改动覆盖状态、Steps、持久化 events/SSE replay，以及受影响的 cancel/waiting/resume/recovery/rerun 路径。
- `run_only` 改动同时覆盖 owner/非 owner 的读取、克隆、运行、SSE/artifact 脱敏，以及非 owner Step Trace 的 403 边界。
- Model/migration/schema 改动覆盖字段读写和 API 序列化；当前开发不为已删除运行方案保留兼容行为。
- 临时文件使用 pytest `tmp_path`，不要读写 `backend/data/`、`runtime/workspaces/` 或部署库。

## 验证

- 默认全量：`cd backend && uv run pytest -q`。
- 范围明确时可运行相关测试文件，但交付中说明覆盖范围和未跑全量的原因。
- 真实 Runtime/部署测试只在用户明确授权且环境变量完整时运行；不能用普通测试结果冒充真实验证。
