# The user-management contract — what every app must not deviate from

**Status:** 🟢 Binding · **Created:** 2026-08-05 · **Owner:** vjvarada ·
**Board row:** WS-24 · **Verified against code:** 2026-08-05, on `main` @ `74082882`
(deployed and running). · **Amended 2026-08-08:** **R11** (never take the acting tenant
from input) added with D15/WS-29; the rule count is now **eleven**, and the fact-owner
table below splits tenancy from visibility because D11 was re-taken.

> **This document owns RULES, not FACTS.** Every fact it states is owned by
> another spec and is cited, never restated as though this were its home:
>
> | Fact | Owner |
> |---|---|
> | The access model — roles, permissions, overrides, resolution | `org_access_control.md` |
> | The readiness gate, the onboarding runbook, the role × app matrix | `colleague_onboarding.md` |
> | The **visibility ladder** inside a tenant (D12) | `tenancy_and_visibility.md` §3–§5 |
> | The **tenancy boundary** (D15 — re-taken 2026-08-08; D11 is superseded) | `saas_multitenancy.md` §1 |
> | Multi-tenancy build shapes — SQL, seams, ratchets, runbooks | `saas_multitenancy_implementation.md` |
> | Centers as projections | `department_centers.md` |
>
> If this doc and an owner disagree, **the owner is right and this doc is
> stale** — fix it here. What lives here and nowhere else is the *contract*: the
> rules an app must follow so that the person using it is the person the
> platform thinks it is.

**Anchors here are symbols, not line numbers, deliberately.** This corpus has
repeatedly shipped stale `file:line` citations — the 2026-07-31 audit found
~12 wrong migration numbers alone, and a WS-24 pass corrected six `rooms.py`
anchors. A symbol survives an edit above it; a line number does not.

---

## 1. The identity chain — four hops, and what each one guarantees

    browser ──1──▶ Next.js BFF ──2──▶ Caddy ──3──▶ gateway ──4──▶ resolve_access
    (session cookie)  (adds identity)  (strips forged)  (default-deny)  (DB is the truth)

1. **The browser holds a session cookie** on the workbench origin, issued by
   Entra ID SSO (`workbench/control_plane/src/auth.ts`). The tenant is pinned
   to the Fracktal directory GUID, so only directory members can complete
   sign-in. **A browser request can never add a header** — this is the fact
   that broke the OAuth connect flow for six days (§5.1).
2. **The BFF turns a session into an identity.** `src/lib/gateway.ts` —
   `gatewayHeaders()` attaches the internal Bearer *and* `X-User-Email` and
   throws without a person; `serviceHeaders()` is bearer-only and takes a
   written reason, so every identity-free call is an argument someone made on
   purpose. Read that file's "THE RULE" comment before you use either.
3. **Caddy strips client-supplied identity** on the api vhost
   (`header_up -X-User-Email`, `-X-User-Role`). The gateway cannot tell a
   forwarded header from a forged one, so the proxy *is* the boundary.
   ⚠️ **This only protects traffic that goes through Caddy** — see §6.
4. **The gateway is default-deny.** `require_authenticated` is attached
   app-wide at the `FastAPI(dependencies=[…])` level, so a route added tomorrow
   is covered without anyone remembering. `PUBLIC_ROUTES` is the exemption
   list, and every entry authenticates itself another way (provider signature,
   HMAC state, shared secret).
5. **`resolve_access` is the only answer to "who is this and what may they
   do".** `packages/acb_auth/acb_auth/access.py`. Roles, per-user overrides and
   status resolve to one `EffectiveAccess`, cached 60s. Status is a property of
   the *result*, not a filter on the query, so a stale cache can never outlive a
   suspension by more than the TTL.

**The invariant:** an app never establishes identity. It receives one. If you
find yourself writing code that decides *who the caller is*, you are in the
wrong layer.

---

## 2. The member lifecycle — the states and every door between them

    (nobody)  ──sign-in──▶  access_request:pending   ──approve──▶  active
        │                            │ deny                            │
        │                            ▼                                 │
        │                     access_request:denied                    │
        │                                                              │
        └──invite──▶ invited ──activate──▶ active ──suspend──▶ suspended
                                             │                    │
                                             ├──remove──▶ removed ◀┘
                                             └──purge───▶ (gone)

**`is_active` is `status == "active"` exactly.** Every other status is no
access. Two consequences that have each cost real time:

- **Inviting somebody does not admit them.** Invite writes `invited`; sign-in
  is SSO so there is no acceptance event to promote them. Steps 1 and 1b of
  `colleague_onboarding.md` §2 are **one operation in two clicks**. Approving a
  *sign-in request* does both at once, on purpose.
- **A queue that renders only `pending` loses people permanently.** The
  resolver's upsert deliberately never rewrites `status`, so a request filed as
  `approved`/`denied` for somebody who still cannot sign in is invisible
  forever. Any route that resolves a request **must** have actually admitted
  them (§5.3).

