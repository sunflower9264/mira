# AGENTS.md

本文件约束 `web/src/components/preview/`。

## Role

`preview/` 负责桌面 Preview/App View 共用的运行体验：启动页、SSE 进度、Console、Step 面板、等待输入、历史回放、Trace、重新执行、失败修复、文件产物和 HTML 输出。

## Rules

- Preview 和 App View 共用 `useRunStore`；修改运行逻辑必须同时考虑桌面编辑器 Preview、只读 App View 和 Mobile Run。
- 运行态使用 run graph 快照，不用当前编辑器 graph 伪造历史步骤；Step 编辑仍作用于当前 App graph。
- `pending`、`running`、`waiting_for_user` 从刷新或历史打开时进入 live run；`interrupted` 显示继续运行语义；终态才进入 replay。
- 停止运行必须同时要求存在 `runId` 且 status 可取消；取消 API 返回后依赖 store 主动刷新 run 快照。
- 启动前展示 workflow lint；error 阻断，warning 只提示。含唯一 `user_input` 节点时启动前必须填写输入，不要求它是入口节点。
- `can_view_source=false` 的市场应用不能显示节点数量、prompt、Trace、内部 step 日志或来源节点标题；预检和运行仍由后端使用真实 graph。
- 结果区只显示 `输出` 和 `文件`。文件产物通过 `RunArtifactsPanel` 调用 run artifacts API；只使用 `download_url`，不拼本地路径，不扫描 HTML。
- HTML 输出通过 `HtmlOutputFrame` iframe 隔离渲染；iframe 内不承担页面级滚动，外层容器负责滚动。
- Console Trace 只面向桌面编辑器调试，支持 `generate`、`condition`、`output`；不要扩展到 App View 或手机端，除非用户明确要求。
- 从历史 run 指定节点重新执行、失败节点修复和 condition 分支测试走 `useRunStore.rerunFrom`，创建新 run；来源 run 保持只读。
- condition 分支测试只放在桌面编辑器 Console，使用 `condition_branch_override` 写入新 run snapshot，不修改 App graph。
- waiting/ask_user 面板使用后端 context/groups/options；先展示 `context.title` 和 `context.summary`，补充文本和附件按题目隔离，多问题只在最后一题提交，提交后显示摘要并保留停止入口。
- StepTab 的“生成提示词”调用 Prompt Assistant API，只写回目标节点 prompt/可选 output_contract；生成态按节点 id 存在 editor store，切换节点不自动取消。
- Prompt Assistant 前端当前不做 active endpoint 刷新恢复；不要在组件文案或状态里假设它已恢复。
- 模型下拉只显示当前 Agent 的 `supported_models`；推理等级使用 provider 固定选项，默认最低 `low`。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查启动输入、lint、streaming delta、step end、run end、cancel、waiting resume、interrupted continue、历史回放、Trace、rerun-from、失败修复、condition 分支测试、artifacts 和 HTML 输出。
