# Multi-tenancy — isolating organizations in Metorite

> ## ⚠️ SUPERSEDED FOR ARCHITECTURE (2026-08-09) — read `saas_multitenancy.md` first
>
> A parallel workstream landed the full design in **PR #404** while this branch was open, and
> it is canonical: **`project-docs/specs/saas_multitenancy.md`** (architecture, §11
> tickets), plus `saas_multitenancy_handover.md` (the H1→H8 runbook) and
> `saas_multitenancy_implementation.md` (shapes). Where the two disagree, that one wins.
>
> **What it answers that this document left open:** **D-MT-2 is decided — D15, pooled, enforced
> by row-level security** against an `app.tenant_id` GUC that `acb_common.db.tenant_session()`
> binds with `SET LOCAL` inside a transaction. §4's WS-29c row below (a flag-gated RLS
> experiment) is therefore struck: it is MT-1b + MT-1c on `main`, already built.
>
> **What this document is still good for**, and why it is kept rather than deleted: it is the
> *measured* record — the table counts, the two corrections marked ⚠️ below, and the reasoning
> behind the `pm_*` key that migration 161 actually carries. `multi_tenancy_leak_audit.md`
> beside it is likewise still live: its 14 findings are about paths a database predicate does
> not close, and RLS does not close them either.
>
> **Ticket-ID collision, stated so nobody reconciles it twice.** Both workstreams minted
> "WS-29". `work_plan.md`'s WS-29 row is the SaaS one. The tickets in §4 here (WS-29a…e) are
> this branch's, and only **a** and **b** were built; the rest are superseded by MT-0/MT-1.

> **Minted 2026-08-08** on the owner's notice that *"we are also going to be doing migrations
> for a multi-tenant system so that multiple organizations can use Metorite in an
> isolated way."*
>
> **Everything below §1 is measured, not recalled** — read off the migration tree and checked
> against a live Postgres 16 with the full set applied. Where this document gives a number,
> `tests/unit/test_tenancy_boundary.py` recomputes it on every run.

---

## 1. The measured state

Metorite is **single-tenant with the beginnings of a tenant boundary already in place**,
which is a better starting position than it sounds and a worse one than it looks.

| | |
|---|---|
| App tables defined in migrations | **143** (plus `LiteLLM_*`, vendored, not ours) |
| Carrying a real tenant key | **3** |
| Carrying none | **140** |
| `pm_*` tables (Projects, WS-27) | 17 — **0 scoped** |

**As of WS-29a (2026-08-08) that is 20 scoped and 123 unscoped**: the 17 `pm_*` tables were
keyed while they were nearly empty. "Nearly", not "entirely" — the premise that they held no
rows was wrong, and the live database had 10 `pm_tasks` and 2 `pm_projects` of fixture residue
from WS-27's own live runs. `SET NOT NULL` does not care where a row came from, so the
migration backfills to `slug='default'` first and fails the deploy loudly if that organization
is missing rather than guessing one.

The three that are scoped: `app_user`, `org_group`, `org_role` — all
`REFERENCES organization(id)`.

> ⚠️ **CORRECTED 2026-08-08. This document first said six, and it was wrong.**
> `crm_activities`, `crm_contacts` and `crm_deals` do carry a column spelled
> `organization_id`, but it `REFERENCES crm_organizations(id)` — a **customer
> company**, not the tenant root. Verified against the live database's
> `pg_constraint`. The CRM is unscoped, like everything else.
>
> **Two consequences, and the second is worse than the miscount.** First, the
> column name is *taken*: scoping the CRM needs a rename or a different name,
> and that must be decided before WS-29d touches `crm_*`. Second,
> `test_tenancy_boundary.py` matched on the column NAME, so it counted these
> homonyms as scoped — meaning any future table with an `organization_id`
> pointing anywhere at all would pass the ratchet silently. **A guard that can
> be satisfied by a coincidence of naming is not a guard.** It now matches on
> the foreign key's TARGET.

**An `organization` table already exists** (migration 130) with `slug`, `display_name`,
`domain`, `settings`, and exactly one seeded row — `slug='default'`. `app_user` gained
`organization_id` in the same migration. So the spine of a tenant model is there; it was
simply never carried past the access-control system and the CRM.

**This is not a Projects problem.** WS-27 is 17 of the 140, and the majority of the tree is in
the same position: every `gtd_*`, `email_*`, `wa_*`, `workflow*`, `app*`, `chat_*` table, and
— tellingly — `org_settings`, `org_role_permission`, `user_role` and `org_group_member`.
`org_settings` says so in its own comment: *"there is no per-tenant key namespace because this
deployment is one organisation."* That comment is about to stop being true.

### 1.1 The one number that decides the cost

