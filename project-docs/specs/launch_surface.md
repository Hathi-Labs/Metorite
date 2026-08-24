# Launch Surface — what a customer sees on day one

**Status: ACTIVE · owning spec of WS-34 · written 2026-08-24 · verified against
code on 2026-08-24** (anchors below were read from the tree that day; re-verify
every path at dispatch, R4/§1.4).

**Owner directive, 2026-08-24.** Verbatim, because everything here derives from
it and a paraphrase is how a directive drifts:

> - an Admin customer should be able to assign seats from the app by inviting
>   users to join the organisation.
> - an Admin customer should be able to free up seats as well from the app. the
>   users without seats should still show up as unassigned in the admin member
>   section so that they can be reassigned.
> - the operator console (us) can also do this for the customers.
> - for the time being remove the concept of "Centers" from the application.
>   even in terms of our pricing plan, the customer will actually have access to
>   everything. customers have a flat pricing structure of 500rs/user/month + AI
>   credits. Personal Center however will still exist as a category. everything
>   else will just come under "Apps"
> - Hide all apps other than: [the table in §2]
> - All the apps that are not currently being displayed should still remain in
>   the application and make a note of the app that actually go live.
> - ensure that the entire UI UX is properly responsive to the permissions and
>   access that every user actually has and works without problems.

Recorded as **D49** in `work_plan.md` §3. Where this spec and D49 disagree, D49
wins and this file is the bug.

---

## 1. Scope and non-goals

### In scope

1. **The launch surface** — the exact set of apps a customer sees, as one
   registry, with everything else marked `preview` and hidden (§2, §3).
2. **Retiring Centers from the product surface** — the nav section, the Center
   landing pages as a destination, and Center packages as a pricing object
   (§4, §5).
3. **Flat pricing** — ₹500/user/month + AI credits, one sellable seat, access to
   everything that is live (§4).
4. **The seat lifecycle a customer admin can drive from the app** — invite →
   seat → release → *unassigned* → reassign (§6).
5. **Operator parity** — the same four moves available to us, per customer (§7).
6. **A nav that never lies** — one resolution contract shared by the sidebar,
   the mobile drawer and the home grid, with a defined answer for *unresolved*
   and for *transiently unreachable* (§8).

### Non-goals — named so nobody builds them from this file

- **Deleting Center code.** `lib/centers.ts`, `/centers/[slug]`, the `center.*`
  feature rows and the `center` plan rows all stay exactly where they are. The
  directive is *remove the concept from the application*, i.e. from what a
  customer meets — not *delete the work*. §5 says precisely what changes.
- **Deleting or gating hidden app code.** A `preview` app keeps its routes, its
  API, its tests and its board row. It loses its nav entry and its home tile,
  nothing else (§3).
- **Repricing anything already sold.** D49 sets the price for the flat plan; what
  happens to an organization holding Center-package seats is an **OWNER-GATE**
  migration decision, not this spec's (§4.4).
