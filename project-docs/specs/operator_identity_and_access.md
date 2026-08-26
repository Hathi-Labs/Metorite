# Operator identity and access — the staff side of the Operator Console

**Status: ACTIVE — minted 2026-08-26. Verified against code on 2026-08-26.**

**◐ CP-12a BUILT 2026-08-26** (`ws31-cp12a-staff-identity`) — the substrate
half. Migration 009, `customer_console/operators.py`, five `store.py` reads and
writes, and **28 tests against a real Postgres 16, 0 skipped**.

Six mutations killed, each one an auth fence. They remove check 1, check 3 or
the status check. They make the refusals distinguishable. They remove the
fail-closed guard. They count only `active` rows in the bootstrap gate.

Ships **DARK** — nothing calls `admit` yet.

**◐ CP-12b BUILT 2026-08-26** (`ws31-cp12b-operator-session`) — the fifth
auth scheme and the real actor. `cc_sess_` through `keys.py`,
`operator_sessions.py`, five more `store.py` writes, and nine operator
routes that now name the PERSON in `control_audit.actor`.

**19 tests, 0 skipped. 550 passed across every Console suite, so the changed
`Operator` dependency broke nothing.** Eight mutations killed. ⚠️ One known
gap was pinned rather than hidden: `/orgs/provision` is a dual-arm door, so
its operator arm recorded the shared actor. **CP-12c closed it.**

**◐ CP-12c BUILT 2026-08-26** (`ws31-cp12c-role-matrix`) — the §5 matrix,
enforced at the door and **failing closed** on any operator route it does not
name. 49 tests. **618 passed** across every Console suite. Seven mutations
killed, one of which fails with `{403, 404} == {403}` — the oracle leak stated
as an assertion. Two spec corrections are recorded in §5 and §8.1.

**◐ CP-12d BUILT 2026-08-27** (`ws31-cp12d-operator-admin`) — an admin adds,
re-roles and deactivates an operator. Four routes, four guards, 24 tests, and
**676 passed** across every Console suite. Eight mutations killed.

⚠️ **Three of these tests proved nothing until mutation testing said so.**
Guard 2 (no self-write) answers 409 before guard 1 (last admin) can. So no
OPERATOR caller can isolate guard 1. Only the break-glass token reaches it,
because that token holds no operator id. It is now a written test rather than
a lesson somebody learns twice.

**◐ CP-12e BUILT 2026-08-27** (`ws31-cp12e-elevation`) — the window and the
break-glass path. `operator_elevation.py`, three routes, and the `elevated`
rows of §5 now need a live window and the role together. 19 tests. **668 passed**
across every Console suite. Nine mutations killed, plus two PAIRS.

⚠️ **The shared token's audit actor is renamed `operator` to `breakglass`.**
Four existing assertions moved with it, and they now read the constant rather
than a literal. Old rows keep the old word.

⚠️ **A real bug was caught at build.** FastAPI matches routes in declaration
order, so `/operators/{operator_id}` swallowed `/operators/elevate` and
`DELETE` answered 404 instead of closing a window. Pinned by
`test_the_elevate_routes_are_not_swallowed_by_the_path_parameter`.

CP-12f and CP-12g are unbuilt.

**Board row:** `work_plan.md` §2 — **WS-31**, ticket series **CP-12**.
**Decision of record:** **D64** (`work_plan.md` §3), taken by the owner on
2026-08-26. It reconciles **D35.3** with **D34**.

**Owns:** who a platform operator is, what each one may do, how one is added and
removed, and what the audit log says afterwards.
**Does not own:** the customer-management surface itself. That stays
`customer_console.md` **CP-8**, and this spec adds no route to it.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **Staff identity.** One directory, ours, checked three ways.
2. **Roles.** `viewer`, `editor` and `admin`, bound to the routes that exist.
3. **Operator administration.** An admin adds, re-roles and deactivates an
   operator from the console.
