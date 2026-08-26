# ADR 0002: User-scoped LLM Wiki as a frozen read-only Run context

- Status: Accepted
- Date: 2026-08-25

## Context

Mira needs a long-lived place for user documents that Codex can consult across Apps. This is different from temporary Uploads, App asset nodes, branch workspaces, formal Artifacts, and Run output. The requested behavior explicitly excludes RAG: no chunking, embeddings, vector database, semantic retrieval, or automatic context injection.

The feature must preserve Run reproducibility, checkpoint rerun semantics, tenant isolation, public App source redaction, and the rule that only artifact contracts expose Run files.

## Decision

Each user has one Wiki with four logical areas:

- `raw/`: immutable copies of user-managed sources;
- `wiki/`: Maintainer-generated Markdown;
- `purpose.md` and `schema.md`: organization constraints;
- required `wiki/index.md`, `wiki/log.md`, and `wiki/overview.md`.

Sources, maintenance operations, and Wiki revisions are persistent database facts. Revisions are immutable trees with a complete manifest and tree hash. Uploading, deleting, renaming, manual maintenance, and restore all produce a new revision; an operation failure leaves the current revision unchanged.

Convertible files are parsed with `MarkItDown==0.1.7` in a network-disabled Docker helper. Images are available to Codex as files. Uploads and renames only accept those convertible documents and images; archives and other non-convertible formats are rejected at the API boundary and are not stored. The Codex Maintainer receives a writable candidate tree, while backend verification prevents changes to raw sources, `purpose.md`, or `schema.md` and accepts only Markdown under `wiki/`.

New Runs freeze the runner's current revision and raw source manifest into `run_wiki_snapshots`. The materialized snapshot is outside every branch workspace and is mounted read-only at `/mnt/wiki` for planning, execution, contract repair, fan-out, and fan-in. Waiting/resume uses the same Run snapshot; checkpoint rerun copies the source Run snapshot. Run output, workspace files, and Artifacts have no call path to Wiki mutation.

An owned App automatically uses the runner's Wiki. A third-party App requires a remembered grant keyed by `user_id`, `app_id`, and the canonical graph SHA-256. Changing the graph invalidates the grant. Declining does not block execution; the Run is created without a Wiki snapshot.

NL compile and Prompt Assistant do not mount Wiki. Mobile Run follows the same access rules, while Wiki management remains desktop-only in the first version.

## Consequences

- Runs remain reproducible even when the live Wiki changes.
- The read-only mount makes non-writeback an enforcement rule rather than a prompt convention.
- Files remain transparent to Codex and ordinary filesystem tools, but very large Wikis have no semantic recall guarantee.
- Revision trees and Run snapshots add storage metadata and lifecycle cleanup. Hard links are used when available, with copy fallback.
- Read-only access does not prevent an Agent from quoting source content in its output. Users must not authorize untrusted public Apps to read secrets.