**Four doors reach "no access", and they must agree.** `PATCH` status, `DELETE`
(soft remove), `PUT …/roles` (demotion), and `DELETE …/purge`. All four go
through `_common.assert_not_self_lockout` / `assert_not_self_demotion` so a
caller cannot lock themselves out. A fifth, `PUT …/overrides`, guards with its
own inline copy against a different permission — **known, recorded, and owed a
consolidating pass.**

> `assert_owner_survives` is **not** a self-guard. It refuses only while the
> caller is the last owner, which is a coincidence of org size. Relying on it
> was how the self-suspension hole stayed invisible.

---

## 3. The vocabulary — and the one rule about extending it

Three kinds of grant, all resolved by `acb_auth.permissions`:

| Shape | Means | Gate helper |
|---|---|---|
| `feature:<slug>` | this surface is navigable | `require_feature_router(slug)` |
| `<domain>:<object>:<verb>` | this capability | `require_permission(...)` |
| `agents:run:<name>` | may run this agent | `can_run_agent(...)` |

Deny wins over allow; `*` is a wildcard resolved by `permission_matches`;
per-user overrides sit on top of roles.

**Do not mint a new permission slug to fix a hole.** A new slug is nobody's
grant until an admin creates it — so the "fix" switches the feature off for the
owner too, and the surface stays open for everyone who already holds the old
one. Reuse the existing capability that already governs the thing. Both the HR
directory fix and the sign-in queue reused `admin:members:read` /
`admin:members:manage` for exactly this reason.

> **One registered, argued DEVIATION from that rule — `billing:purchase`**
> *(2026-08-19, WS-30 SC-4a's B7 block; agent-proposed default the owner may
> overrule, D16/D17)*. **No existing capability governs spending the company's
> money**, and the nearest floor, `admin:members:read`, means *"may see the
> member list"* — reusing it would grant purchase authority to every
> member-reader, which is the opposite of what a money write needs. The rule's
> own failure mode (a slug nobody holds) is answered rather than ignored: the
> minting change **seeds it in the same migration**, onto `default`'s `admin`
> role, in `133_workflows_publish_permission.sql`'s idempotent shape. ⚠️ It is
> **born unheld in every organization other than `default`** — not because of
> this slug, but because a newly created org gets no roles at all
> (`saas_multitenancy_implementation.md` §7.1 step 3, §8 trap 5), which the
> org-provisioning ticket owns. **Deviations go here, in §3, or they are not
> deviations — they are drift.**

**A `feature:` flag gates navigation, not data.** `140_center_features.sql` is
explicit about it, and three feature slugs are enforced *nowhere* server-side
(`memory`, `artifacts`, `observability`) — they hide a nav pane, and the
per-object rule is the boundary. If your app's data needs scoping, scope the
query.

---

## 4. The rules — every one of these was learned by breaking it

**R1 — Never navigate the browser directly at the gateway.** It carries no
credentials. If a flow needs a top-level navigation (OAuth consent, a file
download), add a BFF route that authenticates, calls the gateway with
`gatewayHeaders()`, and re-issues the redirect.

**R2 — Never add a route to `PUBLIC_ROUTES` to make it reachable.** That is the
fix that looks right and hands the surface to the internet. Route it through
the BFF instead. If a route genuinely must be public, it authenticates itself
another way, and you write down which way.

**R3 — Never take the acting identity from a query parameter or a request
body.** The authenticated identity outranks anything the caller supplies. A
`user_email=` parameter that outranks the session is an account-takeover
primitive.

**R4 — Declare the auth floor on the route.** `routes/admin/_common.py` creates
its router with **no** `dependencies=`; every route declares
`Depends(require_admin_user)` in its own signature. A route that omits it
inherits *nothing*. Pin it with a wiring test that reads the router.

**R5 — Owner-scoped reads use the shared predicate and answer 404, never
403.** `routes/notes/core.py` — `OWNED_MEETING_PREDICATE` / `load_owned_meeting`
is the pattern. 403 confirms the row exists; 404 does not. Bind the predicate
**into** the mutating statement rather than loading first, so there is no
TOCTOU window.

**R6 — Projection helpers are keyword-only with no default.**
`_row_to_person(row, *, include_hr)` — a missed call site must be a `TypeError`,
not a silent leak of the permissive answer.

**R7 — Destructive routes owe four things:** the shared self-guard; one
transaction (a half-delete is worse than either outcome); a response that says
what was destroyed *table by table*; and an audit row that survives the
deletion. An audit trail that disappears with the person is not one.

**R8 — Report the blast radius before the click, and count what cascades.**
`ON DELETE CASCADE` means a delete on one table can silently empty a table on
your "kept" list. Deleting a member's `email_accounts` row takes 20 tables with
it. If your confirmation dialog understates that, it is lying to the operator.

**R9 — Hiding a control is a courtesy; the server is the boundary.**
`lib/access.ts` says so in its own comment. Ship the server check first, then
hide the button.

**R10 — Case-insensitive on both sides, always.** An IdP that changes UPN
casing between sessions must not silently switch a guard off or empty somebody's
library.