4. **A real actor in the audit log.** Every write names the person, not a token.
5. **Time-boxed elevation.** No operator holds a destructive privilege at rest.
6. **A break-glass path** that is separate, alerted and rotated after use.
7. **Session hardening.** An opaque token, an expiry, and server-side revocation.
8. **The fences** that make each rule above fail when an agent breaks it (R7).

### 1.2 Non-goals — each one is a decision, not an omission

| # | Not built | Why |
|---|---|---|
| NG-1 | **Any reach into tenant content** — tasks, projects, documents, mail | **D64.5.** An operator sees the commercial record of a company. An operator never sees what that company wrote. This is the largest single cut to the blast radius, and widening it is a new owner decision |
| NG-2 | **Impersonation, or "sign in as this user"** | Same decision. It is the capability abused in the 2020 Twitter incident. It also makes the tenant audit trail lie, because the row says the customer acted |
| NG-3 | **Four-eyes approval on destructive actions** | Deferred with a named trigger. See §9 |
| NG-4 | **Per-action step-up re-authentication** | Deferred. §6.3's elevation covers most of the same ground, and §9 records what it does not cover |
| NG-5 | **SCIM or directory group sync** | The operator registry is the authority. A group sync would make the directory the authority, which contradicts D34.4 |
| NG-6 | **The customer's own admin surface** | That is `launch_surface.md` §6.2 and `subscription_console.md` SC-2. Two audiences, two surfaces |
| NG-7 | **The theming engine** | **D35.4 stands.** The Operator Console is exempt, structurally, because it is a different app |

---

## 2. What is true today — measured, not remembered

An agent must read this table before it proposes a change. Every row was read
from the code on 2026-08-26.

| # | Finding | Where |
|---|---|---|
| **F1** | **One shared passphrase admits everybody.** `OPERATOR_CONSOLE_STAFF_SECRET` is compared, in constant time, against what the person typed. There is one secret for the whole team | `workbench/operator_console/src/lib/staff.ts:48-61` |
| **F2** | **The cookie holds the passphrase itself.** The sign-in route writes the typed secret into the cookie. A cookie that leaks is a passphrase that leaks. No expiry is set, and no server-side record exists to revoke | `workbench/operator_console/src/app/api/operator/session/route.ts:30-35` |
| **F3** | **The audit log cannot say who.** Every operator write records the literal string `operator` | `apps/services/customer_console/customer_console/main.py:826` and `:1234` and `:2048` |
| **F4** | **There are no roles.** Anybody who signs in can do everything, and that includes destroying a customer's tenant plane | `POST /orgs/purge`, `main.py:1377` |
| **F5** | **Removing one person means changing the secret for everybody.** There is no per-person revocation | F1 |
| **F6** | **Nothing slows a guess.** The sign-in route has no rate limit, no lockout and no delay | `session/route.ts` |
| **F7** | **The gate holds by convention, not by structure.** The app has no `middleware.ts`. Each route calls the gate itself. A new route that forgets is open, and no test says otherwise | `find workbench/operator_console -name "middleware*"` returns nothing |

**Read F1 to F7 together and the shape is clear.** The console is protected by a
shared password with no identity behind it. That posture was correct while the
app was dark and nobody had deployed it. The app was deployed on 2026-08-22, so
the posture is now the weakest control in the product.

⚠️ **F3 is the one that compounds.** Roles are only worth as much as the record
of who used them. If the log cannot name a person, then a role gate is a control
nobody can check after the fact.

---

## 3. The decision this rests on — D64

**D35.3 said the console pins one Microsoft Entra directory, ours.** **D34 then
bought Supabase Auth** for the customer plane, with Microsoft as one of its
providers. Nobody reconciled the two, so the staff gate stayed an interim secret
for four months. **D64 reconciles them.**