`app_user.email` is **globally unique**, so a person belongs to exactly one organization.
Whether that stays true is **D-MT-1**, and it is the decision the whole retrofit hangs off.

> ⚠️ **CORRECTED 2026-08-08. This paragraph said "structurally", and until migration 162 that
> was not true.** `app_user_email_key` was `UNIQUE (email)` — **byte-exact** — while every
> lookup in this codebase matches `lower(email)` (R10). The two disagreed, and a live run
> proved the gap real: `Casey@Alpha.Example` and `casey@alpha.example` are two rows, and under
> D-MT-1 they can sit in two organizations. `resolve_organization_id` then returns whichever
> row the planner hands back, so **a person's tenant becomes non-deterministic** — and with it
> everything scoped by that tenant.
>
> Found by WS-29's S1-1 live run, reproduced directly against Postgres, and closed twice: in
> application code for the one write path that could reach it, and structurally by
> **migration 162** (`UNIQUE (lower(email))`, replacing the byte-exact constraint). The
> decision stands — (a) is still the reversible direction — but its enforcement was
> application-level while this document claimed it was structural. It is structural now.

### 1.2 Why Projects is cheaper to retrofit than its size suggests

128 `FROM`/`JOIN` references to `pm_*` tables across 16 modules — but they do not each scope
themselves. There is **one closure query**, `_VISIBLE_PROJECTS_SQL`, reached through
`resolve_visibility` (60 call sites), `load_visible_project` (31), `load_visible_task` (26) and
`task_visibility_clause` (6). Every read in the app funnels through it.

So the Projects retrofit is: **a column on 17 tables, a predicate in one query, and one line in
the `Visibility` resolver.** That is contained. It is contained *because the app was built with
a single visibility seam*, and it stops being contained the moment real data lands in those
tables.

---

## 2. What this means for the ClickUp import — read this first

~~🔴 **Do not run `POST /projects/import/clickup` against production until the `pm_*` tenant
key lands.**~~ — **SATISFIED 2026-08-08 by migration 161**, which keyed all seventeen tables.
Kept rather than deleted because the reasoning is what generalises, and because **one condition
replaced it: migration 161 has to be applied to the target database first.** It is on no real
box yet — the deploy path is broken (WS-25), so nothing on this branch has shipped.

The import is an owner gate (`work_plan.md` §6 (a)) and is the next thing WS-27 wants. Running
it now writes a real ClickUp workspace — hundreds of tasks, their activities, attachments and
grants — into 17 tables with no tenant column. Adding the column afterwards means a backfill
and an `ALTER` on live rows instead of a one-line default on empty ones.

**The cost of waiting is a few days. The cost of not waiting is paid once per table, forever.**

**The same warning belongs on `INGESTION_CONSUMER=1` and `CRM_ZOHO_SYNC=1`**, which the leak
audit surfaced: both write unscoped rows *unattended*, and each is one environment variable
away from doing so. The ClickUp import is merely the one with a button.

---

## 3. The decisions

### D-MT-1 — Can one person belong to more than one organization?

`DECISION (owner-delegated 2026-08-08).` Put to the owner with both options costed; the
answer was *"go ahead with what you think is right"*, so the recommendation below was
taken as the decision. **ANSWERED: (a) — one person, one organization, for v1.**
Everything else in this document is downstream of it.

**This is the reversible direction, which is why it was safe to take.** (a) → (b) is a
migration plus an org-switcher, run once, while accounts are few. (b) → (a) takes a
capability away from people already using it. Given a delegated choice between a door
that stays open and one that closes, the open one wins — and the moment the product is
sold to an agency or a consultancy, revisit this before the first such tenant onboards
rather than after.

* **(a) One person, one organization.** `app_user.email` stays globally unique. A request's
  tenant is *derived* from `X-User-Email`, so the identity seam every app already reads does
  not change shape — `resolve_visibility` grows one lookup and every query inherits the answer.
  **Cost:** a consultant working with two customer organizations needs two accounts with two
  email addresses. For an internal tool becoming a product this is normal; for an agency
  product it is a dealbreaker.
* **(b) One person, many organizations.** `UNIQUE(email)` becomes `UNIQUE(organization_id,
  email)`, and identity stops being resolvable from the email alone. **Every request needs a
  tenant discriminator** — a subdomain, a path segment, or a selected-org cookie — and that
  touches the auth seam of *every* app, not just Projects. It also reopens settled ground:
  `pm_project_grants.subject` and `pm_task_assignees.assignee` are bare emails (D-PM-4), and
  under (b) a bare email no longer identifies a person.

