# ADR 0003: Persistent owner-scoped Codex Workspace runtime

- Status: Accepted
- Date: 2026-09-01

## Context

Mira needs a first-class Workspace surface in addition to saved workflow Apps. A Workspace is a long-lived project directory where one owner can keep files and several Codex conversations, while background processes and project state survive across turns. It must remain compatible with Mira's Docker-only Codex boundary and must not expose a host shell through the browser.

The Codex App Server supports multiple threads, streamed turns, interrupt, thread metadata, review, compaction, and background terminal inspection. Its reconnectable transport is experimental, so process loss and backend restart must be treated as normal recovery cases rather than impossible states.

## Decision

Each Workspace owns one persistent Docker sandbox and one persistent project directory. Its Codex App Server manages all sessions in that Workspace. A session stores the Codex thread id; turns in different sessions still share the same project directory. Mira permits only one active turn per Workspace so file writes and long-running process effects are ordered.

The container follows the existing runtime sandbox policy: Docker init, dropped capabilities, `no-new-privileges`, resource limits, the configured Docker network, a scoped fake HOME, and no host HOME, repository root, Docker socket, `.env`, Git credential, or another user's directory. A per-Workspace capability token authenticates the backend connection and is stored outside the project mount.

The backend owns container lifecycle and records only sanitized product events. Shell command text and stdout/stderr are neither returned by Workspace APIs nor persisted. The UI may show that a process is running, its lifecycle state, and elapsed duration. Workspace deletion is an explicit permanent operation that first stops and removes its container, then deletes its project directory, scoped HOME, sessions, turns, events, proposals, and encrypted Git credential.

Workspace turns may remain quiet for several minutes while a visual workflow runs or the user answers a question, so the backend does not use the WebSocket client's ping watchdog as a turn deadline. A transient `Reconnecting... n/m` notification is not a terminal failure when Codex subsequently reports a completed turn; non-zero exits, terminal errors, and non-completed turn states still fail normally.

Startup reconciles database runtime state with Docker containers. A healthy matching container is reattached; an absent or unhealthy container is recreated against the same Workspace directory and scoped HOME. An interrupted active turn becomes interrupted and is never silently replayed. At most ten non-deleted Workspaces may exist per owner.

Mira implements Web equivalents only for Codex commands that have a coherent product action. Session create/name/delete, goal, compact, review, interrupt, model selection, status, background-process list/stop, Skills, MCP, and file mention are explicit controls. TUI-only presentation and process controls are omitted. `/diff` is intentionally unavailable because Workspace does not expose a Git diff or manual file editing surface.

## Consequences

- Workspace files and background project processes survive across turns, while concurrent writes stay deterministic.
- Runtime restart is recoverable, but an in-flight turn can still become interrupted.
- Persistent containers consume more resources than Application Runs; the ten-Workspace cap and container resource limits are product constraints for this personal/small-team deployment.
- Workspace file visibility is broader than formal Run artifacts but remains owner-only and path-confined. This does not weaken the Application Run artifact contract.