| # | D64 says | Note |
|---|---|---|
| **D64.1** | **Supabase Auth, with the Microsoft provider, authenticates staff** | D35.3's *intent* is kept — one directory, ours. D35.3's *mechanism* is dropped. We do not stand up a second identity integration to say the same thing |
| **D64.2** | **Three checks admit an operator, and all three must pass** | The directory answers *who are you*. The operator registry answers *may you*. This is **D34.4 applied to staff**, not a new idea |
| **D64.3** | **Three roles: `viewer`, `editor`, `admin`** | `admin` is the only role that administers operators |
| **D64.4** | **No standing destructive privilege.** An `admin` holds the *right to elevate*, not the privilege | The elevation is time-boxed and needs a stated reason |
| **D64.5** | **Operators reach the commercial record only** | NG-1 and NG-2. Widening this is an owner decision |
| **D64.6** | **Four-eyes and per-action step-up are deferred**, with the triggers written down | §9 |

---

## 4. The identity model

### 4.1 The three checks

An operator is admitted when all three pass. Any one that fails refuses the
sign-in, and the console says which class of refusal it was without naming which
check failed.

1. **The directory.** Supabase Auth returns a Microsoft identity. The console
   asserts that the `tid` claim equals `OPERATOR_ENTRA_TENANT_ID`.
2. **The domain.** The email domain is in `OPERATOR_STAFF_DOMAINS`.
3. **The registry.** A row exists in `operator` for that email, and its status is
   `active`.

⚠️ **Check 3 is not redundant.** Without it, every person our directory ever
admits becomes a platform operator on their first sign-in. The directory tells
us a person works here. It does not tell us they run the platform.

### 4.2 Where the identity lives — the Console, not the Next app

**The operator tables belong to the Customer Console's own migration ladder,
`infra/customer_console/`.** The Operator Console stays a browser-facing
front-end with no database of its own.

The reason is the seam rule. The Console is already the cross-tenant plane. It
already holds `control_audit`, the four authentication schemes and the operator
door. A database in the Next app would be a second data plane for one subject.
The repo forbids a second way to do an existing thing.

### 4.3 The session — an opaque token, not the secret

The session token is a **fourth value in an existing scheme, not a fourth
implementation**. `keys.py` already mints and verifies bearer secrets for
organization keys, deployment keys and discount codes. It gains one more env
segment.

```
cc_sess_<prefix>_<secret>
```

* `mint_key`, `hash_secret`, `verify_secret` and `split_key` are **reused
  unchanged**. This follows the precedent `keys.py` states for the discount code
  in its own comments.
* The browser cookie holds the token. The database holds only its hash.
* The cookie keeps `httpOnly`, `secure` and `sameSite=lax`, which the code
  already sets correctly today.

### 4.4 The fifth authentication scheme, and why it earns its place

`auth.py` opens with *"Four authentication schemes, deliberately separate."* This
adds a fifth, and the argument is the same one that justified the fourth.

> **The operator token identifies no one. A staff session identifies a person.**

Under the new scheme the Operator Console forwards the **operator's session
token**, and the Console derives the actor and the role from it, server-side. The
console stops holding `CUSTOMER_CONSOLE_OPERATOR_TOKEN` for ordinary work.

This gives the property `keys.py` already states for organization keys: **the
credential resolves the actor, and nothing else may.** An actor header would not
give that property. A leaked operator token could then forge any actor, exactly
when the log matters most.

---

## 5. The roles, bound to the routes that exist

⚠️ **This matrix is the contract.** An agent that adds a Console route must add a
row here in the same change. The table of record is
`customer_console/operator_roles.py::MATRIX`, and `test_operator_roles.py` fails
both ways — a route with no row, and a row with no route.

