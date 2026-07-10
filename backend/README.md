# Mira Backend

FastAPI backend for Mira. It implements the authenticated HTTP API and per-run SSE contract used by `web/`.

## Quick Start

Windows:

Run the backend from a WSL2 Linux shell. Native Windows Python startup is not supported for the Docker Agent sandbox runtime.

macOS / Linux / WSL2:

```sh
cd /path/to/mira/backend
uv sync
uv run python scripts/dev.py
```

`scripts/dev.py` will:

- create `backend/.env` from `.env.example` when missing;
- generate `JWT_SECRET` and `AGENT_CONFIG_SECRET` placeholders;
- check/build the Docker Agent runtime image from `backend/runtime/Dockerfile`;
- run `scripts/init_admin.py`;
- start uvicorn on `http://0.0.0.0:8000`.

Before the backend can start, edit `backend/.env` and set `ADMIN_PASSWORD` to a real value. Startup refuses an empty password, a password shorter than 6 characters, or the placeholder `change-me`.

Useful URLs after startup:

```text
http://0.0.0.0:8000/api/health
http://0.0.0.0:8000/api/docs
http://0.0.0.0:8000/api/openapi.json
```

Frontend:

```powershell
cd web
npm ci
npm run dev -- --host 0.0.0.0
```

Open `http://0.0.0.0:5173`; Vite proxies `/api` to the backend.

Root startup scripts are also available:

On Windows, run the root startup script from WSL2:

```sh
sh start.sh
```

They stop existing listeners on ports `8000` and `5173`, then start backend and frontend.

## Development Checks

```powershell
cd backend
uv sync
uv run pytest -q
uv run python -m compileall app scripts
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Frontend checks:

```powershell
cd web
npm ci
npm run typecheck
npm run build
```

See `backend/VERIFICATION.md` for the current verification checklist.

## Directory Layout

```text
app/
  api/        FastAPI routers
  models/     SQLAlchemy ORM models
  schemas/    Pydantic request/response schemas
  services/   app logic, settings, prompt templates, run orchestration, SSE hub
  runtime/    AgentRuntime interface, Claude/Codex adapters, ask_user bridge, Docker sandbox runner
