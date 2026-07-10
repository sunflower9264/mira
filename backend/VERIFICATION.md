# Verification Checklist

This checklist tracks the current Mira backend and frontend verification surface.

## Automated Checks

Backend:

```powershell
cd backend
uv run pytest -q
uv run python -m compileall app scripts
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Frontend:

```powershell
cd web
npm run typecheck
npm run build
```

Docs / prompt seed changes:

```powershell
git diff --check
cd backend
uv run pytest -q tests/test_prompt_templates.py tests/test_condition_node.py
```

## Covered By Tests

- Public `/api/health` and `/api/docs`.
- Auth login/me, disabled register endpoint, create-user script, wrong password, invalid token, admin bootstrap rules.
- Per-user app/run/SSE isolation.
- App CRUD, delete cascade cleanup, gallery clone, version clone, publish/unpublish.
- Version limit pruning while preserving published versions.
- Settings read, admin-only writes, Agent config encryption, config validation, required supported-model validation, and secret-field cleanup.
- Prompt template seeding, admin editing, reset behavior, and condition prompt usage.
- Skill upload, metadata parsing, deletion through settings, invalid zip rejection, safe extraction.
- MCP config rendering and env whitelist behavior.
- Skill runtime sync into run-scoped Claude `.claude/skills` and Codex `.agents/skills`; shared fake HOME skills remain empty.
- NL compile enabled-Agent guard, Agent failure path, and LLM patch application path with runtime override.
- Condition nodes: binary/cases parsing, `__default__` fallback, branch skipping, source handle validation.
- Run happy path, failure path, cancel, total timeout, stale-run recovery.
- SSE terminal replay and valid/invalid `Last-Event-ID`.
- Runtime JSON parsers for Claude/Codex text/tool/session events.
- Runtime fake HOME env isolation and provider-key non-leakage.
- 500 handler shape with `request_id`.

## Manual With Real Credentials

Required before calling real-agent support complete:

1. Configure shared Claude/Codex runtime config files, supported model lists, and global instruction files in Settings.
2. Click Settings refresh and confirm the CLI/config status is ready.
3. Clone `tpl_book_recs` from Gallery and run it from Preview.
4. Confirm streaming text chunks appear in Console.
5. Run the same generate node twice and confirm session continuation.
6. Configure an MCP server and confirm `tool_call` / `tool_result` chunks.
7. Upload and enable a Skill zip and confirm real agent behavior changes.
8. Repeat startup on a fresh Windows clone.
9. Verify `start.sh` or `scripts/dev.py` on macOS, Linux, or WSL.

## Evidence Map

| Area | Evidence currently expected | Remaining real-world evidence |
| --- | --- | --- |
| Startup | `.env` init, runtime install, admin init, `/api/health`, `/api/docs`, OpenAPI. | Fresh clone run-through on target machines. |
| Auth | pytest covers login/me, disabled register endpoint, create-user script, and admin/user separation. | UI login click-through on fresh clone. |
| Apps/Versions | pytest covers CRUD, gallery, clone, publish, unpublish, pruning. | UI editor/gallery/version history click-through. |
| Settings/Skills | pytest covers admin-only settings, skill upload/delete, config validation, and supported-model validation. | Real Settings save with usable runtime configs and model lists. |
| Runtime | parser, fake HOME, internal ask_user bridge, and Docker sandbox runner tests; runtime image build is managed by dev script. | Real Claude/Codex container smoke with valid credentials and Docker available. |
| Run/SSE | pytest covers run lifecycle, cancel, replay, stale recovery. | Real streaming run in UI. |
| Conditions | pytest covers branching, skipped steps, source handles, prompt override. | UI run with binary and cases condition graphs. |
| MCP/Skills | pytest covers rendering/sync. | Real MCP tool call/result and real Skill behavior change. |
| NL Compile | pytest covers heuristic and LLM patch path. | Real LLM NL compile with configured provider. |

## Notes

- Do not print or paste real credentials into prompts, tests, logs, or expected model output.
- `backend/runtime/`, `backend/data/`, `.env`, logs, caches, and runtime `node_modules` should remain out of git.
- `.serena/` is intentionally deleted by the user in this workspace and should not be restored by documentation work.
