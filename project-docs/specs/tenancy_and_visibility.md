# Tenancy and visibility — who can see what

**Status:** Architecture of record for **visibility (§2–§5)**. ⚠️ **§1 and §6 (tenancy) were
re-taken on 2026-08-08 — see [`saas_multitenancy.md`](saas_multitenancy.md) §1.** ·
owner-answered 2026-08-03 · **Date:** 2026-08-03 ·
**Verified against code:** 2026-08-03, **re-verified and corrected 2026-08-03** against
`ws-14-doc-remediation` (parent `bebbd924`) · **Owner:** vjvarada

> **Correction pass, 2026-08-03 (WS-14 doc remediation).** The first header claimed
> every claim was "re-measured against the tree at `520476ab`". **Six anchors were wrong
> at that commit** and are fixed here — §2 TV-1 anchors a and b, §3.2's repeat of anchor
> a, §1.1 leak-sites 4 and 5, and the §5 Agents row's *"nothing writes it"*, which was
> factually false (`acb_skills/manifest.py:245` writes `t:<team>`; what is absent is a
> shipped config that asks for it — so team instancing may need **config, not code**).
> Anchor c (`access.py:330-336`) was correct and is unchanged. Re-measured with
> `grep -n` against the working tree on the date above; every corrected line number is
> reproducible with the §7 commands. **Read the numbers here, not the ones any other doc
> quotes** — `work_plan.md` D11/D12 and the WS-14 row carried the same stale ranges and
> were corrected in the same change.

> **Repair round, 2026-08-03 (review REQUEST-CHANGES on the pass above).** Three fixes
> here. (1) **`_load_room_row` does not exist** — §2 anchor a and §2 done-when 2 named
> it; the function is **`_load_room`** (`gateway/rooms.py:149`), and `_load_room_state`
> (`routes/rooms.py:118`) is a *different* function, so the wrong name pointed at real
> but unrelated code. The line ranges were right; only the symbol was wrong.
> (2) **WS-14a done-when 2 had no home its own command could reach** — §7 names
> `test_app_grants.py` and `test_org_access_control.py` as the wholly hermetic files,
> but done-when 5's command listed only the two `skipif` files, so quoting it verified
> the DB-backed half and nothing else. Done-when 2 now names
> `tests/unit/test_org_access_control.py` and done-when 5's command includes it.
> (3) **§4.1's "one shared validator" named no module, and no shared home exists** —
> it now names `packages/acb_auth/acb_auth/permissions.py` and justifies it, drops
> `role` from the proposed schema, records the previously-unconsidered "reuse
> `app_grants`" alternative, and cross-references the board as **D13**.

**Purpose.** Two audits asked whether the multi-tenant foundation is complete. It is
not built, and the owner's answer is that it should not be. This document records
that decision and the visibility model that replaces it, so the next cycle builds
against a written architecture instead of re-deriving one per app. It is the single
owner for "who can see what" (`work_plan.md` §4).

**Nomenclature (R3).** The owner says "department". The board and the code say
**Center**. Throughout this document:

> **department = Center = an `org_group` row.** One slug, three namespaces
> (`center.<slug>` feature · `/centers/<slug>` route · `org_group.slug`), zero
> mapping tables — `department_centers.md` §1. Write "Center". Never introduce a
> `department` table, column, or feature slug.

---

## 1. DECISION — the tenant boundary is the deployment ⚠️ **SUPERSEDED 2026-08-08**

> ### ⛔ **RE-TAKEN. Read `saas_multitenancy.md` §1 instead.** *(owner-requested 2026-08-08)*
>
> **The reason: the business model changed.** Metorite is being sold to external
> customers, priced per module, per user, per month, plus metered AI. §1.4 of that
> document shows that price point and one-VM-per-customer are arithmetically
> incompatible, and §1.3 shows why the cost objection recorded in §1.2 below no longer
> holds: because `packages/acb_common/acb_common/db.py` is a **single** engine and a
> **single** `get_db()`, tenant scoping installs at one seam with Postgres RLS and
> **zero existing queries change** — the "a `WHERE organization_id = ?` on 111 tables"
> framing below was measured against an assumption, not against that seam.
>
> **The new decision:** *tenant = `organization_id`, enforced by Postgres RLS at the
> connection seam; the deployment is a placement (region/tier), not a tenant boundary.*
> A dedicated database or dedicated stack survives as a **priced enterprise tier**, which
> is what §1.2's cost analysis below is now the pricing input for.
>
> **§6 of this document is superseded with it** — row-level tenancy, an org switcher and
> multi-org users are now all in scope, per `saas_multitenancy.md` §1.5.
>
> **Everything else in this document survives unchanged and is still binding:** the
> visibility ladder (§3), the project-grant decision (§4), and the per-surface gap table
> (§5). Tenancy and visibility are different axes — tenancy is *which company*, visibility
> is *who inside that company* — and `saas_multitenancy.md` §7.8 restates §3.2's
> standing rule against a second scoping doctrine.
>
> ⚠️ **What un-mootedness costs.** §1.1 below concludes that leak sites 1–10 "cannot fire"
> because there is one `organization` row. Under the new decision **that premise is gone**
> and every one of them must be verified rather than assumed —
> `saas_multitenancy.md` §6.4 and §6.5 carry that list, and §6.1/§6.2 add two hard
> blockers (process-global credential injection; self-mutation writing to the shared
> monorepo) that must be fixed **before a second tenant exists at all**.
>
> The text below is retained verbatim as the record of the decision that was taken on
> 2026-08-03 and of why it was correct at the time. **Do not build against it.**

