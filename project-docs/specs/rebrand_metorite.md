# Rebrand: CommandCenter → Metorite

**Status:** ACTIVE — plan of record for the brand migration.
**Owner:** owner (Vijay).
**Board:** [`../metorite_migration.md`](../metorite_migration.md) rows **MG-13 … MG-18**
own the execution order and states. This spec owns *what* to rename and what to freeze;
the board owns *when*.
**Origin:** this repository is a full-history mirror of `FracktalWorks/CommandCenter`.
See [`docs/history/README.md`](../../docs/history/README.md) for the inherited commit and PR history.
**Inherited P0:** CommandCenter `work_plan.md` **WS-2 / BO-8** (rotate Zoho token, purge
history, fail-closed) has been open since 2026-07-11 and travels with this migration —
see the board's MG-2. It is not closed by renaming anything.

---

## 1. The one rule

**The brand is a surface. The codename is not.**

`CommandCenter` is a *name* — it appears in docs, UI strings, page titles, and repo
URLs, and it can be replaced by a find-and-replace with a reviewer on it.

`acb` is an *identifier* — it is the Python distribution namespace, the systemd unit
prefix, the Docker container names, the deploy path on the box, the env-var prefix,
and the Redis key prefix. Renaming it is a **production migration with no rollback**
(R6: we roll forward or restore, never back). It buys nothing a customer can see.

> **Decision: `acb` stays.** Metorite is what the product is called. `acb` is what the
> internals are called, the way Google's monorepo is `google3` and Meta's stack is
> still `fbcode`. Revisit only if `acb` ever leaks into a customer-visible surface.

---

## 2. Rename now — the brand surface

Roughly **850 occurrences across 275 files**. Do it as **one PR per pile**, not one
big sweep, so the review is tractable.

| # | Pile | Where | Notes |
|---|---|---|---|
| B1 | **UI strings** | `workbench/control_plane/src/**` | Page `<title>`, nav header, login screen, empty states, email templates. This is the only pile a customer sees — do it first. |
| B2 | **Repo URLs** | `.github/workflows/**`, `README.md`, `scripts/**` | `FracktalWorks/CommandCenter` → `Hathi-Labs/Metorite`. Anything that clones or `gh api`s the old repo breaks silently otherwise. |
| B3 | **Product docs** | `project-docs/**` (39 files under `specs/`) | Prose only. Do **not** rewrite historical decision records D1–D31 — they were taken under the old name and rewriting them destroys the audit trail. Add a banner instead. |
| B4 | **Engineering docs** | `docs/**`, `learning-resources/**`, `CLAUDE.md`, `AGENTS.md` | `CLAUDE.md` line 3 (`You are working on **CommandCenter**`) is the highest-leverage single line — every agent session reads it. |
| B5 | **Test fixtures** | `tests/live`, `tests/unit` (30 files) | Some assert on the literal string. Change the fixture and the assertion together or the suite goes red. |
| B6 | **Package display name** | `workbench/control_plane/package.json` | `"name": "control_plane"` — cosmetic, unpublished. |

### Fence (R7)

Add `tests/unit/test_brand_surface.py`: assert that no file under
`workbench/control_plane/src/` contains `CommandCenter` or `Command Center`
case-insensitively. That makes a brand regression fail the build. Everything
outside `src/` is advisory.

---

## 3. Do NOT rename — the coupled internals

Each of these is load-bearing on the live box. Changing one is a deploy, a migration,
or an outage.

| Identifier | Count | Why it is frozen |
|---|---|---|
| `acb_common`, `acb_auth`, `acb_llm`, `acb_audit`, `acb_graph`, `acb_skills` | 194 files import them | Python distribution names in `pyproject.toml`. Renaming means touching every import in the tree plus the `uv` lockfile. Zero user-visible benefit. |
| `acb-gateway`, `acb-workbench`, `acb-backup`, `acb-health-watchdog`, `acb-whatsapp-bridge` | 7 unit files | systemd unit names, already installed and enabled on the VPS. Renaming = stop, disable, reinstall, re-enable, by hand, with downtime. |
| `acb-postgres`, `acb-redis`, `acb-neo` | 20+ refs | Docker container names. The DB connection strings resolve them by name. |
| `/opt/acb/app`, `/opt/acb/data`, `/opt/acb/backups` | 29+ refs | Deploy root, the pull-timer's working directory, and the backup destination (BO-23). |
| `ACB_MASTER_KEY`, `ACB_AGENT_USER_EMAIL`, `ACB_ENV`, `ACB_LIMITS__*` | 8 vars | Env-var prefix. `ACB_MASTER_KEY` decrypts stored credentials — renaming it without a dual-read shim locks everyone out. |
| Postgres database name, roles, Redis key prefix | — | R5/R6. A rename here is a migration, and we cannot roll back. |

If any of these ever must move, they go through **expand/contract** (R6): read both
names, write the new one, drop the old one a release later — never a rename in place.

---

## 4. What did not come across from GitHub, and must be re-created by hand

The mirror carries git. It does not carry GitHub.

- [ ] **Actions secrets** — every deploy and CI workflow is red until these exist.
- [ ] **Branch protection on `main`** — was enabled on CommandCenter 2026-08-03
      (PRs required, force-push blocked, `required_status_checks` deliberately
      `null` so docs-only PRs run zero checks). Re-create it with the same shape.
- [ ] **Deploy keys / environments / webhooks.**
- [ ] **Repository default branch and merge settings.**
- [ ] **The 72 inherited branches** — mostly `worktree-agent-*` scratch from the old
      agent loop. Prune them once the history archive is confirmed; the commits stay
      reachable through `refs/archive/pr/*` regardless.

## 5. Deployment is a separate decision, not part of this rebrand

The mirrored `.github/workflows` still point at Fracktal's VPS. Until you decide
whether Metorite deploys to the same box, a new box, or nowhere yet, **leave the
deploy workflows disabled** rather than letting them fire with missing secrets. Two
repos self-deploying to one `/opt/acb/app` via the 5-minute pull timer would fight
each other on every push.
