# ADR 0004: Workspace Wiki working copy and Workflow proposal bridge

- Status: Accepted
- Date: 2026-09-01

## Context

A long-lived Codex Workspace must be able to improve the owner's LLM Wiki and use Mira's visual workflows without bypassing the existing revision, graph validation, source-redaction, Run, Decision, and Artifact boundaries.

Mounting the live Wiki directory into every Workspace would allow last-writer-wins corruption and would make ordinary Runs freeze an uncommitted state. Letting Codex write App graph JSON directly to the database would similarly bypass lint, ownership, and user confirmation.

## Decision

Every Workspace has a Wiki working copy with an explicit base revision. Before each Workspace turn, Mira synchronizes the current official Wiki revision into the working copy. Codex may change only Markdown files below `wiki/`; `raw/`, `purpose.md`, and `schema.md` are protected by backend verification.

After a successful turn, Mira compares the working copy with its base. If the official revision is still the base, the verified candidate is published as a new immutable revision. If another Workspace has published in the meantime, the Wiki Maintainer receives complete `base`, `current`, and `proposed` trees and produces a merge candidate. The backend verifies protected content and Wiki invariants before publishing. A failed or cancelled turn never writes back. A failed merge preserves the proposed tree, marks sync failed, and exposes retry; it never overwrites the official revision.

Creating a formal Application Run from a Workspace first completes pending Wiki synchronization. The existing Run creation service then freezes the resulting official revision through the normal `run_wiki_snapshots` path.

Workspace access to workflows is a bridge, not a new node type. Codex may list and read Apps visible under existing permissions, but owned graph changes are saved as immutable `WorkflowProposal` records containing a base graph SHA-256 and candidate graph. The backend runs the existing clean/validation/lint pipeline. The browser shows a read-only React Flow preview and requires owner confirmation. Confirmation fails when the App graph no longer matches the base digest; the model never writes App rows directly.

Running a workflow inside a Workspace creates a normal Run and uses the existing Run/Step/SSE/Decision/Artifact APIs. Workspace UI may host those controls, but it does not create a second execution engine. Third-party Apps retain source redaction and existing run permissions.

## Consequences

- Wiki updates from several Workspaces are serialized through immutable revisions and three-way merge rather than last-writer-wins.
- Formal Runs remain reproducible and see only published Wiki state.
- Workflow authoring remains user-confirmed and protected from stale graph overwrites.
- Workspace UI can provide an integrated experience while Run history, waiting questions, cancellation, output, and formal Artifacts keep one source of truth.
