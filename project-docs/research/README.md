# Research records

These files are research records. Each one owns no work and no status. The
spec that cites a record owns the work, and Metorite holds the current state
(D118).

Read a record to learn **why** a decision was made. Do not build from it. A
record describes the code and the products at its date, so check each fact
against the code first.

## The records

| Record | Date | What it holds | Cited by |
|---|---|---|---|
| `plane_pm_research_2026-08.md` | 2026-08 | A read of Plane (AGPL-3.0, patterns only), with the verdicts P-1 to P-31 | `specs/project_management_app.md` |
| `paca_pm_research_2026-08.md` | 2026-08 | A read of Paca (Apache-2.0), with the adopt, adapt and refuse table | `specs/project_management_app.md`, `specs/workflows_app.md` |
| `note_taker_research_2026-07.md` | 2026-07 | The evidence base of the note taker | `specs/note_taker_app.md` |
| `multiplayer_prior_art_qm_2026-08.md` | 2026-08 | Prior art for many people and agents in one session | `AGENTS.md`, `docs/multiplayer/` |
| `multi_user_organization_research.md` | 2026-07 | Orgs, members, roles and memory scope | `specs/org_access_control.md`, `specs/saas_multitenancy.md` |
| `chat_agent_framework_review_2026-07.md` | 2026-07 | A review of the chat and agent frameworks | `specs/agent_coding_skill.md`, `specs/maf_coding_engine.md` |
| `competitive_hardening_2026-07.md` | 2026-07 | Patterns from competitor agents, CH-1 to CH-9 | `AGENTS.md`, `specs/permissions_sandbox_b6.md` |
| `harness_hardening_2026-07.md` | 2026-07 | A gap analysis of the agent harness, HH-1 to HH-8 | `AGENTS.md` |
| `agent_platform_hardening_2026-07.md` | 2026-07 | An adversarial review of the agent platform, with the T0, T1 and T2 isolation ladder | `specs/permissions_sandbox_b6.md`, `specs/agent_architecture.md` |
| `COMPETITIVE_COMPARISON.md` | 2026-07 | Metorite compared with Hermes Agent and OpenClaw | `AGENTS.md` |

## Rules

1. **Put new research into the owning spec.** Write it as a "What other apps
   do" subsection with its sources. `specs/project_import.md` §4 is the
   model. Add a file here only when a record is too large for its spec.
2. **File the research before a planning session ends.** Post it on the
   Metorite epic as a "Research notes" comment. The spec slice of that epic
   then copies it into the spec.
3. **Do not delete a record that a live doc cites.**
   `tests/unit/test_doc_names_resolve.py` fails when a doc names a file that
   does not exist.

The cleanup PR #1386 deleted these records on 2026-10-29, while live docs
still cited them. Task #956 restored them from git history (`ba15fb9f2^`).