| Action | Console route | `viewer` | `editor` | `admin` |
|---|---|---|---|---|
| List customers | `GET /orgs` | yes | yes | yes |
| Read a billing summary | `GET /billing/summary` | yes | yes | yes |
| Read the plan catalog | `GET /billing/catalog` | yes | yes | yes |
| Read a credit balance | `GET /credits/balance` | yes | yes | yes |
| Read the operator activity log | `GET /operators/activity` | yes | yes | yes |
| Provision a new customer | `POST /orgs/provision` | no | yes | yes |
| Activate a subscription | `POST /billing/subscriptions/activate` | no | yes | yes |
| Assign a seat | `POST /billing/seats` | no | yes | yes |
| Release a seat | `POST /billing/seats/release` | no | yes | yes |
| Grant credits at or below the threshold | `POST /credits/grant` | no | yes | yes |
| **Grant credits above the threshold** | `POST /credits/grant` | no | no | **elevated** |
| **Suspend or resume a company** | `POST /orgs/lifecycle` | no | no | **elevated** |
| **Issue or revoke an organization key** | `POST /keys`, `POST /keys/revoke` | no | no | **elevated** |
| **Issue a discount code** | `POST /discounts` | no | no | **elevated** |
| **Purge an organization** | `POST /orgs/purge` | no | no | **elevated** |
| Add, re-role or deactivate an operator | `POST`/`PATCH` `/operators` | no | no | yes |

**"elevated"** means the role is `admin` **and** a live elevation window is open.
See §6.3.

**The credit threshold** is `OPERATOR_CREDIT_ELEVATION`. It defaults to
**15,000 credits**.

⚠️ **CORRECTED at build, 2026-08-26.** This read `OPERATOR_CREDIT_ELEVATION_PAISE`
and named a rupee amount. `POST /credits/grant` grants a Decimal quantity of
**credits**, and there is no credit-to-rupee rate in this system. The rate card
ships UNPRICED by decision (D19.2), and pricing it is **H-42**, an owner call. A
threshold in paise would imply a conversion that does not exist. The next
implementer would have had to invent a price to apply it.

The NUMBER still echoes **D33.4b**'s ₹15,000 auto-top-up cap, so both keep one
idea of "large enough to need a second thought". ⚠️ Re-derive it against the
rate card once H-42 prices one.

⚠️ Only a POSITIVE grant is measured. A negative delta is a correction. Holding
corrections to the admin bar would push people to fix a mistake by granting
MORE rather than by reversing it.

**Why `viewer` reads the activity log.** Transparency inside the team is the
point of the log. A control that only the powerful can read is not a control.

---

## 6. Operator administration

### 6.1 Adding and removing an operator

| Route | Who | What it does |
|---|---|---|
| `POST /operators` | `admin` | Adds an email and a role. The person becomes real on their first successful directory sign-in |
| `PATCH /operators/{id}` | `admin` | Changes a role, or sets status to `suspended` |
| `DELETE /operators/{id}` | `admin` | **Deactivates. It never deletes the row** |

**Four guards, each with its own test:**

1. **The last-admin guard.** The Console refuses to demote, suspend or deactivate
   the last `active` admin. It answers **409**. Without this guard, one careless
   change locks the whole team out of a live console.
2. **No self-write.** An operator cannot change their own role or their own
   status. An admin who could promote themselves has no role at all.
3. **Deactivation seals, it does not erase.** This follows **D63**. The row stays,
   the audit history stays readable, and the status becomes `deactivated`.
4. **Deactivation revokes every session at once.** The Console sets `revoked_at`
   on every `operator_session` row for that operator, inside the same
   transaction. This is the fix for **F5**.

### 6.2 The first operator

The `operator` table starts empty, and an empty table admits nobody. The Console
reads `OPERATOR_BOOTSTRAP_EMAIL` **only when the table holds zero rows**, and
inserts that one email as an `admin`.

⚠️ **This is a one-time path and it must stay one-time.** Once any row exists,
the variable is ignored. The fence is
`test_bootstrap_is_refused_when_any_operator_exists`, shown red first.

### 6.3 Elevation — the right to act, not the privilege at rest

**No operator holds a destructive privilege while sitting still.** An `admin`
opens a window, does the work, and the window closes.