- **A billing surface in the sidebar.** `/settings/billing` stays URL-only until
  the Console has somewhere to run (`nav.ts`'s standing note, CLAUDE.md §4).
- **Changing the authorization model.** Features, permissions, groups and the
  `require_permission()` boundary are untouched. This spec changes what is
  *offered*, never what is *enforced*.

---

## 2. The launch surface of record

**This table is the authority.** The registry in code (`src/lib/nav.ts`) must
match it exactly, and `nav.test.ts` is the fence that says so (§9 LS-1).

### Live — shipped to customers

| Section | App | Route | Gate | Note |
|---|---|---|---|---|
| **Personal Center** | Tasks | `/tasks` | `feature:tasks` | |
| **Personal Center** | My Profile | `/people/me` | ungated | Your own record is never the directory (D-PC-15) |
| **Personal Center** | My Access | `/access` | ungated | Renamed from "Your access". Ungated by construction — it is the page that explains a missing pane |
| **Apps** | Projects | `/projects` | `feature:projects` | |
| **AI Studio** | Chat | `/chat` | `feature:chat` | Section renamed from "Studio" |
| **Admin** | Approvals | `/approvals` | `feature:approvals` | |
| **Admin** | Organisation | `/settings/organization` | admin | Tabs: Members & roles · Seat assignments · Branding (§6.2) |
| **Admin** | Appearance | `/settings/appearance` | ungated | Personal preference; the org-wide default on the same page is gateway-authorized |

Eight entries, four sections, in that order. **"Personal Center" survives as a
section label and nothing else** — it is a category of apps mapped one-to-one to
the signed-in person, not a projection of a department, and the directive keeps
it by name.

### Preview — in the application, absent from the surface

Every other pane. Listed here so "which apps did we hold back" has one written
answer rather than a diff:

| Section it will return to | App | Route | Held back because |
|---|---|---|---|
| Personal Center | Dashboard | `/dashboard` | Company-view-only today; the personal view is WS-15 |
| Personal Center | Email | `/email` | WS-17 incomplete |
| Personal Center | WhatsApp | `/whatsapp` | WS-20 incomplete |
| Personal Center | Notes | `/notes` | WS-19 incomplete |
| Personal Center | Memories | `/memory` | WS-9 — operator-grade surface, not customer-grade |
| Personal Center | Artifacts | `/artifacts` | Reads as a debugging surface |
| Apps | CRM | `/crm` | WS-26 incomplete |
| Apps | People | `/people` | WS-28 partially built; the directory is not launch-ready |
| Apps | Centers (six panes) | `/centers/<slug>` | **Withdrawn by D49**, not merely incomplete (§5) |
| AI Studio | Workflows | `/workflows` | WS-11 incomplete |
| AI Studio | App Workshop | `/build/apps` | Incomplete |
| AI Studio | Agent Workshop | `/build/agents` | Incomplete |
| Admin | Models | `/settings/models` | Operator concern, not a customer one |
| Admin | Agent Registry | `/agents` | Operator concern |
| Admin | Integrations | `/integrations` | Incomplete |
| Admin | Live Activity | `/observability` | Operator concern |

**The count is the fence.** `nav.test.ts` asserts that exactly the eight rows
above are `live` — so adding a pane without deciding its launch status fails,
and promoting one is a deliberate one-line edit with a test to update.

---

## 3. What `preview` means, precisely

A pane's launch status is a field on the pane, not a fork of the registry:

```ts
launch: "live" | "preview"   // src/lib/nav.ts, NavPane
```

1. **`preview` panes never render in navigation** — not in the sidebar, not in
   the mobile drawer, not on the home grid. One filter, applied in
   `visibleSections()`, so the three surfaces cannot disagree (§8).
2. **Their routes still work.** Typing `/email` still reaches the Email app,
   still subject to `feature:email` at the gateway. This is deliberate: the
   directive says the apps *remain in the application*, and a route that 404s is
   a deleted app with extra steps.
3. **One escape hatch for us:** `NEXT_PUBLIC_SHOW_PREVIEW_APPS=1` restores every
   `preview` pane to the navigation. Default unset, i.e. OFF — CLAUDE.md §4's
   ship-dark rule. It is a *build-time* variable on the Next tier, so flipping it
   is a redeploy of the control plane, and it changes **nothing** about
   authorization: a preview pane restored to the nav is still refused by the
   gateway to a member without the grant.
4. **`preview` is not a permission.** A member who holds `feature:email` still
   cannot see the Email pane at launch, because the pane is not offered. Access
   answers *may you*; launch status answers *are we selling it yet*. Conflating
   them would mean revoking grants to hide an app, and then re-granting them at
   promotion — a migration per launch decision.
5. **`/access` tells the truth about it.** The "My Access" report distinguishes
   *not granted* from *not launched yet*, so a customer admin who reads
   "Email — not available yet" is not left hunting for a grant that would not
   help (§9 LS-3).

---

## 4. Pricing — flat, per user, plus AI credits (D49)

### 4.1 The shape

**One sellable seat: ₹500 per user per month, and it carries everything that is
live.** AI usage is metered separately in credits, unchanged — D19.2's credit
unit, the rate card and the metering path are untouched by this decision.

There is no Core layer, no Center package, no add-on and no bundle in what a
customer is offered. A seat is a seat.

### 4.2 What this supersedes

- **`saas_multitenancy.md` §2.4b** (Center packages, D23/D24: ₹600 Core + ₹600 /
  ₹300 Center packages + ₹500/₹300 add-ons + ₹1,800 all-Centers + ₹3,000
  Complete). **Superseded as the pricing shape of record.** The section keeps its
  banner and its reasoning as the decision record; it prices nothing now.
- **`saas_multitenancy.md` §2.4a**'s surviving `complete` row — same status.
- **`subscription_console.md`** SC-1/SC-2's "Centers & add-ons panel" and
  "users × Centers seat grid". The grid collapses to one column, which is §6.2's
  Seat assignments tab.

### 4.3 What it does **not** supersede

- **D19.3 — the hard cap.** Assignment still refuses past `purchased` with a 409
  carrying `buy_more`, and never auto-upgrades. One plan does not mean unlimited
  seats; it means one *kind* of seat.
- **D32.5 — the three counts.** `purchased` / `assigned` / `available`, computed
  once in `customer_console/seats.py`, clamped and flagged there, rendered
  verbatim everywhere. Nothing in this change recomputes a seat count.
- **The entitlement seam and the 402-vs-403 partition** (`saas_multitenancy.md`
  §2.3). A flat plan makes the *module intersect* trivially total for a seated
  member; it does not remove the seam. Removing it would be a one-way door the
  moment we sell a tier again.
- **The credit ledger, the rate card, and its unpriced-until-measured fence.**

### 4.4 Migration posture — **OWNER-GATE**

The Console catalog is DATA (`infra/customer_console/002_seed_catalog.sql`'s own
rule). D49's consequences for it:

- **AGENT-SAFE:** add the flat plan as a new, active catalog row, and mark the
  Center packages / bundles `active = FALSE` so the checkout cannot sell what we
  no longer offer. Expand-only, no renames, no deletes (R6).
- **OWNER-GATE:** anything touching an organization that already holds seats on a
  retired plan — repricing, converting, refunding, proration. `work_plan.md` §6
  (money on a live system). An agent records the situation and stops.

`core` is the plan every existing seat sits on (membership IS the Core seat,
D19.3, and `seats.py`'s `CORE_PLAN_SLUG`). **It stays active and stays the seat
sign-in allocates** — the flat plan is a *price* change to the thing already
being assigned, not a second seat vocabulary. Introducing a parallel slug and
then having to decide which one a member "really" holds is exactly the
second-implementation defect CLAUDE.md §5 forbids.

So concretely: **`core` is repriced to ₹500 and renamed for display to
"Metorite" ; every `center` / `addon` / `bundle` row is deactivated.** One
sellable row, one seat slug, no new vocabulary.

---

## 5. Centers — withdrawn from the surface, kept in the tree

| Thing | Fate under D49 |
|---|---|
| The **"Centers" nav section** | **Deleted.** Its two non-Center panes (Projects, CRM) move to **Apps** |
| `/centers/<slug>` **landing pages** | Kept, routable, unlinked. `preview` |
| `lib/centers.ts` **registry** | Kept verbatim. Still feeds the Center landing pages and `HREF_FEATURES` |
| `center.*` **feature rows** (migration 140) | Kept, granted as before. Nothing is revoked |
| **Center packages** in the Console catalog | Deactivated (§4.4) |
| `department_centers.md` | Stays ACTIVE as the *design* record; gains a banner saying the surface is withdrawn. WS-13/14/15/16 are **parked**, not cancelled |
| `?center=<slug>` **scoping** on `/projects` | Kept. It is a filter, not a Center — the directive removes the concept as a *destination*, and a URL parameter nobody navigates to costs nothing |

**Why kept rather than deleted.** D22 put the Center roster on the record and
D12's slice grants are built on `group:<slug>` — the same slugs. Deleting the
registry would take the group vocabulary with it and break the Projects grant
model, which is live. The concept leaves the *surface*; it stays the *scoping
primitive*. That distinction is the whole of this section.

---

## 6. Seats a customer admin can actually drive

### 6.1 The four moves, and what already exists

| Move | Today | Gap |
|---|---|---|
| **Invite** a person into the org | `POST /admin/members` → `invited` row (`routes/admin/members.py:147`) | Does not touch seats |
| **Assign** a seat | `POST /seats/assign` → Console `seat_admin` door (`routes/seats.py`) | Built; reachable only from `/settings/billing`, which is not in the nav |
| **Release** a seat | `POST /seats/release` | Same |
| **See who is unseated** | — | **Missing.** `GET /me/members` returns `{email, role, status}` and its docstring records the per-member seat summary as DEFERRED |

So the directive's first three bullets are mostly *wiring*, and the fourth is a
real read that does not exist.

### 6.2 The surface: Organisation, three tabs

`/settings/organization` becomes the one admin destination for the org, with
tabs:

1. **Members & roles** — today's `/settings/members`, unchanged in behaviour.
   `/settings/members` redirects here so existing links and the member-detail
   route `/settings/members/[email]` keep working.
2. **Seat assignments** — new. One row per org member, each showing
   **Seated** or **Unassigned**, with Assign / Release. Above the rows, the three
   counts from `GET /me/seats`, rendered verbatim.
3. **Branding** — today's `/settings/organization` page (logo, display name).

**Unassigned is a first-class state, not an absence.** A member whose seat was
released stays on the roster, stays `active`, and shows as *Unassigned* with an
enabled Assign control. That is the directive's second bullet and the reason the
seat tab reads the **gateway's** member roster (every member of the org) rather
than only the Console's seat rows (only members who have ever held one).

### 6.3 Invite assigns a seat

The invite dialog gains one checkbox, **"Assign a seat now"**, default **on**:

- Invite succeeds → attempt the seat assign → report both outcomes.
- **The invite is never rolled back by a failed assign.** Two systems, two
  transactions; a member who exists without a seat is exactly the *Unassigned*
  state the surface is built to show, and unwinding a membership because billing
  was briefly unreachable is the worse failure.
- At the cap the assign returns 409 with `buy_more`; the dialog shows the
  Console's own sentence (`buyMoreMessage`) and leaves the member Unassigned.
- On an unwired deployment the assign returns 503 and the dialog says seat
  management is not configured. The invite still stands.

### 6.4 The read that has to exist

`GET /me/members` grows `seats: string[]` per member — the live seat plan slugs
that member holds, from one additional store read over `seat_assignment` with
`released_at IS NULL`, joined in memory rather than as an N+1 per member.

Empty array = **Unassigned**. That is the whole of the new vocabulary.

---

## 7. Operator parity

`workbench/operator_console` already assigns and releases by email
(`/api/operator/seats`, `/api/operator/seats/release`, driving the Console's
operator door). What it cannot do is *see who is unseated* — the same gap as
§6.4, one door up.

So: the operator customer page gains the same roster, from the operator-side
read, with the same Seated / Unassigned vocabulary and the same two controls.
An operator can then do for a customer exactly what the customer's own admin
can, which is the directive's third bullet.

**Inviting a member into a customer's org stays customer-side.** An operator
seating somebody who is not a member is refused Console-side already (no
identity, no membership); we are not adding a door that creates people inside a
customer's organization. That would be an outward write into a customer's
directory with no customer in the loop.

---

## 8. A navigation that never lies

Three defects, all of them the same shape — *the UI answering a question before
it has the answer*:

### 8.1 The full-then-shrink flash

`visibleSections(null)` returns **every** pane while access is unresolved
(`nav.ts`), and both the sidebar and the mobile drawer pass `null` while
`accessLoading`. So the first paint after every sign-in shows the complete
application and then removes most of it. That is the reported "sometimes all the
apps appear, sometimes they don't": it is not intermittent, it is a race — you
see the full list exactly as long as `/api/auth/me` takes to answer.

**The rule:** an unresolved viewer gets a **skeleton**, never a guess. Nav is
rendered from a resolved answer or from placeholder rows of the right shape; it
is never rendered from "assume everything".

### 8.2 A transient failure reads as signed out

`fetchAccess()` maps *every* failure — a 502, an abort, a dropped connection, a
gateway restart — to `NO_ACCESS`, and `AccessProvider` re-fetches every 120 s.
So one blip on a long-lived tab empties the sidebar and the member concludes
they were signed out. They were not.

**The rule:** distinguish **authoritative** from **transient**. A 401/403 is the
server saying *no*, and clears the access. Anything else — network error, 5xx,
timeout — **keeps the last good answer** and marks the access stale. The sidebar
does not change; a quiet indicator can, later.

### 8.3 The home grid ignores access entirely

`src/app/page.tsx` maps `NAV_SECTIONS` with no filter at all, so the landing page
lists every pane to every member regardless of grants. Same registry, same
filter, or it is a second answer to one question.

### 8.4 The contract

> Every navigation surface renders `visibleSections(features, isAdmin)` over the
> one registry. There is exactly one filter, it takes launch status and access
> together, and its answer for an unresolved viewer is "not yet", never
> "everything".

Fence: `nav.test.ts` + `accessProvider.test.ts` (§9 LS-4/LS-5).

---

## 9. Tickets

Every ticket is **AGENT-SAFE** unless marked otherwise.

### LS-1 · The launch registry — **AGENT-SAFE**

Add `launch: "live" | "preview"` to `NavPane`. Restructure `NAV_SECTIONS` into
Personal Center / Apps / AI Studio / Admin per §2. Delete the `centers` section;
move Projects and CRM into Apps. Rename "Your access" → "My Access" and the
"Studio" section → "AI Studio".

**Done when:** `nav.test.ts` asserts (a) the live set is exactly §2's eight
`(section, href)` pairs; (b) every pane carries an explicit `launch`; (c) no
section is named "Centers"; (d) `visibleSections` drops `preview` panes when the
preview flag is off and restores them when it is on.

### LS-2 · One filter, three surfaces — **AGENT-SAFE**

`Sidebar`, `AppShell`'s mobile drawer and `app/page.tsx` all render
`visibleSections(...)`; the home page stops mapping `NAV_SECTIONS` directly.

**Done when:** a grep for `NAV_SECTIONS` outside `nav.ts`, `accessReport.ts` and
tests returns nothing, and a test asserts the home page and the sidebar produce
the same pane set for one access payload.

### LS-3 · Launch status in the access report — **AGENT-SAFE**

`paneReport()` gains a `not-launched` status so `/access` distinguishes it from
`denied`.

**Done when:** `accessReport.test.ts` covers a preview pane held by a member who
*does* hold its feature, and the reason sentence says it is not available yet.

### LS-4 · No full-then-shrink flash — **AGENT-SAFE**

`visibleSections(null, …)` returns `[]`. The sidebar and drawer render skeleton
rows while unresolved.

**Done when:** `nav.test.ts` pins `visibleSections(null).length === 0`, and a
component test asserts the sidebar renders no real nav links before access
resolves.

### LS-5 · Transient failure keeps the last good access — **AGENT-SAFE**

`fetchAccess` returns a discriminated result (`ok` / `unauthorized` /
`unavailable`). `AccessProvider` clears on `unauthorized` and keeps the previous
value on `unavailable`.

**Done when:** `accessProvider.test.ts` asserts a 503 after a successful resolve
leaves `access.features` unchanged, and a 401 empties it.

### LS-6 · Organisation tabs — **AGENT-SAFE**

§6.2. Extract the members admin into a component, add the tab strip, move
branding to its own tab, redirect `/settings/members`.

**Done when:** `/settings/organization` renders three tabs; `/settings/members`
redirects; `/settings/members/[email]` still resolves.

### LS-7 · Per-member seat state — **AGENT-SAFE**

§6.4: `MemberView.seats: list[str]`, one extra store read, no N+1. The BFF and
the seat tab render Seated / Unassigned from it.

**Done when:** a Console test asserts a released member comes back with `seats:
[]` and a seated one with `["core"]`, both from one roster call; `R8` — run
against a real Postgres.

### LS-8 · Invite assigns a seat — **AGENT-SAFE**

§6.3, including the three failure paths (cap, unwired, invite-succeeded-
assign-failed).

**Done when:** each of the three paths has a test asserting the member still
exists and the dialog's message names the right cause.

### LS-9 · Operator roster — **AGENT-SAFE**

§7.

**Done when:** the operator customer page lists members with seat state and both
controls act on the Console's operator door.

### LS-10 · Flat plan in the catalog — **AGENT-SAFE** (the data half)

§4.4's expand-only half: reprice `core` to ₹500, retitle it for display,
deactivate every `center` / `addon` / `bundle` row.

**Done when:** the migration replays clean twice, `GET /billing/catalog` returns
exactly one active row, and a test pins that the checkout refuses a deactivated
slug.

### LS-11 · Existing seats on retired plans — **OWNER-GATE**

§4.4. An agent reports what is held and stops.

---

## 10. Verification commands

```bash
# Frontend — the whole gate
cd workbench/control_plane && npx tsc --noEmit && npx vitest run

# The fences this spec introduces, by name
cd workbench/control_plane && npx vitest run src/lib/nav.test.ts \
    src/lib/accessReport.test.ts src/components/accessProvider.test.ts

# Console-side seat + catalog work (R8 — needs a real Postgres)
uv run pytest tests/unit/test_customer_console_sql.py \
              tests/unit/test_customer_console_seats.py -q

# The gateway seat proxy's posture fence
uv run pytest tests/unit/test_seat_admin_proxy_route.py -q

# Operator console
cd workbench/operator_console && npx tsc --noEmit && npx vitest run
```

⚠️ Never run `tests/unit/` as a bare directory (CLAUDE.md §6) — name the files.

---

## 11. Owner gates, restated so they are refused by name

1. **LS-11** — anything touching an organization that already holds seats on a
   plan D49 retires: repricing, converting, refunding, proration.
2. **Setting a live price on a live system** — `work_plan.md` §6, unchanged by
   this spec. Seeding an *inactive* catalog row is data; charging somebody is not.
3. **Flipping `NEXT_PUBLIC_SHOW_PREVIEW_APPS` on a deployed box** — a release
   act (CLAUDE.md §4).
4. **Promoting a `preview` app to `live`** — it is the decision "this is finished
   enough to sell", which is the owner's, and the registry edit is trivial once
   it is taken.