migrations/   Alembic migration environment and versions
scripts/      dev, init, smoke, and admin scripts
seeds/        gallery, default agents, and prompt template seeds
tests/        pytest integration tests and test-only MockRuntime
```

Runtime and user data are intentionally ignored by git:

```text
backend/data/
backend/runtime/homes/
backend/runtime/workspaces/
backend/logs/
```

## Core Concepts

- Users own Apps, Versions, Runs, Steps, Step Logs, and per-user runtime workspaces.
- Built-in gallery seed apps are owned by `system_gallery` and are read-only source templates. `GET /api/apps?gallery=true` returns these templates, while `GET /api/apps?market=true` excludes them from the public app market. The frontend may display both lists together in the app market area, but templates must still be cloned into a user-owned draft before editing.
- `GET /api/apps/recent-runs` returns the current user's recently run visible apps, including owned apps and market apps, ordered by latest run time.
- A workflow may contain at most one `user_input` node and one `output` node. Hard graph validation rejects extra singleton nodes, and workflow lint reports readable preflight errors before run/publish. `output` is the terminal display node and cannot be an edge source.
- Each Run stores the graph snapshot captured at creation time. Execution, continuation, and history serialization use that snapshot, so later App graph edits only affect future runs.
- `POST /api/runs/{run_id}/rerun-from` creates a separate run from a historical source run, captures the current App graph, copies reusable successful/skipped ancestor Step results before the selected node, and then continues through the existing run orchestrator. When request inputs override a changed upstream `user_input`, the new run starts from the earliest changed input ancestor instead of reusing that old input step. It can also carry a `condition_branch_override` for condition branch test runs; the override is written only into the new run snapshot.
- App cover images are stored as uploads. `App.cover` stores only an upload id; clients read the image through `GET /api/apps/{app_id}/cover`.
- Asset node fields are type-specific: text uses `content`, URL assets use `urls[]`, file assets use `uploads[]`, and drawing assets use a single `upload`. File asset execution returns an array of upload metadata objects with runtime-readable `path` and signed `download_url`.
- `POST /api/apps/{app_id}/lint` provides readable workflow preflight issues. Errors block run/publish entrypoints, including non-empty workflows without an `output` terminal node; warnings are shown as guidance and do not replace the existing hard graph validation.
- Upload attachments and run-generated files exposed to run prompts include runtime-readable paths plus signed relative `download_url` values. Output HTML should use `download_url` for user download links and avoid showing local server paths; historical run serialization also converts workspace file paths into signed artifact links.
- `GET /api/runs/{run_id}/artifacts` returns a run-level artifact list for the run owner. It is assembled on demand from artifact output-contract results and files in the run workspace, uses signed download URLs for workspace files, filters unsafe contract URLs, and does not require a separate artifact table.
- Settings, Skills metadata, MCP config, Agent config contents, Agent supported model lists, global instruction files, and Prompt Templates are global and admin-maintained. Admins can preview uploaded Skill archives through the stored `SKILL.md` without injecting the Skill into shared fake HOME.
- Claude/Codex config contents are stored encrypted in the database. Runtime files under shared fake HOME directories are regenerated from DB and may overwrite local edits.
- Enabled MCP servers and Skills form the global Tool inventory. Apps use all enabled Tools by default and can exclude individual Tools through `graph.tools.disabled_tool_ids`; LLM graph nodes do not use `allowed_tools` to enable or disable Tools. MCP servers and Skills also have a `planning_enabled` flag, defaulting to false; only flagged tools are injected into NL compile, Prompt Assistant, and app-run preflight planning/read-only calls.
- Supported model lists live in Settings (`agents_json[].supported_models`). They are manually maintained by admins and required when saving an Agent config. Models are not inferred from Claude/Codex config text, SDKs, CLI state, or auth files.
- Keep `AGENT_CONFIG_SECRET` with database backups; encrypted Agent config rows cannot be decrypted without it.

## Agent Runtime

Claude CLI (`@anthropic-ai/claude-code`) and Codex CLI (`@openai/codex`) run only inside the Docker Linux sandbox image built from `backend/runtime/Dockerfile`. The image keeps Python 3.12 and uses Node.js 22 for the Agent CLIs. `scripts/ensure_runtimes.py`, which runs as part of `scripts/dev.py` and the root startup scripts, builds the image when Docker is available and rebuilds it when the Dockerfile or bundled runtime helpers change. The backend no longer installs or executes Claude/Codex CLIs on the host. The runtime image also includes Chromium, CJK fonts, and `/opt/mira/capture_screenshots.py` so app workflows can run generated web prototypes and produce screenshot artifacts from inside the sandbox.

Windows support is WSL2-only: run the backend from a WSL2 Linux shell and use Docker Desktop WSL integration or Docker Engine inside WSL. Keep the repository, `backend/data`, and `backend/runtime` on the WSL filesystem rather than `/mnt/c` to avoid path and bind-mount edge cases.

Runtime isolation is intentional:

- Each Agent call starts a short-lived container from `RUNTIME_SANDBOX_IMAGE`.
- The call workspace is mounted read-write at `/workspace`; scoped HOME is mounted read-write at `/home/mira`; the current user's uploads are mounted read-only at `/mnt/uploads`.
- The container does not mount the host HOME, project root, Docker socket, `.env`, shared runtime directory, or unrelated user workspaces.
- Containers run with the backend process UID/GID, keeping bind-mounted scoped HOME and workspace directories writable across Linux/WSL deployments. `HOME` still points to `/home/mira`, and containers keep dropped capabilities, `no-new-privileges`, and env-configured memory/CPU/pids/timeout limits.
- Claude uses `.claude/settings.json` generated from encrypted DB config inside the scoped HOME. App runs receive a scoped `.mira/mcp.json` containing only Tools allowed by the run snapshot plus internal `ask_user`.
- Claude `stream-json` parsing consumes real text deltas and final message events without enabling partial message snapshots; partial snapshots are not safe to aggregate as output text because they can mirror the same assistant response.
- Codex reads scoped `config.toml` and `auth.json` copied from encrypted DB-derived shared config. Snapshot-allowed MCP servers and internal `ask_user` are written into the scoped config file, not passed through CLI argv. Codex exec calls skip CLI git repo trust checks because Mira supplies an isolated runtime workspace.
- Host-level Claude/Codex login state is not used by backend runs.
- Runtime env is configured through `RUNTIME_SANDBOX_IMAGE`, `RUNTIME_CALLBACK_BASE_URL`, `RUNTIME_DOCKER_NETWORK`, `RUNTIME_CONTAINER_MEMORY`, `RUNTIME_CONTAINER_CPUS`, and `RUNTIME_CONTAINER_PIDS_LIMIT`.
- Containers may use outbound network in the first sandbox version, but file access is limited by the mounted volumes and path rewriting.
- The internal `ask_user` MCP helper calls `RUNTIME_CALLBACK_BASE_URL/ask-user/{session_id}` with a per-runtime bearer token. Sandbox containers map `host.docker.internal` to Docker's `host-gateway` so the default callback URL also works with Linux Docker in WSL2. The backend validates that token and reuses the existing waiting/resume/SSE flow while the runtime waits for resume or cancellation.
- Each run stores `graph._runtime_tools.allowed_tool_ids` at creation time. Runtime injection intersects that snapshot with currently enabled Settings Tools, so App edits do not change an existing run while Admin disable/delete still prevents future use.
- Planning calls use the same App disabled-tool snapshot but additionally require the Settings Tool's `planning_enabled` flag. This keeps search/reference Skills or read-only MCP available during ask_user planning without exposing write-capable tools by default.
- Run execution is dependency-driven: nodes whose direct upstreams are complete can run concurrently. Linear LLM chains reuse the previous step's Agent session, while parallel branches, fan-out, and fan-in nodes use independent sessions and exchange context through explicit upstream outputs.
- Runtime calls only pass a model when a graph node has an explicit `model`; there is no backend fallback model. The UI model picker only lists `supported_models` from enabled Agents.
- Runtime calls pass node-level `reasoning_effort` when present, and normalize missing or invalid values to the provider's lowest level (`low`). Claude uses `--effort`; Codex uses `model_reasoning_effort`.
- Generate nodes can declare `output_contract` for structured JSON, HTML, or artifact outputs, but free text is the default and omits `output_contract`. Use contracts only when downstream nodes need stable fields, the current generate node must emit an HTML snippet, or the node must create downloadable files. JSON contracts must include a strict object `json_schema`; HTML contracts return `{"html":"..."}` and are parsed then saved as-is; artifact contracts require an `artifact_kind` and return workspace file `path` entries that the backend validates before issuing signed download URLs. The backend passes schemas to supported Agent CLIs, validates the result again, and asks the same Agent session to repair the output once before failing the step. Output nodes remain HTML-only final preview nodes and internally use the same `{"html":"..."}` contract; tool results are intermediate and are not saved as the final output.
- LLM step trace is exposed through `GET /api/runs/{run_id}/steps/{node_id}/trace` for run owners. It supports `generate`, `condition`, and `output` nodes and is assembled on demand from the run graph snapshot, step input/output, persisted `step.delta` events, logs, and run workspace artifacts. Artifact entries use signed download URLs and do not expose local absolute paths.

## Prompt Templates

Default prompt templates live in `backend/seeds/prompts/*.md` and are synchronized into the `prompt_templates` table. Seed files are the source of truth: `seed_prompt_templates()` overwrites database rows with the same key, so runtime prompt constraints take effect in existing databases. Admin prompt edits from Mira Settings update both the database row and the matching seed file, so backend startup keeps the saved content.

Important templates:

- `nlcompile_plan`: asks the selected agent to produce a structured confirmation plan with goal, assumptions, data flow, implementation steps, graph changes, expected inputs, expected outputs, and acceptance criteria. New workflows and large edits do not trigger questions by themselves: `ask_user` is reserved for missing business decisions that cannot be inferred and would materially change the visible result or graph topology. Key implementation, input, output, change, and acceptance lists must be non-empty so structurally valid but content-free plans enter repair instead of reaching confirmation.
- `nlcompile_graph_patch`: after the user confirms a plan, asks the selected agent to produce at least one graph patch from `$confirmed_plan`. The shared patch protocol includes the legal JSON shape for every node type plus normal and condition edges, so empty graphs do not require the model to guess wire fields. This stage must not call `ask_user`, redesign the plan, return an empty success, or change topology for visual layout; a later layout pass owns coordinates. Added nodes receive deterministic fallback coordinates before layout so a layout failure still returns an editable graph.
- `condition_choice`: gives a condition node structured branch key/label pairs and asks it to return exactly one key. Cases nodes also receive the reserved `__default__` option when the graph has a real default edge.
- `ask_user_protocol`: tells planning calls how to ask focused user questions through the real ask_user tool, provide 2-3 real option objects per question with `label`, `description`, and one first-position `recommended` default, adopt the returned user answers, and ask a focused follow-up only when the answer creates a real unresolved conflict or ambiguity. Mira appends the fixed option `以上都不是` before exposing the question to the user. NL compile and Prompt Assistant button calls receive the real tool through Mira's internal stdio MCP bridge. If that internal bridge or MCP call fails before NL compile has persisted a waiting request, Mira returns a 502 instead of accepting a plan generated after the failed tool call; once the waiting request is persisted, runtime tool timeouts keep the DB session resumable so the user's answer can be replayed into a new plan call. Prompt Assistant waiting requests are stored in `prompt_assistant_generations` and can be restored with `GET /api/apps/{app_id}/prompt-assistant/active`; when there is no waiting request, the active endpoint returns `204 No Content`. Planning calls may use read-only runtime capabilities and explicitly planning-enabled MCP/Skills, but must not use tools that mutate files, external services, or business state.
- `ask_user_preflight_protocol`: contains the app-run preflight JSON state machine. App-run generate/condition nodes use this backend-driven ask_user preflight before their normal execution call; output nodes skip it and directly render final HTML. Generate nodes with an explicit output contract and non-empty direct user input also skip preflight unless the prompt explicitly requires ask_user. Claude/Codex return structured `action=ask` or `action=complete` JSON, Mira persists each question/answer round in the step input, and normal execution starts only after a final decision summary. The preflight can use the same planning-enabled read-only tools, but does not receive the real ask_user tool or generate-node final output-contract instructions; output contracts are appended only to the normal execution prompt and repair prompt. Do not inject this JSON action protocol into NL compile plan prompts.
- `output_html_rendering`: keeps output nodes constrained to renderable HTML while preserving relevant upstream facts and applying a restrained responsive presentation baseline when the node does not specify a visual style.
- `output_contract_repair`: rewrites a generate node result once when it fails the configured `output_contract`. The repair receives the original task context and must preserve facts, values, order, language, paths, and existing HTML while making only the minimum structural correction.
- `prompt_assistant`: instructs the selected app Agent to first decide whether the user's request is a new node goal or an edit to the existing prompt, then generate a new prompt or minimally update the current generate/condition/output prompt using direct upstream/downstream graph context. The target prompt is passed in full, related prompts preserve both head and tail, and the current generate `output_contract` is visible; contexts over 200 KiB fail explicitly instead of silently truncating constraints. Button calls may ask one focused `ask_user` question and resume through an in-memory generation id; NL compile apply-time post-processing uses the same template but does not enter ask_user.
- `status_smoke`: verifies that an agent can complete a short real call.

NL compile is two-stage and persisted in `nlcompile_sessions`. `POST /api/nlcompile` returns `planned` with `compile_id`, structured `plan`, and `plan_markdown`; it does not return `new_graph` or `applied_patches`. `GET /api/apps/{app_id}/nlcompile/active` returns the latest active session for restore, or `204 No Content` when there is no active session, and `POST /api/nlcompile/{compile_id}/refine` reruns the plan stage with structured history and user feedback while preserving the same compile id. `POST /api/nlcompile/{compile_id}/apply` uses the saved current graph, confirmed plan, and history to generate patches. Apply treats the Agent output as a batch: the backend simulates the full patch set on a temporary graph, retries order-dependent `update_node` and `add_edge` patches after later patches have been applied, then runs full graph structure, prompt-node, topology, and NL-compile-only redundant-transitive-edge validation. When the batch adds or updates prompt nodes, NL compile calls Prompt Assistant to rewrite those prompts with confirmed plan and ask_user history before applying AI layout beautification. If parsing, patch application, prompt-assistant post-processing, or validation fails, the backend asks the Agent to regenerate the complete `{"patches":[...]}` JSON up to 3 total attempts. If repair still fails, the request returns 502 and no partially applied graph is returned. Backend startup marks planning, waiting, and applying nlcompile sessions as `interrupted` so the frontend can restore or replay from DB state.

## Admin Scripts

```powershell
cd backend
uv run python scripts/create_user.py --username <name>
uv run python scripts/delete_user.py --username <name> --dry-run
uv run python scripts/reset_user_runtime.py --username <name> --dry-run
```

`create_user.py` creates a regular non-admin user and prompts for a password by default. Use `--password` only for local automation.

Destructive admin scripts require `--apply` plus typing the username to confirm.

## Real Runtime Verification

After configuring shared Claude/Codex Agent configs and supported models in Mira Settings:

```powershell
cd backend
uv run python scripts/smoke_runtime.py --provider claude --username <username> --app <app_id> --node <node_id> --prompt "hi"
uv run python scripts/smoke_runtime.py --provider codex --username <username> --app <app_id> --node <node_id> --prompt "hi"
```

Prompt-effect regressions use the isolated real-backend fixture, which copies the configured source database and writes only to pytest temporary data/runtime directories. For a three-run semantic check, export `MIRA_RUN_REAL_AI_BACKEND_TEST=1`, point `MIRA_REAL_AI_SOURCE_DB` at the configured development database, set `MIRA_REAL_AI_EFFECT_REPEATS=3` and `MIRA_REAL_AI_EFFECT_DELAY=25`, then run the selected cases in `tests/test_real_ai_backend.py`. The delay prevents a valid multi-call workflow from being mistaken for a prompt failure when the provider rate-limits burst traffic.

Then verify in the UI:

- Settings refresh shows installed/runnable for the configured agent, and generate/condition node model pickers show only models from enabled Agents.
- Clone `tpl_book_recs`, open Preview, and run it with a short input.
- Console shows streaming text chunks.
- A second run of the same generate node resumes the stored `agent_session_id`.
- Configure an MCP server and confirm `tool_call` / `tool_result` chunks.
- Upload and enable a Skill zip and confirm agent behavior changes.

Do not put secrets in prompts or expected model output; smoke scripts print chunks and final output.

## Common Issues

- `未找到 runtime sandbox 镜像` / Docker daemon unavailable: confirm Docker Desktop WSL integration or Docker Engine is running, then re-run `uv run python scripts/dev.py` to build `RUNTIME_SANDBOX_IMAGE`.
- Admin init fails: update `ADMIN_PASSWORD` in `backend/.env`.
- SQLite locked: stop duplicate backend processes, then restart.
- Large user data: use `delete_user.py` or `reset_user_runtime.py` dry-run first, then rerun with `--apply`.