| Field | Rule |
|---|---|
| Reason | **Required.** Free text, at least 12 characters |
| Reference | Optional, and it follows SC-4g's `<reason>:<ref>` grammar. One vocabulary, not a second |
| Window | `OPERATOR_ELEVATION_TTL_MINUTES`, default **30** |
| Scope | The elevation covers the whole elevated set in §5. It is not per-action |
| Record | The Console writes one `control_audit` row when the window opens, and one for each action inside it |

**What elevation is not.** It is not a way to become an admin. It time-boxes a
role the person already holds. A `viewer` who asks to elevate is refused **403**,
and the refusal is logged.

### 6.4 Break-glass — the shared operator token, kept for exactly this

`CUSTOMER_CONSOLE_OPERATOR_TOKEN` does not go away. It stops being the console's
everyday credential and becomes the emergency path, which is what it was always
shaped like.

Four properties make it safe to keep:

1. It bypasses the role system completely, and that is its only purpose.
2. Every use writes `control_audit` with `actor = 'breakglass'`.
3. Every use sends an alert to `OPERATOR_ALERT_EMAIL`, through the Resend seam
   CP-2d already built.
4. **The runbook rotates it after every use.** A break-glass credential that
   survives its incident is a back door.

⚠️ **An agent must never use this path.** It is an owner act, and §10 registers
it by name.

---

## 7. The data model

One migration, additive, taking **the next free number in
`infra/customer_console/` at build time**. The highest number today is `008`
(**R1** — do not write the number into this spec).

```sql
CREATE TABLE IF NOT EXISTS operator (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    role         TEXT NOT NULL DEFAULT 'viewer'
                 CHECK (role IN ('viewer', 'editor', 'admin')),
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'suspended', 'deactivated')),
    directory_subject TEXT,          -- the Entra object id, set on first sign-in
    added_by     UUID REFERENCES operator (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operator_session (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id  UUID NOT NULL REFERENCES operator (id),
    prefix       TEXT NOT NULL UNIQUE,
    token_hash   TEXT NOT NULL,
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    ip           INET,
    user_agent   TEXT
);

CREATE TABLE IF NOT EXISTS operator_elevation (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id  UUID NOT NULL REFERENCES operator (id),
    reason       TEXT NOT NULL,
    reference    TEXT,
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ
);
```

⚠️ **These three tables are deliberately NOT tenant-scoped.** They belong to the
cross-tenant plane. **R5(a)** is satisfied the way CP-1 satisfied it. The
Console's ladder is not the tenant ladder, so `gen_tenant_migration.py` does not
scan it. Say this in the pull request body. A reviewer who does not know it will
correctly challenge it.

**`control_audit` gains no column.** The `actor` column becomes the operator's
email instead of the literal `operator`. That shape is already in use: `main.py`
records an email in `actor` under the deployment-key scheme, and CP-2g's purge
scrubber already rewrites email-shaped actors. **Reuse both. Do not add a
second actor column.**

**Retention.** Keep `control_audit` for at least **12 months**. That is the floor
common SOC 2 guidance asks for, and we have no reason to hold less.

---

## 8. The tickets

All seven slices ship **behind `OPERATOR_IDENTITY_ENABLED`, default OFF**. The
interim passphrase keeps working until CP-12g. This is the repo's "ship dark"
rule, and it is what stops a half-built identity change from locking the team
out of a live console.

