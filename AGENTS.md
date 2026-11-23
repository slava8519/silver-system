---
type: doc
schema_version: 1
updated: 2026-11-23
---

# Instructions for the Second Brain project

This is an independent product-design project. Treat the directory containing this file as the project root and resolve project paths relative to it.

## Start here

For everyday vault tasks, choose the matching project skill in `.agents/skills/` from its name and description. Load its instructions and use its targeted CLI commands with the selected vault. Resolve a supplied relative vault path from the working directory and pass it directly to the CLI; check that one directory if needed. If the vault is ambiguous, ask which one to use. Expand to another skill or source when the task calls for it.

For product development, read `README.md`, `ROADMAP.md`, and the relevant documents in `docs/`. In the owner's workspace, also read the local `NEXT-SESSION.md` and consult the internal mapping in `private/` when discussing compatibility with the original working system. Do not treat the neighbouring product draft as an approved specification.

## Product decisions

- One universal second brain; personalisation primarily through directions. Do not require users to choose a personal-versus-manager identity.
- Check the same operations on three coexisting synthetic directions: managing several IT projects, personal life, and perfumery. The guided demo teaches the model, daily use, and adding one's own direction.
- Codex and Obsidian are the owner's everyday tools. The owner asks questions about working records in Codex and follows citations to the source. The local admin interface manages directions and projects, shows the vault's structure, links and current state, and collects text, files and screenshots in Inbox for later processing. Keep owner clarification questions in the Inbox review. The guided demo has its own question interface over synthetic records. Use the same Markdown records and shared operations across interfaces.
- Cover each new user-facing vault operation with a focused, portable skill, extending an existing one when it fits. Keep domain-specific facts in the vault. Check the result, its sources or write revision, and the amount of data returned to the agent on synthetic examples. Browser controls invoke the shared operations directly.
- All directory names must be English. Documentation, note contents, titles, and direction names may use the owner's preferred language.
- People are first-class cross-cutting entities with one canonical card per person. Team membership, roles, observations, and reviews are separate concepts.
- When a decision depends on who the owner means by “I” or “my team”, read the selected vault’s owner context through `brain-person`. Use its explicit membership links to identify people, and consult sourced records for their responsibilities and availability.
- A direction is persistent; a project has a finite outcome; a cycle or plan groups existing work. Preserve this distinction for the current high season.
- Projects and OKRs are optional ways to organize a direction's work. A task may belong directly to a direction or to a project. People choose their project and reporting formats. A plan groups work by stable ID. Gantt is an optional view and editing interface over the existing work records. A task card stores its agreed due date (`due`), optional forecast (`forecast`) and confirmed completion (`completed`) independently. Date changes and their history belong to the task.
- The working admin and the desktop Obsidian Gantt share the same editor and vault operations. Store row summaries, sourced events and owner corrections in the task card. Keep the two-way flow discoverable through the planning skill.
- Preserve source attribution, history, stable identity, and manual decisions. Never invent dates, progress, authorship, or coverage to complete a template.
- Keep the initial implementation small: Markdown, ordinary attachments, a rebuildable index, and tested commands. Do not introduce a separate application, workflow platform, or vector database without an agreed need.

## Scope and safety of changes

Work within this project. Do not migrate, rename, edit, or clean up the neighbouring PerfQA vault as a side effect. A future migration needs a separate plan preserving fields, sources, links, and history.

This project contains design documents and a local Python command-line prototype. Run its own checks with `python3 -m unittest discover -s tests -v` and record observed results. Do not run parent-vault maintenance commands against this project. Public distribution and a Git repository are separate decisions.

For document edits, verify internal links and consistent terminology. When adding code, introduce relevant tests and document actual checks in the handoff.

The `private/` directory contains internal PerfQA mappings and must not enter a public distribution. Use synthetic examples for a future public starter. Publication, a licence choice, and copying working code are separate decisions; do not publish automatically.

In the owner's workspace, keep `NEXT-SESSION.md` current when a decision changes. Distinguish agreed requirements, design proposals, implemented features, and verified outcomes.