**Chosen: (a) for v1**, because it preserves the `X-User-Email` seam the whole
platform is built on and can be relaxed later behind an org-switcher, whereas (b) is a change
to identity itself and cannot be deferred once accounts exist. **The trigger to revisit is
named, not vague:** the first customer who needs one human in two organizations. Until then
`X-User-Email` alone resolves the tenant, and no app's auth seam changes.

### D-MT-2 — Where is isolation *enforced*?

`DECISION (agent-proposed, owner may overrule) — OPEN.`

* **(a) Row-level security.** Postgres RLS with `organization_id = current_setting('app.org')`,
  set per connection. The database refuses cross-tenant reads whether or not the application
  remembers to filter. **Cost:** every connection must set the GUC — including the ingestion
  workers, the broker, and the migration runner — and a missed `SET` fails closed, which is
  the right direction but is an outage rather than a leak.
* **(b) An application predicate**, exactly as `task_visibility_clause` works today.
  **Cost:** correctness rests on 143 tables' worth of query authors never forgetting, which is
  the discipline that produced 137 unscoped tables in the first place.
* **(c) A schema per tenant.** Strong isolation, no predicate anywhere. **Cost:** migrations
  run N times, and the connection pool multiplies. At single-digit tenant counts this is fine
  and at three digits it is a second full-time problem.

**Proposed: (a) RLS, with (b) kept where it already exists.** RLS is the only option where the
*absence* of code is safe rather than a leak — and given the measured 137, absence of code is
the failure mode this system actually has. `task_visibility_clause` stays: RLS decides *which
tenant*, grants decide *which projects within it*, and those are different questions.

### D-MT-3 — `organization_id` on the row, or reachable through a parent?

`DECISION (agent-proposed, owner may overrule) — OPEN.`

`pm_tasks` already has `root_project_id` denormalised precisely so scope checks need no
recursive walk (migration 146). The same argument applies one level up: **carry
`organization_id` on every tenant-owned table**, even where it is derivable.

**Rejected:** deriving it through the parent chain. RLS policies cannot afford a join, a
derived key cannot be indexed usefully, and "derivable" stops being true the moment a row's
parent is nullable — which `pm_tasks.parent_task_id` already is (`ON DELETE SET NULL`).
**Cost:** the column must be kept true on write, which is one more thing an `INSERT` can get
wrong; a `CHECK` against the parent's value is the cheap guard.

---

## 4. The ratchet, in place now

`tests/unit/test_tenancy_boundary.py` freezes the 137 and fails any **new** table without
`organization_id`, on the model of the frontend's `conformance.test.ts`:

* a table not in the baseline must carry a tenant key;
* a baselined table may stay as it is;
* a baselined table that *gained* one fails until it is removed from the baseline, so the
  figure never quietly becomes fiction.

It reads the migrations — including `ALTER TABLE … ADD COLUMN organization_id`, which is how
`app_user` got its key and which a `CREATE TABLE`-only scan misses — and its output was
checked against a live Postgres before it was written. Its purpose is **not** to demand the
retrofit. It is to stop the number growing while D-MT-1 is answered, because every table added
between now and then is another backfill.

---

## 5. Proposed sequence

| | Ticket | Depends on |
|---|---|---|
| 1 | ~~**WS-29a**~~ ✅ **BUILT** — migration 161. ⚠️ The tables were *nearly* empty, not empty; it backfills | ~~D-MT-1~~ ✅ (a) |
| 2 | ~~**WS-29b**~~ ✅ **BUILT** — plus three leaks it exposed, incl. `/assigned-to-me` having no visibility clause at all | ~~WS-29a~~ ✅ |
| 3 | **WS-29c** — RLS policies and the connection-level GUC, behind a flag, off | D-MT-2 |
| 4 | **WS-29d** — the remaining 120 tables, by family, largest blast radius first | WS-29c |
| — | **WS-27g's ClickUp import** | ~~after WS-29a~~ ✅ **unblocked** — but apply 161 to the target DB first |

**WS-29a is the only urgent one**, and only because of the import. The rest can proceed at
whatever pace the product needs.

---

## 6. What is already right, and should not be redone

Worth stating so the retrofit does not churn it:

* **`organization` exists and is referenced correctly** where it is used at all — `app_user`,
  `org_group`, `org_role` all `REFERENCES organization(id) ON DELETE CASCADE`.
* **Projects has one visibility seam.** That is the property making its retrofit a day rather
  than a month; it should survive intact, with the tenant predicate composed *above* the grant
  closure rather than tangled into it.
* **The grant vocabulary (`email | group:<slug> | org`) is tenant-shaped already** — except
  that the `org` literal means "everybody", and under multi-tenancy it must mean "everybody in
  *this* organization". That is one clause, in one query, and it is the single most dangerous
  line in the retrofit: today it is correct, and after the first second tenant onboards it is a
  cross-tenant leak.