| Ticket | What it delivers | Depends on | Gate |
|---|---|---|---|
| **CP-12a** | ◐ **BUILT 2026-08-26 (substrate half).** The three checks of §4.1, the `operator` registry and the one-time bootstrap — migration 009, `operators.py`, five `store.py` functions, 28 R8 tests, 6 mutations killed. ⚠️ **Deferred to CP-12b:** the Supabase sign-in exchange itself. `admit()` takes a *verified* `(tid, email)` and nothing verifies one yet, so the module is reachable by no route | — | 🟢 **AGENT-SAFE** to build. 🔴 The provider configuration and the env values are **OWNER-GATE** |
| **CP-12b** | ◐ **BUILT 2026-08-26.** The fifth auth scheme (`cc_sess_`), `operator_sessions.py`, absolute **and** idle expiry, server-side revocation, and nine operator routes whose audit rows now name the person — 19 R8 tests, 8 mutations killed. ⚠️ **Known gap, pinned by a test:** `/orgs/provision` is a dual-arm door and its operator arm still records the shared actor. CP-12c closes it | CP-12a | 🟢 **AGENT-SAFE** |
| **CP-12c** | ◐ **BUILT 2026-08-26.** `operator_roles.py` holds the §5 matrix, enforced in `auth.require_operator` BEFORE the route body runs — so a refusal cannot reveal whether a company exists. Fails CLOSED on an unnamed route. 49 R8 tests, 7 mutations killed. Also closes CP-12b's provision-actor gap | CP-12b | 🟢 **AGENT-SAFE** |
| **CP-12d** | ◐ **BUILT 2026-08-27.** FOUR routes (`GET`/`POST` `/operators`, `PATCH`/`DELETE` `/operators/{id}`) and the four guards of §6.1. `GET` is `viewer` — who holds power over our customers is what the team should see without asking. 24 R8 tests, 8 mutations killed. ⚠️ Deferred: the console SURFACE that drives them, which lands with CP-12f | CP-12c | 🟢 **AGENT-SAFE** to build. 🔴 Granting a real person the `admin` role on the live box is **OWNER-GATE** |
| **CP-12e** | ◐ **BUILT 2026-08-27.** `operator_elevation.py` plus `POST`/`GET`/`DELETE` `/operators/elevate`. An `elevated` row needs a live window AND the role. The shared token's actor becomes `breakglass` and every use logs a WARNING. 19 R8 tests, 9 mutations killed plus two pairs. ⚠️ The alert is a log line, not mail — see DEF-7 | CP-12c | 🟢 **AGENT-SAFE** |
| **CP-12f** | **The Activity surface.** A cross-org read of `control_audit` with a filter for actor, action and company. Every role reads it | CP-12b | 🟢 **AGENT-SAFE** |
| **CP-12g** | **The cutover and the fences.** Delete `staff.ts`. Remove `OPERATOR_CONSOLE_STAFF_SECRET`. Add the route-coverage fence that closes **F7** | all | 🟢 **AGENT-SAFE** to build. 🔴 The flag flip and the secret removal are **OWNER-GATE** |

### 8.1 Done-when, per ticket

**CP-12a**
1. A Microsoft identity whose `tid` is not ours is refused **403**, and the
   refusal is logged.
2. An email outside `OPERATOR_STAFF_DOMAINS` is refused **403**.
3. An email with no `operator` row is refused **403**, even when checks 1 and 2
   pass.
4. An `operator` row whose status is `suspended` or `deactivated` is refused.
5. An unset `OPERATOR_ENTRA_TENANT_ID` fails **closed** with a **503**, the same
   way `staff.ts` does today.
6. The bootstrap inserts one admin when the table is empty, and is refused once
   any row exists.

**CP-12b**
7. The cookie holds an opaque token. A database read of `operator_session`
   cannot recover it.
8. A session past `expires_at` is refused **401**.
9. A session idle past `OPERATOR_IDLE_TIMEOUT_MINUTES` is refused **401**.
10. Setting `revoked_at` refuses the next request, with no restart and no cache
    wait.
11. Every write in §5's matrix lands a `control_audit` row whose `actor` is the
    operator's email.
12. **No code path writes the literal `operator` into `actor` under this scheme.**

**CP-12c**
13. Each cell of §5's matrix has a test. A `no` cell answers **403**, and the
    refusal is logged.