**R11 — Never take the acting TENANT from input.** *(Added 2026-08-08 with D15;
this is R-identity's twin and was created by the same reasoning.)* The
organization a request acts in comes from the **authenticated session** or from a
**tenant-scoped API key**, and from nowhere else. Not an `X-Organization-Id`
header, not a query parameter, not a body field, not a subdomain the server
trusts without re-resolving it against the session.

R3 says never take the acting *identity* from a query parameter; under D15 the
tenant is the wider blast radius of the same mistake — an identity you can spoof
gets you one person's data, a tenant you can spoof gets you a whole company's.
`multi_user_organization_research.md` §17.3 proposes exactly this header, and
`saas_multitenancy.md` §7 item 2 **rejects it by name** so a future reader does
not implement the research.

Two corollaries an implementer must not shave:

- **A background job carries its tenant on its job record** and refuses to run
  without one (`saas_multitenancy.md` MT-1d). A job with no request has no session
  to inherit from, and a job that guesses leaks unbounded rather than one row.
- **The subdomain is a lookup, not an assertion.** Resolve `<slug>` to an
  organization, then verify the authenticated principal holds a membership in it.
  A subdomain that is trusted on its own is a header with a friendlier name.

---

## 5. The traps, with the incident that found each

**5.1 — A route that cannot carry credentials.** `/email/oauth/{provider}/authorize`
was gated by the app-wide default-deny while the only caller was a browser
navigation. It returned "Authentication required" to **every user for six
days**, invisible because the owner's mailbox predated the change. *Ask of any
new route: what exactly attaches the identity?*

**5.2 — A guard that predicts instead of verifying.** Approve read the member,
decided what *would* happen, wrote, and never read back what *did*. Three
separate bugs were the same omission. *Verify the outcome before you declare
success.*

**5.3 — A 200 that did not do the thing.** Provisioning declined silently and
the route stamped `approved` anyway — the person stayed locked out *and* left
the queue forever. *Fully succeed or refuse out loud.*

**5.4 — A test fake that mirrors SQL.** A Python re-implementation of an
`ON CONFLICT` clause can only ever agree with itself: the exact mutation that
opened a privilege escalation passed all 28 cases. *Anything decided in SQL
needs a structural assertion against the statement string.* Worse, a fake that
does not recognise a predicate may fall through to matching **every** row —
make it raise.

**5.5 — Assertions weaker than their docstrings.** Eight instances in this
workstream. The recurring shapes: asserting a bare status code when two guards
return the same one; asserting a symbol is *mentioned* rather than *used*;
matching text that the code's own explanatory comment satisfies. *Where two
guards answer alike, discriminate on the detail text and on what was written.*

---

## 6. What is NOT closed — read before assuming the boundary holds

Measured 2026-08-05 against the running deployment:

| | State |
|---|---|
| Caddy strips forged identity on the api vhost | ✅ live |
| `deploy/hostinger/caddy/Caddyfile` carries the same directives | ❌ **drifted** — a config break silently reinstalls the unprotected copy |
| `GATEWAY_INTERNAL_TOKEN` distinct from `LITELLM_MASTER_KEY` | ❌ **byte-identical** (same sha256, same length) |
| Gateway `:8080` and workbench `:3001` closed to the internet | ❌ **both open**, so traffic can reach the gateway without passing Caddy |
| Backups run and land | ✅ a 22 MB dump landed 2026-08-05 via the pre-migration gate |
| A restore has ever been performed | ❌ never |

**The consequence, stated plainly:** while the internal token is the LLM key
*and* the gateway is directly reachable, the identity strip in hop 3 can be
walked around. Everything in §4 is still correct and still worth doing — an
owner predicate applied to a forged identity is not a control, and these two
items are what make the identity trustworthy. **Both are OWNER-GATE**
(credential rotation, firewall) and are registered in `work_plan.md` §6.

Nothing here blocks app development. It blocks *trusting* app development.

---

## 7. When you need something the contract does not allow

Do not route around it. Do one of:

- **The capability exists under another name** — reuse it (§3).
- **The rule is wrong for your case** — say so in the PR, name the rule, and
  record the departure in the owning spec. Departures are recorded, not
  smuggled; several in this workstream were correct.
- **The model needs extending** — that is a change to `org_access_control.md`
  and a board row, not a local decision in an app.

## 8. Verification

    uv run pytest tests/unit/test_org_access_control.py \
                  tests/unit/test_default_deny_auth.py \
                  tests/unit/test_org_access_enforcement.py \
                  tests/unit/test_admin_member_offboarding.py \
                  tests/unit/test_admin_member_purge.py \
                  tests/unit/test_signin_requests.py \
                  tests/unit/test_notes_owner_scoping.py \
                  tests/unit/test_tasks_people_scoping.py \
                  tests/unit/test_email_oauth_authorize_wiring.py -q

⚠️ **Never** `uv run pytest tests/unit/` as a directory — it hangs against the
live DB. Name the files. Never run `test_owner_bootstrap.py` against
production.

`scripts/onboarding_preflight.py` is the executable half of the readiness gate.
**An agent must hand the owner the command, never run it against prod**
(`work_plan.md` §6); `--mode local` is an agent's only mode.