> ### `Tenant boundary = THE DEPLOYMENT.` *(owner-answered 2026-08-03 · superseded 2026-08-08)*
>
> One deployment per tenant. If a second organization ever exists it gets its own
> box, its own database, its own credential set. Row-level organization isolation
> is **explicitly not being built.**

This is the same rule `department_centers.md` already states from the other
direction ("a *separate deployment* is reserved for a separate organization, never
for a department") and the same rule D9 landed when it rewrote twelve "Pomad Centre"
sites as "a second tenant deployment". It is now the architecture of record rather
than an aside in three specs.

### 1.1 What follows from it

**`organization_id` stays as a label, not a mechanism.** It exists on **3 of 111
own tables** — `app_user` (added by `130_org_access_control.sql:56`), `org_role`
(`130:86`) and `org_group` (`138_groups_and_session_participants.sql:42`). Measured:
111 distinct tables are created by the numbered migrations in `infra/postgres/`
(the 152-name count you get from `schema.generated.sql` includes LiteLLM's and
Langfuse's vendored schemas, which are not ours). It is **read by zero
authorization decisions**: `acb_auth.deps` populates `UserContext.organization_id`
from `resolve_identity()` (`deps.py:155-157`, one extra `SELECT` per authenticated
request against `app_user`), and the only Python readers of the value are the
dataclass that stores it (`roles.py:109-111`) and the line that stores it. Every
`WHERE organization_id = :org` in the gateway binds `:org` from `get_org_id(db)`,
which is the hardcoded `slug = 'default'` lookup — **not** from the caller's
identity.

Under this decision that is **correct, not a bug**. Do not "fix" it by threading
`user.organization_id` into queries: that would be the first 5% of row-level
multi-tenancy, which §6 puts out of scope, and it would create a second scoping
doctrine alongside the one in §3.

**The leak sites are moot by definition, not by fix.** The 2026-08-03 audit
enumerated the places where a second `organization` row would serve org A's data to
org B. Verified samples, so a reader can judge the class:

| # | Site | What it does |
|---|---|---|
| 1 | `routes/admin/_common.py:96-112` | `get_org_id()` resolves the org by the literal `DEFAULT_ORG_SLUG = "default"` (`:36`) |
| 2 | `routes/admin/_common.py:115-129` | `get_member()` looks a member up by email with **no** org predicate |
| 3 | `routes/admin/members.py:170-178` | invite is `INSERT … ON CONFLICT (email) DO UPDATE SET organization_id = EXCLUDED.organization_id` — under two orgs this is an account-takeover primitive |
| 4 | `gateway/rooms.py:201-211` | the `in_org` check is `SELECT 1 FROM app_user WHERE email = :email AND COALESCE(status, 'active') = 'active'` (SQL at `:205-208`) — no org filter. *(Corrected 2026-08-03: the old citation `:184-190` and its `status='active'` quote were both wrong — `:184-190` is the `group_slugs` comprehension plus the head of the `my_groups` query, and the real predicate is `COALESCE`-wrapped.)* |
| 5 | `gateway/rooms.py:384-393` | `SESSION_VISIBLE_SQL`'s `org`-participant arm — an `EXISTS` on a `subject = 'org'` row `AND` an `EXISTS` on an active `app_user`, same shape, same absence. The adjacent `s.visibility = 'org'` arm at `:394-400` has it too. *(Corrected 2026-08-03 from `:346-356`, which is the tail of `resolve_room_access`'s return — `is_shared`, `members`, `visibility` — and not SQL at all.)* |
| 6 | `acb_auth/access.py:338-340` | `_ORG_MEMBER_SQL` is `SELECT email FROM app_user WHERE status = 'active'` — the `org` subject expands to *every* active user on the box *[Anchor stale: measured 2026-08-08 at access.py:400 / access.py:522 — re-derive with grep; see saas_multitenancy.md §6.4.]* |
| 7 | `infra/postgres/130_org_access_control.sql:180` | role seeding does `SELECT id INTO org_id FROM organization WHERE slug = 'default'` (same in `131:` and `133:`) |
| 8 | `acb_auth/access.py:439-458` | `_BOOTSTRAP_OWNER_SQL` hardcodes `slug = 'default'` |
| 9 | `acb_auth/access.py:460-464` | `_HAS_OWNER_SQL` is `SELECT 1 FROM user_role ur JOIN org_role r … r.slug='owner' LIMIT 1` — **no org filter**, so once *any* owner exists anywhere, `ensure_owner_bootstrap()` is a permanent no-op and a second org's users have no inviter *[Anchor stale: measured 2026-08-08 at access.py:400 / access.py:522 — re-derive with grep; see saas_multitenancy.md §6.4.]* |
| 10 | every app-data table | `gtd_items`, `email_*`, `meeting`, `apps`, `workflows`, `chat_session`, `agent_blob`, `mem` carry no org column at all — the bulk of the surface |

Under one deployment per tenant, **sites 1, 2, 3, 4, 5, 6, 7, 8, 10 cannot fire** —
there is exactly one `organization` row, so "the default org" and "the caller's org"
are the same set. Site 9 is the interesting one: it is not a leak, it is a
**lockout**, and it is the reason a second org on this box would not merely leak but
would be unusable. That is an argument *for* this decision, not a ticket against it.

The three joins in §2 are the exception: they are wrong **within one org too**, so
they survive this decision.

**Per-deployment credentials become correct rather than a gap.** Both audits filed
"credentials are deployment singletons" as a multi-tenancy defect. Verified:
`provider_keys` is keyed `provider TEXT PRIMARY KEY` (`08_provider_keys.sql:7`) —
one key per provider for the whole box; `mcp_servers`, `plugins` and `model_config`
have no owner/org column; and integration secrets reach agents by being written into
the **process-global** `os.environ` (`executor.py:4388`, restored at `:4411`). Under
one deployment per tenant, a deployment-wide credential store is exactly the right
shape. The residual is a *within-org* concern — per-member integration credentials
already ship (`org_access_control.md`), and per-run credential scoping is WS-3's
P5-a, not this document's.

### 1.2 What a second tenant actually costs

Stated so the choice stays honest rather than becoming a habit. A second tenant
needs, at minimum:

1. A second VPS (or at least a second isolated Postgres + Redis; `infra/` binds one
   `acb-postgres-data` volume and one `acb-redis-data` volume per stack).
2. A second database, migrated from zero — `scripts/apply_migrations.sh` replays
   every numbered migration from `02_` upward on every deploy, so a fresh box gets
   the full ladder (140 files today; `00_`/`01_` are initdb-only).
3. A second credential set: provider keys, integration OAuth clients, webhook
   secrets, `GATEWAY_INTERNAL_TOKEN`, `LITELLM_MASTER_KEY`, encryption key.
4. DNS + TLS + a second systemd unit set (`deploy/hostinger/` carries four units
   plus the generated `acb.service`).
5. A second deploy pipeline target, or a parameterised one.

That is roughly a day of owner-gated work and a permanent second thing to patch. It
is **not** free — but it is bounded, auditable and does not put a `WHERE
organization_id = ?` on 111 tables and every query in the gateway. The trade is
recorded here so a future reader can re-take it deliberately.

**Constraint this places on new work.** Non-negotiable #3 in the root `AGENTS.md`
already says native-MAF self-mutation must be swapped for a tenant-isolated
mechanism "before any multi-tenant/customer deployment". Under this decision that
condition is satisfied by construction *for the first tenant* and becomes a
provisioning checklist item for any second one. Do not read this decision as
retiring that constraint.

---

## 2. TV-1 — the three leaks that survive this decision *(AGENT-SAFE · 1 small PR)*

> **Board row: `work_plan.md` §2 · WS-14a.** Until 2026-08-03 this ticket existed only
> here, and §4 assigned it to a *spec*, not a workstream — so the corpus's most
> dispatch-ready item could not be dispatched, because the loop selects from §2. It now
> has a row. It is numbered **WS-14a** rather than a fresh WS-n because it is the
> `org_group`-join half of the same subject-vocabulary surface WS-14 generalises: TV-1
> makes the `group:` expansion correct, WS-14 spreads it to more surfaces. They are
> independent PRs and either may land first.

`org_group` is joined on **slug alone** at three places. Slug is unique only
*within* an organization — `UNIQUE (organization_id, slug)`
(`138_groups_and_session_participants.sql:49`) — so a slug-only join is a
cross-organization match by construction. Under §1 there is one org today, so
nothing leaks today. *[2026-08-09: premise retired by D15 — with a second
organization these three joins leak across tenants, which is why WS-29 absorbed
TV-1 as MT-1i. The done-whens below stand verbatim.]* They are on this list for two reasons: they are three
one-line predicates now and an archaeology project later, and **two of the three
sit inside the session-authority intersection**, which is the single most
consequential access computation in the codebase (`groups_sessions_authority.md`
§3 — a shared run acts with the *intersection* of every participant's access).
Getting a wider group than intended there widens, it does not narrow.

| Anchor | Symbol | The join |
|---|---|---|
| a | `apps/services/gateway/gateway/rooms.py:181-199` (the `SELECT g.slug` is at `:192`) | `SELECT g.slug FROM org_group g JOIN org_group_member m … WHERE u.email = :email AND g.slug = ANY(:slugs)` — inside **`_load_room`** (`rooms.py:149`), feeding `my_groups` |
| b | `apps/services/gateway/gateway/rooms.py:368-403` (`SESSION_VISIBLE_SQL` opens at `:368`; the slug join is at `:377`) | `SESSION_VISIBLE_SQL`: `JOIN org_group g ON g.slug = substring(p.subject from 7)` |
| c | `packages/acb_auth/acb_auth/access.py:330-336` | `_GROUP_MEMBER_SQL`: `WHERE g.slug = :slug AND au.status = 'active'` |

*(Anchors a and b were `:170-179` and `:332-340` until 2026-08-03 and were wrong at
`520476ab` too: `:170-179` is `if row is None` plus the participant fetch, and `:332-340`
is the tail of `resolve_room_access`'s return value. Anchor c was and is correct.
Re-derive all three with the `grep -n "org_group"` command in §7 before editing.)*

**Done when:**

1. All three queries carry an organization predicate resolved from the row being
   authorised, not from a literal — e.g. by joining `org_group.organization_id` to
   the acting user's `app_user.organization_id`, so the predicate is *derived* and
   cannot go stale when §1 is revisited. A hardcoded `slug='default'` join does
   **not** satisfy this: it swaps one wrong constant for another.
2. **The hermetic half — a test that cannot skip, verified red first.** Both files
   §7 names as the extension point open with
   `pytest.mark.skipif(not _db_ready(), …)` (`tests/unit/test_session_authority.py:33-51`,
   `tests/unit/test_rooms.py:33-52`), so a fixture added there **skips green** with no
   Postgres up and "verified red" is unsatisfiable against it. The red-first obligation
   therefore attaches to a test that needs no database: assert, as strings, that each of
   the three queries carries an organization predicate.
   - **Where it lives — named, because the mandated command in done-when 5 reaches
     neither skipping file's hermetic half:** `tests/unit/test_org_access_control.py`.
     §7 measures it as one of the two *wholly* hermetic files in this area (no DB, no
     skip, verified 2026-08-03), it already imports `acb_auth.access` where anchor c's
     constant lives, and `tests/unit/test_admin_groups.py` shows a gateway module being
     imported from a hermetic unit test, so pulling in `gateway.rooms` for anchors a
     and b is established practice. A **new** file is acceptable instead — but only if
     it carries no `_db_ready()` guard and is added to done-when 5's command; the
     default is the named file, so this criterion always has a home.
   - `SESSION_VISIBLE_SQL` (`gateway/rooms.py:368`) and `_GROUP_MEMBER_SQL`
     (`acb_auth/access.py:330`) are already module-level constants — import and assert
     on them directly.
   - Anchor a's query is an inline string inside **`_load_room`**
     (`gateway/rooms.py:149` — *the name was published as `_load_room_row` until
     2026-08-03; no such symbol exists, and `_load_room_state`
     (`routes/rooms.py:118`) is a **different** function, so the wrong name pointed
     at real but unrelated code*). **Lift it to a
     module-level constant** (e.g. `MY_GROUPS_SQL`) as part of this PR, so the same
     assertion reaches all three. That extraction is the reason this criterion is
     buildable rather than a wish.
   - Verified **red** before the fix: run it against the current joins and quote the
     failure. A string test that passes on today's code is testing nothing.
3. **The behavioural half — DB-backed, and it must be shown to have actually run.**
   A fixture seeds **two** `organization` rows and two identically-slugged `org_group`
   rows (one per org) with disjoint members, and asserts that `resolve_session_access`
   for a room whose participant subject is `group:<slug>` expands to **only** org A's
   members, and that `rooms.py`'s `my_groups` set and `SESSION_VISIBLE_SQL` do not
   admit the org-B member. This belongs beside the existing `_needs_db` tests and will
   carry that marker. **Because it can skip, the PR must quote a run in which it did
   not:** the pytest output for the named node ids must read `passed`, never `skipped`
   or `no tests ran`. `-q` output that shows only a summary line does not discharge
   this — use `-v` or `-rs` so the skip reasons are printed.
4. `uv run ruff check <the files you touched>` is clean. Do **not** write
   "`uv run ruff check .` clean" — that command reports ~1983 pre-existing errors on
   this tree and is not a signal.
5. **Verification commands** (name the files; never `pytest tests/unit/` as a
   directory — whole-directory collection hangs against this box's live DB):
   ```
   uv run pytest tests/unit/test_session_authority.py tests/unit/test_rooms.py \
                 tests/unit/test_org_access_control.py -v -rs
   uv run ruff check apps/services/gateway/gateway/rooms.py \
                     packages/acb_auth/acb_auth/access.py
   ```
   The third file is **not optional garnish** — it is the only one of the three that
   runs done-when 2. The first two both `skipif` without a database, so quoting a run
   of just those two verifies the DB-backed half and *nothing else*. If done-when 2
   was placed in a new hermetic file instead, name that file here too.

**Non-goals.** Do not add `organization_id` to any other table, do not thread
`UserContext.organization_id` into unrelated queries, and do not touch sites 1–10 in
§1.1. This ticket is three predicates.

**Related, not duplicated.** Three *live* access defects — Notes readable/deletable
by any colleague, an identity-trust fallback, and a room fail-open — are being fixed
in **PR #346** (`ws-0-live-access-defects`, "fix(access): three live defects — Notes
was readable, deletable and sendable-as by any colleague"). TV-1 is not those, and
neither should absorb the other.

---

## 3. DECISION — the visibility model *(owner-answered 2026-08-03)*

> **Owner, verbatim:** *"Sensitive services are private, and we should also have
> department-wise privacy so that the sales team cannot see what the finance team is
> doing. At the same time, we can have organizational-level sharing as well."*
>
> *"Ideally we would have department-wise isolation. At the same time we can share
> some things across departments. At the same time, create projects and groups where
> information can be shared between select users of different departments, depending
> on invite or sharing settings."*

### 3.1 Three tiers, plus invite

| Tier | Means | Subject that expresses it |
|---|---|---|
| **private** | the owning member only | the member's `email` |
| **Center** | one Center's members — "sales cannot see finance" | `group:<slug>` |
| **org** | every active member of the deployment | `org` |
| **ad-hoc cross-Center group** *(by invite)* | a named set spanning Centers, for a project | an `org_group` row that is **not** one of the six Center groups, addressed the same way: `group:<slug>` |

The fourth row is not a fourth mechanism. A project group is an ordinary `org_group`
whose slug does not pair with a `center.*` feature — the code already distinguishes
the two: `routes/admin/groups.py:37` holds the six Center slugs and `:65` exposes an
"is this a Center group" flag precisely so the UI can treat the rest as ordinary
groups. Membership is by invite, which is what the owner asked for, and it is the
same `org_group_member` table.

### 3.2 The primitive already exists — generalise it, do not reinvent it

`chat_session_participant.subject` uses exactly this vocabulary today.
`routes/rooms.py::_valid_subject` (`:100-111`) accepts `org` · `group:<slug>` ·
an email, and `chat_session.visibility` is `CHECK (visibility IN ('private',
'people', 'org'))` (`138_…sql:83`) — the same three tiers, in shipped code, with
group membership resolved at read time (`gateway/rooms.py:181-199` — corrected
2026-08-03 from `:163-179`, the same stale range as §2 anchor a) rather than
denormalised.

**Correction to the framing that reached this document.** The brief asserted that
`app_grants` uses the same vocabulary. It does not, and the code says so at both
ends:

- `routes/apps/grants.py::is_valid_subject` (`:68-85`) is `email | agent:<name> |
  agents:*` and **explicitly rejects the literal `org`** (`:77`) with the rationale
  that `apps.visibility='org'` already means it. It has **no** `group:` case.
- `routes/rooms.py::_valid_subject`'s own docstring claimed it was *"Identical to
  `routes/apps/grants.is_valid_subject` on purpose."* **That was false** — the two
  functions are disjoint on `org`, `group:` and `agent:` — and it was **corrected
  2026-08-03** in a docstring-only edit (no executable line touched). The replacement
  was written to be **line-count-neutral**, so `_valid_subject` still spans `:100-111`
  and the correction still sits on `:103`: every anchor this document, `work_plan.md`
  D12 and `docs/multiplayer/memory-clearance.md` publish into that file remains valid.
  Keep that discipline when editing prose inside heavily-cited modules — an anchor-only
  fix that silently moves eight other anchors is a net regression.

So the primitive is **rooms-only** today. Apps have the *tier* vocabulary
(`private|people|org`, `114_custom_apps.sql:30-31`) but a grant subject that cannot
name a Center. The work is to extend the one grant model outward — which is a
smaller job than designing an ACL, and a larger job than "it's already there".

**Standing rule for reviewers:** a second scoping doctrine in this codebase is what
produced the Notes hole. When a surface needs sharing, it adopts
`email | group:<slug> | org` and resolves group membership at read time. It does not
invent `shared_with_department`, a `visible_to` array, or a per-app grant table with
its own subject grammar.

### 3.3 Which surfaces are private by default

**Private by default — a grant is required to widen them:**

- **Email** — `email_accounts.user_id` (`17_email_accounts.sql:16`, "CC user who
  owns this connection"). A mailbox is one person's until a shared-mailbox grant
  exists (owned by `email_app_master_plan.md`, sequenced by WS-14, per D5).
- **Tasks / GTD** — `gtd_items.user_id TEXT NOT NULL`
  (`48_task_manager_gtd.sql:91`), filtered on 27 query sites in `routes/tasks/items.py`.
- **Notes / meetings** — `meeting.owner_email` (`95_note_taker.sql:38`). Nullable, and
  **filtered on read since PR #346 merged as `d2ef7fa0` (2026-08-03)** — the predicate
  lives once in `routes/notes/core.OWNED_MEETING_PREDICATE` and is applied both in the
  list SQL and in `load_owned_meeting`. Private is now the true tier, not just the
  intended one. Rows with a NULL `owner_email` (pre-migration-95 legacy) stay visible to
  every feature holder by deliberate exception — see `routes/notes/meetings.py:1-27`.
- **Memory (personal)** — the `<email>` scope; `prefs:` likewise.

**Shareable, with the tier stated on the row:**

- **Chat / rooms** — `visibility ∈ private|people|org` + participant subjects.
- **Custom Apps** — `apps.visibility ∈ private|people|org` + `app_grants`.

**Org-wide by construction today (a deliberate posture, recorded so it is not
mistaken for an oversight):**

- **Workflows** — `crud.py:5` states it outright: *"`owner_email` is attribution,
  not access."* The list query is `SELECT … FROM workflows w ORDER BY w.updated_at
  DESC` (`crud.py:91`) with no owner predicate, and delete is `DELETE FROM workflows
  WHERE id = :id` (`:346`). Anyone holding `feature:workflows` sees and can delete
  every workflow. That is fine for an internal tool with one org; it is the first
  thing that must change if a Center wants a private automation. *[2026-08-09:
  under D15/WS-29 this is now scheduled work, not an accepted posture — see
  saas_multitenancy.md §2 (entitlements) and MT-1b.]*
- **Memory `org:global`** — org-wide by definition.

**A new surface must declare its tier.** This is the doctrine that stops each new
app guessing. Concretely, for a reviewer:

> A PR that adds a persisted user-facing surface names its default tier in the
> migration header and either (a) carries an owner column and filters on it, or
> (b) carries `visibility` + a subject grant table using §3.2's vocabulary, or
> (c) states in the header that it is intentionally org-wide and why. "It inherits
> the app's tier" is not one of the three.

---

## 4. DECISION — what "a project belongs to a team" means

`DECISION (owner-answered 2026-08-03)`

This semantic has blocked **WS-14 Centers C** for weeks. It resolves to:

> **A project belongs to a team when an explicit grant row carries a
> `group:<slug>` subject for that project.** Not derived from who is assigned to
> its tasks, and not an owning column on the project row.

**Why an explicit grant.**

1. It is the same mechanism as §3.2, so a Center project, a cross-Center project
   group, and an org-visible project are one code path with a different subject.
2. It is revocable as a distinct act. Removing a grant is visible in an audit log;
   re-assigning tasks to change who can see a project is not.
3. It composes with the intersection rule. `resolve_session_access` already expands
   `group:` subjects at read time; a project grant slots into that expansion without
   a second resolver.

**Alternative rejected — derive it from assignees** ("a project belongs to whichever
team its assignees are in"). Rejected because access would then be a side effect of
task assignment: assigning one finance colleague to a sales project would silently
admit all of finance, and *un*assigning the last member of a team would silently
revoke a whole Center's access to a project mid-flight. Access must be an act, not a
consequence.

**Alternative rejected — an owning `group_id` column on the project row.** Rejected
because it is single-valued: it cannot express the owner's third requirement ("share
between select users of different departments"), so the cross-Center project case
would need a *second* mechanism the day after it shipped. A grant table is
single-valued when it has one row.

### 4.1 Where the project grant table lives *(the first decision the tasks slice makes)*

`DECISION (agent-proposed, owner may overrule) — 2026-08-03.` Registered on the board as
**D13** (`work_plan.md` §3) so it is discoverable without reading this section.
§8 used to defer this
("`gtd_*` vs a shared `object_grants`… WS-14's design call"), which made the tasks team
slice undispatchable: acceptance cannot name a table the implementer is also being asked
to invent. It is decided here so it stops blocking, and recorded as overrulable so the
owner keeps the call.

> **A `gtd_*`-local table — `gtd_project_grant (project_id, subject, granted_by,
> created_at)` — not a polymorphic `object_grants`, and not `app_grants`.**

**No `role` column.** The draft carried one, copied from `app_grants.role`
(`114_custom_apps.sql:61-62`). Cut 2026-08-03: `app_grants.role` is read by `can_edit`
(`routes/apps/_common.py:473`), whereas every clause of the tasks slice's acceptance
(`department_centers.md` C1) is a **read**-path clause, so a project grant's role would
have one legal value and no reader. Write-through-grant ("may a grantee edit items in a
granted project?") is a real, *unanswered* question; when it is answered it arrives as
`ALTER TABLE … ADD COLUMN role` at the next free migration number (R1), additive and
backfill-free. See C1's boxed note for the full reasoning.

**Why.** `app_grants` already exists and is per-surface. A shared `object_grants` would
therefore be a *second* grant shape on day one unless it also migrated `app_grants` —
which is out of any tasks ticket's scope, so the "one table" argument buys nothing it
promises. A local table also keeps referential integrity (a real FK onto
`gtd_projects`), which a polymorphic `(object_type, object_id)` key cannot have. What
must **not** fork is the *subject grammar*: §3.2's standing rule forbids "a per-app grant
table with its own subject grammar", not a per-app grant table. One shared validator
accepting `email | group:<slug> | org` satisfies the rule; two validators would not.

**Where that shared validator lives — `packages/acb_auth/acb_auth/permissions.py`,
exported from `acb_auth/__init__.py`.** Named here because "the shared validator" named
no module and **no shared home exists**: the only two subject validators today are
route-local and disjoint, `routes/rooms.py::_valid_subject` (`:100-111`) and
`routes/apps/grants.py::is_valid_subject` (`:68-85`), so an implementer told to reuse
"the shared one" would have had to import a *private* symbol across route packages and
the criterion would still have read green. `permissions.py` is the right home because it
already owns the permission vocabulary, is **pure by contract** (no DB, no FastAPI, no
I/O — `permissions.py:1-8` and `packages/AGENTS.md`), and every consumer already imports
`acb_auth`, so it adds no import-graph edge. The tasks slice creates it and is its first
caller; converting the two existing validators to compose with it is a **named follow-on**
(their grammars differ, and `rooms.py:100-111` is an anchor four documents publish —
see the line-count-neutrality rule in §3.2). Acceptance: `department_centers.md` C1
done-when 5.

**The third alternative, now that it has been asked: reuse `app_grants` itself.**
Rejected, and not on taste — on its key. `app_grants` is
`app_id UUID NOT NULL REFERENCES apps(id) ON DELETE CASCADE`, `PRIMARY KEY (app_id,
subject)` (`114_custom_apps.sql:58-67`). A project is not an app, so reuse means
dropping that FK and renaming the column to something polymorphic — which *is* the
`object_grants` option, arrived at by mutating a live table that four Custom-Apps code
paths read (`grants.py`, `_common.load_grants`, `lifecycle.list_apps:299-301`,
`can_view`/`can_edit`) instead of by creating a new one. It is strictly the worse way to
reach the same place: same loss of referential integrity, plus a migration on shipped
data. If the owner wants one grant table, take the `object_grants` route below, not this
one.

**What the alternative costs, stated so the overrule is informed.** `object_grants`
would mean: one expansion helper for every future surface (Notes, Workflows) instead of
one per surface; but no FK, an index per `object_type`, a platform-level migration
decision inside an app ticket, and `app_grants` left as an unmigrated exception. If the
owner takes it, take it as its own ticket that *also* migrates `app_grants` — not as a
side effect of the tasks slice.

**What WS-14 can now build.** The `dynamic_agents` sharing columns (D3) and the
tasks team slice both depend on this answer, and both now have one. Note the
verified constraint: `dynamic_agents` today has **no** owner, visibility or sharing
column (`15_dynamic_agents.sql:7-20`; grep for sharing/visibility/owner across
`infra/postgres/[0-9]*.sql` returns nothing), so WS-14 owns that migration — at the
**next free number at build time**, never a number written into a doc (R1).

---

## 5. Gap table — the map for going app by app

Each row verified against code on 2026-08-03. "Honours `group:`" means: a
`group:<slug>` subject can be granted on this surface and is expanded at read time.

| Surface | Storage of record | Current scoping | Honours `group:`? | What it would need |
|---|---|---|---|---|
| **Chat / rooms** | `chat_session`, `chat_session_participant` | `user_id` owner + `visibility ∈ private/people/org` + participant subjects `email\|group:\|org` | **Yes** — the only one | Nothing. This is the reference implementation. Fix TV-1's two joins here. |
| **Tasks / GTD** | `gtd_items` (+ `gtd_projects`, `gtd_spaces`) | `user_id TEXT NOT NULL`, filtered on every read | No | A grant table keyed on the project (per §4) and a read path that unions "mine" with "granted to a group I'm in". The 27 `user_id` predicates in `items.py` are the blast radius. |
| **Email** | `email_accounts` (+ ~20 `email_*` tables hanging off it) | `email_accounts.user_id` | No | Shared mailboxes = a grant on the *account* row, not on messages. Owned by `email_app_master_plan.md` (D5). Per-member provider credentials already exist. |
| **Notes / meetings** | `meeting`, `transcript_segment`, `meeting_note` | `meeting.owner_email` (nullable, `95_note_taker.sql:38`), **filtered on read since `d2ef7fa0` / PR #346** via `routes/notes/core.OWNED_MEETING_PREDICATE` | No | **The owner filter has landed; a grant table is the remaining work.** Sharing is now safe to add on top — it would have been decoration before. `routes/notes/meetings.py:17-19` already commits the surface to this document's vocabulary (*"Sharing, when it comes, is the `subject` vocabulary the rooms layer already speaks… deliberately NOT invented here"*), so this is an adopt, not a design. |
| **Agents** | `dynamic_agents` + `agent_skill_setting` | No owner/visibility column at all; run rights via the `agents:run:<name>` permission | No | D3's sharing columns (WS-14, next free migration number). ⚠️ **Corrected 2026-08-03 — this row previously said `t:<team>` exists "but nothing writes it". That was false.** The writer exists and is wired: `AgentManifest.instance_key()` returns `f"t:{self.sharing.team}"` when `sharing.instancing == "team"` (`acb_skills/manifest.py:242-246`), and it has **four non-test call sites** — `manifest.py:256` (`memory_scope`), `:261` (`blob_instance`), `orchestrator/executor.py:935` (`_resolve_agent_instance`), `gateway/routes/workspace.py:256` (`_agent_instance_for`) — pinned by `tests/unit/test_agent_paths.py:164-201`. The true statement: **no shipped agent config sets `instancing: "team"`, so no `t:` value is produced today** (measured: the six `apps/agents/*/config.json` are four `shared` + two `personal`). Consequence for WS-14: **team instancing may be a config change, not a code change** — do not scope a build for a writer that already runs. |
| **Memory** | Mem0 (pgvector), keyed by scope string | Five scope shapes: `<email>` · `prefs:` · `room:` · `agent:` · `org:global` (`routes/memory.py:16-56`) | No — `room:` is the nearest thing | A `group:` scope shape, or the `subject:` compartments already specified as **WS-10 S1** (`docs/multiplayer/memory-clearance.md` §7.1). Do not add a sixth shape independently of that slice. |
| **Workflows** | `workflows`, `workflow_versions`, `workflow_triggers` | `owner_email` is **attribution only**; list query has no owner predicate (`crud.py:91`), delete has none (`:346`) | No | A `visibility` column + grants, if a Center ever wants a private automation. Until then, record the org-wide posture rather than assuming it. |
| **Apps / blobs** | `apps` + `app_grants`; `agent_blob` + `agent_file_history` | `apps.visibility ∈ private/people/org`; `app_grants.subject ∈ email\|agent:<name>\|agents:*` — `org` explicitly rejected (`grants.py:77`); `agent_blob.instance ∈ ''\|u:<email>\|t:<team>` | No | Add `group:<slug>` to `is_valid_subject` and expand it at read time, mirroring `rooms.py`. Shape measured — see §5.1. Fix the false "identical to grants.is_valid_subject" docstring at `rooms.py:103` in the same change *(done ahead of it, 2026-08-03, as a docstring-only edit — the claim was actively misleading implementers)*. |

**Reading the table.** Rooms is the reference. Apps is the cheapest next
conversion (it already has the tiers; it is missing one subject case). Tasks is the
one §4 unblocks. Notes's owner filter has landed (`d2ef7fa0`), so its remaining work
is a grant table. Workflows is a posture decision before it is a code change.

### 5.1 The Apps conversion, measured *(so it is not re-derived)*

`GET /apps` is `list_apps` at `routes/apps/lifecycle.py:289-337`. It already pulls
**every** grant in one ungrouped statement — `SELECT app_id, subject, role FROM
app_grants` (`:299-301`) — buckets them into `by_app` (`:321-323`) and decides
visibility **in Python**, `can_view(row, user, grants)` at `:331`. There is no
per-subject SQL predicate to widen, which is why this surface is cheap: adding
`group:` needs

1. the `group:<slug>` case in `is_valid_subject` (`routes/apps/grants.py:68-85`);
2. the caller's group set resolved **once per request** — one extra query mirroring
   `gateway/rooms.py:181-199`'s `my_groups`, not one per app;
3. `can_view` widened to accept a grant whose subject is a group the caller is in.

**A correction to the framing that reached this document.** It was asserted that this
also requires *inverting* `tests/unit/test_app_grants.py::test_invalid_subjects_rejected`'s
parameter list. Measured 2026-08-03: **it does not.** That list is
`org · "" · not-an-email · has space@x.com · @nouser.com · trailing@ · agent: ·
agent:has space · agent:has/slash · agents: · <300 chars>@x.com` — **no `group:` case
is pinned as invalid anywhere in that file.** So the test work is *additive* (a new
param in `test_valid_subjects_accepted`), which makes this cheaper still — and means an
implementer must not assume a red test will catch them: nothing currently fails if
`group:` support is half-built. Note the separate hazard that **is** real: `org` **is**
pinned invalid at `:49`, deliberately (`apps.visibility='org'` already means it), so
this conversion adds `group:` **only** and must leave `org` rejected.

---

## 6. Explicitly out of scope ⚠️ **SUPERSEDED 2026-08-08 — all four items are now IN scope**

> ⛔ **Re-taken by `saas_multitenancy.md` §1** (owner-requested 2026-08-08), by exactly the
> procedure the closing line of this section prescribes. Items 1–4 below are now queued
> work, not prohibitions:
>
> | Was out of scope | Now |
> |---|---|
> | 1. Row-level multi-tenancy | **The mechanism.** `organization_id` + `FORCE ROW LEVEL SECURITY` on every table, bound at the `get_db()` seam with `SET LOCAL app.tenant_id` — `saas_multitenancy.md` §1.3 |
> | 2. An org switcher | **Subdomain-resolved tenant**, bound to the authenticated session. Never a client-settable header — §1.5 |
> | 3. Users belonging to multiple orgs | **Supported**, via a global `user_identity` + `org_membership` split; RLS is what makes it cheap — §1.5 |
> | 4. Per-org credentials inside one deployment | **Required.** `provider_keys` becomes `(organization_id, provider)` — §6.3, and it is a *blocker*, not a nice-to-have |
>
> The text below is retained as the record of what was decided on 2026-08-03.

Named so nobody builds them, and so a future audit does not re-file them as gaps:

1. **Row-level multi-tenancy.** No `organization_id` on further tables, no RLS
   policies, no org predicate threaded through app queries. §1 replaces it.
   (`multi_user_organization_research.md` §9's entity-graph RLS and §17's SaaS
   tenancy stay research, and are superseded for planning purposes by this
   document.)
2. **An org switcher.** No UI, no route, no `X-Organization` header, no
   "current org" in session state. One deployment serves one org; there is nothing
   to switch to.
3. **Users belonging to multiple orgs.** `app_user.email` is globally unique
   (`ON CONFLICT (email)` at `members.py:173` and `access.py:447` both depend on
   it). Multi-org membership would require breaking that uniqueness, which would
   ripple into every identity lookup. Not being done.
4. **Per-org credentials inside one deployment.** Credentials are per-deployment by
   §1.1. Per-*member* integration credentials already ship and are a different
   thing.

If any of these is ever wanted, the correct move is to re-take the §1 decision
first, in this document, with a date and a reason — not to build one of them as a
side effect of an app ticket.

---

## 7. Verification

Hermetic; no live DB, no prod reach. Each command reproduces a claim above.

```
# §1.1 — three tables carry organization_id, out of 111 own tables
grep -rn "organization_id" infra/postgres/*.sql
ls infra/postgres/[0-9]*_*.sql | wc -l        # 142 numbered migration files

# §1.1 — the hardcoded org slug and the ownerless-bootstrap no-op
grep -n "DEFAULT_ORG_SLUG" apps/services/gateway/gateway/routes/admin/_common.py
grep -n "_HAS_OWNER_SQL" -A 5 packages/acb_auth/acb_auth/access.py

# §2 — the three slug-only joins
grep -n "org_group" apps/services/gateway/gateway/rooms.py \
                    packages/acb_auth/acb_auth/access.py

# §3.2 — the two subject vocabularies, and that they differ
grep -n "def _valid_subject" -A 12 apps/services/gateway/gateway/routes/rooms.py
grep -n "def is_valid_subject" -A 18 apps/services/gateway/gateway/routes/apps/grants.py

# §5 — the surfaces' owner columns
grep -rn "user_id\|owner_email\|visibility" infra/postgres/48_task_manager_gtd.sql \
   infra/postgres/95_note_taker.sql infra/postgres/114_custom_apps.sql \
   infra/postgres/132_workflows.sql infra/postgres/138_groups_and_session_participants.sql
```

Test files that already exercise this area, and are the right place to extend
(**name the file — never run `tests/unit/` as a directory on the Windows box**):
`tests/unit/test_session_authority.py`, `tests/unit/test_rooms.py`,
`tests/unit/test_org_access_control.py`, `tests/unit/test_owner_bootstrap.py`
(⚠️ never against prod).

> ⚠️ **Two of those four skip green without a database.**
> `test_session_authority.py:33-51` and `test_rooms.py:33-52` both open with
> `_needs_db = pytest.mark.skipif(not _db_ready(), …)`, where `_db_ready()` probes
> `chat_session_participant` / `org_group` / `chat_message` and returns `False` on any
> exception. A fixture added to either file **cannot be verified red** on a box with no
> Postgres up — it will report `skipped` and a `-q` summary will look like success.
> Any done-when in this document that says "verified red" must therefore either live in
> a genuinely hermetic test (assert on the SQL strings — see §2 done-when 2) or carry an
> explicit obligation to quote a `-v`/`-rs` run showing `passed`, not `skipped`
> (§2 done-when 3).

The wholly hermetic files in this area — no DB, no skip — are
`tests/unit/test_app_grants.py` and `tests/unit/test_org_access_control.py`.

---

## 8. Open, and deliberately unanswered here

- **Whether Workflows should stay org-wide.** §5 records the posture; changing it is
  a product call, not a defect. No acceptance is written for it.
- ~~**Where a project grant table lives** (`gtd_*` vs a shared `object_grants`).~~
  **Answered 2026-08-03 — moved to §4.1** as a `DECISION (agent-proposed, owner may
  overrule)`, because leaving it open made the tasks team slice undispatchable: an
  implementer cannot invent the table *and* be judged against acceptance that names
  it. Both options and their consequences are recorded there.
- **Whether `subject:` memory compartments and a `group:` memory scope are the same
  feature.** WS-10 S1 owns the compartment design
  (`docs/multiplayer/memory-clearance.md` §7.1); this document only records that
  memory has no `group:` scope today.