14. The matrix **fails closed**: an operator route it does not name is refused.
    `test_operator_roles.py::test_every_operator_gated_route_has_a_matrix_row`
    fails at source level, so the refusal arrives in CI rather than in
    production. ⚠️ **CORRECTED at build:** this said `role_matrix.test.ts`. The
    matrix is enforced server-side in the Console, so a TypeScript test cannot
    see the routes it must cover.
15. Every **403** is byte-identical whether the role is too low or the company
    does not exist. A refusal must not answer a question.

**CP-12d**
16. Demoting, suspending or deactivating the last active admin answers **409**.
17. An operator changing their own role or status answers **409**.
18. Deactivation seals the row and revokes every session of that operator in one
    transaction.
19. A deactivated operator's rows in `control_audit` stay readable.

**CP-12e**
20. An elevated action with no live window answers **403**.
21. An elevation with a reason shorter than 12 characters answers **400**.
22. A window past `expires_at` refuses the next elevated action.
23. A `viewer` or an `editor` who asks to elevate answers **403**.
24. A break-glass call writes `actor='breakglass'` and sends one alert.

**CP-12f**
25. The activity read returns rows for every company, and a `viewer` may read it.
26. The read is keyset-paginated. ⚠️ Read **H-7** first — `now()` can move
    backwards, and a keyset cursor that assumes it cannot is already a known
    defect on migration 168.

**CP-12g**
27. `rg "OPERATOR_CONSOLE_STAFF_SECRET" workbench/` returns no hits.
28. A new route file under `src/app/api/operator/**` that does not go through the
    gate makes the fence fail. **Show this red first.**
29. With the flag ON, the old passphrase is refused.

---

## 9. Named deferrals, and what pulls each one in

**These are not oversights.** Each one is written down with the trigger that
turns it into a ticket.

| # | Deferred | Trigger that pulls it in |
|---|---|---|
| **DEF-1** | **Four-eyes approval on purge, suspend and large credit grants** | A **second admin exists**, or the first SOC 2 engagement starts. Four-eyes needs two people to mean anything, and today it would only lock the owner out. ⚠️ This is the control that holds when everything else has failed, so the trigger must not be allowed to pass unnoticed |
| **DEF-2** | **Per-action step-up re-authentication** | An operator works from a shared or unmanaged device. §6.3's elevation raises the cost of a stolen cookie, but it does **not** re-prove possession of the second factor, and that difference is the whole of what step-up adds |
| **DEF-3** | **SCIM or directory group sync** | The team passes about 10 operators, where hand-maintaining the registry starts to drift |
| **DEF-4** | **Read-only tenant view for support, with consent and an access record** | A support request arrives that the team cannot answer without seeing tenant content. ⚠️ **D64.5 makes this an owner decision, not an engineering one** |
| **DEF-5** | **Anomaly alerting** — a burst of reads, an odd hour, a first-time company | The activity log of CP-12f gives the data. Nobody reads a log until it alerts |
| **DEF-6** | **Tamper-evident audit storage**, off-box and append-only | The first external audit. Today `control_audit` sits in the same database an admin can reach |
| **DEF-7** | **Mailing the break-glass alert to `OPERATOR_ALERT_EMAIL`** | ⚠️ **CP-12e logs a WARNING instead, deliberately.** The Resend seam lives in the GATEWAY (`routes/email_otp.py`), and reaching across a service boundary for one message would put a second email seam inside the Console. The log line is the durable record and the thing an alert rule fires on. The trigger is **log alerting existing at all** — until something watches the Console's logs, mail from the Console would be the only alerting we have, which is worse than one place to look |

---

## 10. Owner-gate register

An agent must **refuse these by name** and say so. They belong in
`work_plan.md` §6.

| # | Act | Class |
|---|---|---|
| **G1** | Configuring the Supabase Auth Microsoft provider, and holding its secret | §6.0 B — external accounts and credentials |
| **G2** | Setting `OPERATOR_ENTRA_TENANT_ID`, `OPERATOR_STAFF_DOMAINS` or `OPERATOR_BOOTSTRAP_EMAIL` on the box | `env-write` |
| **G3** | Flipping `OPERATOR_IDENTITY_ENABLED` on a live box | `enforcement-flip` |
| **G4** | Removing `OPERATOR_CONSOLE_STAFF_SECRET` from the box | `env-write`, and it is the cutover |
| **G5** | Granting a real person the `admin` role on the live console | A role write. CLAUDE.md §3.2 already refuses member and role writes |
| **G6** | **Using the break-glass token** | It bypasses every control in this spec |
| **G7** | Rotating the break-glass token after an incident | `secrets` |

---

## 11. Verification commands

```bash
# The Console half — R8, against a real Postgres 16.
uv run pytest tests/unit/test_operator_identity.py -q
uv run pytest tests/unit/test_operator_session.py -q
uv run pytest tests/unit/test_operator_roles.py -q
uv run pytest tests/unit/test_operator_admin.py -q
uv run pytest tests/unit/test_operator_elevation.py -q

# The seam ratchets must stay green.
uv run pytest tests/unit/test_db_engine_seam.py tests/unit/test_tenant_coverage.py -q

# The console half.
cd workbench/operator_console && npx tsc --noEmit && npx vitest run

# F7's fence — every operator route goes through the gate.
cd workbench/operator_console && npx vitest run route_coverage

# The interim passphrase is gone (CP-12g only).
rg -n "OPERATOR_CONSOLE_STAFF_SECRET" workbench/    # expect: no hits
```

⚠️ **R8 binds every slice here.** The subject of this work is auth and money.
`engineering_practice.md` §4 asks for mutation testing on exactly those two, so
each role gate and each guard in §6.1 must be shown red first.

---

## 12. Why this shape — the outside evidence

The design above is not invented. Four things in the public record shaped it.

**The 2020 Twitter incident is the closest analogue we have.** Attackers reached
an internal support console through phone spear-phishing, and more than 1,000
people held access to tools that could change any account. Twitter's own
conclusion named the two failures: social engineering, and the breadth of what
internal tools allowed. **NG-2 and §6.3 are the two direct answers.** We do not
build the capability that was abused. No operator holds a destructive privilege
at rest.

**Just-in-time access is the current standard for privileged administration.**
The pattern is elevation on request, with an expiry, a reason and a log, and it
replaces standing privilege. §6.3 is that pattern. The recorded failure mode is
also known. Temporary access that nobody expires becomes standing privilege
again. That is why the Console enforces `expires_at`, and the browser does not.

**Break-glass is expected to exist, and expected to be loud.** The guidance is
consistent — keep the emergency path, then time-box it, log it, alert on it and
rotate it. §6.4 does all four.

**Audit expectations are concrete.** Common SOC 2 guidance asks for three things:

- Every privileged action records the user, the action, the time and the
  resource.
- The log holds refusals and successes, not successes alone.
- Retention is at least 12 months.

**F3 fails the first of those today.** CP-12b is what closes it.

### Sources

- [Twitter's own incident update](https://blog.x.com/en_us/topics/company/2020/an-update-on-our-security-incident)
- [Dark Reading, on the breadth of Twitter's internal tool access](https://www.darkreading.com/attacks-breaches/access-to-internal-twitter-admin-tools-is-widespread/d/d-id/1338453)
- [BeyondTrust, on just-in-time access](https://www.beyondtrust.com/resources/glossary/just-in-time-access)
- [Oleria, on zero standing privileges](https://www.oleria.com/blog/just-in-time-access)
- [Bytebase, on SOC 2 audit logging](https://www.bytebase.com/blog/soc2-audit-logging)
- [AuditKit, a SOC 2 audit log checklist](https://auditkit.dev/blog/soc-2-audit-log-requirements)
- [ToolJet, on secure internal dashboards](https://blog.tooljet.com/build-secure-internal-dashboards-for-enterprises/)
