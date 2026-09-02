# Operator identity and access — the staff side of the Operator Console

**Status: ACTIVE — minted 2026-08-26. Verified against code on 2026-09-02,
after CP-12j.** Done-whens 1, 5 and 30 to 41 are MET, and 2 to 29 stay true.
🔴 The owner still owes H-54, and no sign-in works until they finish it.

⛔ **CP-12i added a SECOND admission mode on 2026-09-02 (D71).** The owner
assigns operators Gmail and outside addresses, so the Workspace directory
cannot describe the staff. §4.1b holds the mode, the email fallback and the
per-operator method pin. It ships dark — `OPERATOR_ADMISSION_MODE` defaults
to `directory`, and a box nobody changes keeps all three checks.

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

**◐ CP-12f BUILT 2026-08-27** (`ws31-cp12f-activity`) — the Activity
surface. `operator_activity.py` holds the page cursor, `store.activity_page`
holds the query, and `GET /activity` reads every company. 26 tests.
**13 mutations killed, 0 survived.**

⚠️ **H-7 was measured on this surface, not assumed.**
`test_a_late_commit_can_be_missed_by_a_scroll` reproduces the miss on real
Postgres 16. It also proves the row returns on the next fresh read. The
cursor is EPHEMERAL, and that bound is what migration 168 does not have.

⚠️ **F8 was found at build. See §2.** The identity stack has no front
door. CP-12g must not start before somebody builds one.

**◐ CP-12f2 BUILT 2026-08-27** (`ws31-cp12f2-front-door`) — **F8 is closed.**
`operator_signin.py` plus `POST` and `DELETE` `/operators/session`.

Supabase verifies the token. `operators.admit` then runs the three checks.
53 tests. **19 mutations killed, plus two PAIRS.**

⚠️ Done-whens 1 to 6 were written for CP-12a and **nothing could reach them**
until this slice. They are exercised through the route here for the first
time.

⚠️ **A real bypass was found by mutation testing and closed.** The gate first
asked whether a Microsoft identity was AMONG the account's linked identities.
That is not the same question as whether the sign-in came from one. A
colleague who links a personal account, and an attacker who takes it, would
have reached this console without passing through Entra. The gate now reads
`app_metadata.provider`, the sign-in provider.

🔴 **The owner must also disable manual identity linking in the Supabase
project.** `app_metadata.provider` is the strongest signal the payload
carries, and it is not a per-session claim. Turning linking off removes the
condition the bypass needs. Filed with **H-54**.

⚠️ **MEASURED 2026-09-01, and RECORDED rather than fixed.** `_signin_provider`
reads a provider NAME. It cannot separate two identities that share one name.
So an account with two `google` identities, where only the SECOND carries the
`hd`, is admitted with a 200. `_google_hd` therefore proves *"this account
holds an identity from our Workspace"*, and not *"this sign-in came from our
Workspace"*.

No outsider can reach it. The operator must link the second account to their
own Supabase user first. Turning identity linking OFF closes it, which is the
act above, so a code fix here would be a second mechanism for one problem.
H-54's linking item now carries both facts together.

⚠️ **Two more real bugs caught at build.** `request.client.host` is not always
an address, and an unparseable value made the `INET` cast raise, which turned
a valid sign-in into a 500. `safe_ip` records nothing instead. `DELETE
/operators/session` was also declared behind `/operators/{operator_id}` and
was swallowed, which is the CP-12e route-ordering bug a second time.

⚠️ **The UI is still not wired.** `workbench/operator_console` posts the old
shared passphrase. CP-12g rewires it and removes the passphrase.

**◐ CP-12g slice 1 BUILT 2026-08-27** (`ws31-cp12g-console`) — the console
itself. `identity.ts`, the Operators and Activity surfaces, the Microsoft
sign-in flow, and six BFF routes. 131 frontend tests. **14 mutations killed.**

⚠️ **Done-when 27 is NOT met, and that is the design.** `staff.ts` and
`OPERATOR_CONSOLE_STAFF_SECRET` are still here. Both paths run at once, and
`OPERATOR_IDENTITY_ENABLED` chooses.

The console has been live on the passphrase since 2026-08-22. Delete it before the owner finishes H-54 and the
team is locked out of a running console. Slice 2 deletes it, AFTER one
real sign-in is confirmed. H-56 carries the order.

⚠️ **Done-when 28 is met, and it was shown RED first.** Two fences, each
proven to fail before it passed. One catches an `/api/operator/**` route that
does not reach the gate. The other catches a route or PAGE that reaches the
Console without the caller's session.

⚠️ **The second fence found four real defects in already-merged code.** Four
page reads called the Console with no caller token. Under the session path
each would arrive as `breakglass` — past the §5 matrix, and logged as a
break-glass event on every page view.

**◐ CP-12g slice 1 AMENDED 2026-09-01** (`ws-31-login-both`) — the login page
now names the way back.

⚠️ **This is text, and it is not a second door.** `login/page.tsx` prints one
recovery note on the identity path. The note names
`OPERATOR_IDENTITY_ENABLED`, and it asks the reader to unset that variable and
restart the console. The staff passphrase works again after that restart. The
note shows in the two states that strand a reader: sign-in not configured, and
a refused Microsoft sign-in.

The page renders **no** passphrase form there. Done-when 29 holds. The gate
refuses an interim cookie while the flag is on, and `POST
/api/operator/session` then wants a Supabase `access_token`. A passphrase form
on that page would answer 400 on submit.

H-56 keeps the order. The owner removes the passphrase after one real sign-in
succeeds. Fence: `workbench/operator_console/src/app/login/login.test.ts` —
six cases, three mutations killed.

🔴 **Owner acts still owed:** H-54 configures Supabase and turns identity
linking off. H-58 names the first operators. Then the flag flip, then slice 2.
*(The Console ladder is applied. See the note under §7.)*

**⛔ THE DIRECTORY CHANGED 2026-09-01 — D70.** We have no Microsoft Entra
directory. Supabase Auth with the **Google Workspace** provider authenticates
staff instead, and the `hd` hosted-domain claim replaces the Entra `tid` as
check 1. D35.3's intent stands, which is one directory, ours, admin-managed.
Email OTP is refused for this console.

**◐ CP-12h BUILT 2026-09-01** (`ws-31-google-signin`) — the Google Workspace
gate. `OPERATOR_SIGNIN_PROVIDER` names the directory, and it defaults to
`azure`.

`operators.signin_provider`, `operators.staff_directory_id` and
`operators.directory_matches` hold the choice. `operator_signin._google_hd`
reads the `hd` claim, and it is as strict as `_azure_tid`. The login page names
the provider on the button and in the authorize link.

Done-whens 1, 5, 30, 31, 32 and 33 are met. Done-whens 2 to 29 stay true, and
their suites pass with no edit to an expectation.

**◐ CP-12h REPAIRED 2026-09-01**, on the same branch, after an independent
verification returned four blockers. Four more mutations killed. The
paragraphs below and done-whens 30 to 33 record each repair.

**◐ CP-12h REPAIRED A SECOND TIME 2026-09-01**, after a re-verification found
a fifth defect of the first round's class and three one-line items. The fifth
is `OPERATOR_SIGNIN_PROVIDER` in two containers. §4.2a is now the placement of
record, and §10 G2 names the gate. The three are the login-page wording in
`identity.ts`, an unfiltered `control_audit` count in
`test_a_refused_sign_in_writes_absolutely_nothing`, and the counts below.

⚠️ **The owner walk of H-54 and H-56 found a SIXTH, of the same class.**
`OPERATOR_CONSOLE_ORIGIN` is not one of the six values, and the sign-in button
does not render without it. H-56 step 1 said "set the six" and named no
container. An owner who followed it exactly reached a page that said "Sign-in
is not configured". Both entries now name all three console values.
§4.2a holds the reason.

**The counts, measured on `ws-31-google-signin` 2026-09-01, twice back to
back.** **150** Console tests, **0 skipped**, over the THREE suites CP-12h
touches — `tests/unit/test_operator_signin.py` (**76**) plus
`tests/unit/test_operator_identity.py` (**43**) plus
`tests/unit/test_customer_console_catalog.py` (**31**). **626** operator
console tests, **0 skipped**, over 29 files, from `npx tsc --noEmit && npx
vitest run` in `workbench/operator_console`.

⚠️ **This paragraph said 629 for the frontend until the second repair round.**
A re-measurement returned 626, and 629 reproduces on nothing. The 150 was
correct and named no suites. A verifier who took the two suites this ticket
edits measured 119, and had no way to reach 150.

⚠️ **This is not §11.** §11 runs the seven operator suites and the seam
ratchets, which is a wider net and a different number. These three are the
ticket's own command.

⚠️ **The SWITCH ships dark. The SLICE does not.** An earlier draft of this
entry said an unset variable keeps every byte of the built behaviour. That
sentence was false, and this entry withdraws it. An unset variable keeps
check 1 on the Entra `tid`. It does not keep `_email_is_verified` unchanged.

⚠️ **Done-when 31 tightens the `azure` path too.** `_email_is_verified` read a
top-level `email_confirmed_at` and then scanned every identity. It now reads
one identity, the sign-in provider's. That is a real change to the built path,
and D70 asks for it.

The verifier measured the difference on 2026-09-01. On one Entra payload with
a top-level `email_confirmed_at` and no `email_verified` on its identity, the
parent commit returned a `VerifiedIdentity` and this commit answers 401.

**One reason makes that safe today, and it is not the default.** D70 records
that we hold no Entra directory. The owner has not finished H-54, so no staff
identity exists on either path. No person signs in through `azure` today.
⚠️ **If that stops being true, this tightening bites `azure` first.**

⚠️ **`OPERATOR_SIGNIN_PROVIDER` is the SIXTH owner value, and H-54 named
five.** A box that holds the other five and not this one stays on `azure`.
Every Google sign-in then answers 401, and no message names the unset
variable. H-54 and `work_plan.md` §6.1 clause (b) both list it now.

🔴 **Nobody has proved this end to end, and TWO payload claims are
unmeasured.** Nobody has measured whether Supabase copies `hd` into
`identities[].identity_data`. **Nobody has measured `email_verified` in that
same place either.** Done-when 31 makes `email_verified is True` on the
SIGN-IN identity the only accepted proof of a verified address, and that
placement is exactly as unmeasured as `hd`.

⚠️ **If Supabase leaves the key out when the value is false, nobody signs in
on EITHER path.** H-54 item 3 asks the owner to read both claims off one real
payload. It is one read, and the second answer costs nothing.

The fences here drive a constructed payload. The code fails CLOSED on a
missing `hd`. So a guess that is wrong refuses everybody, and it admits
nobody. The owner must measure one real Google payload.

⚠️ **Read the two halves apart.** Every ◐ BUILT note above CP-12h is a true
record of what CP-12a to CP-12g slice 1 shipped, and that code named Microsoft.
CP-12h closes the gap in the code. The English of those older notes still names
Microsoft, and it stays as a record.

**Board row:** `work_plan.md` §2 — **WS-31**, ticket series **CP-12**.
**Decisions of record:** **D64** (`work_plan.md` §3), taken by the owner on
2026-08-26, which reconciles **D35.3** with **D34**. **D70**, taken by the
owner on 2026-09-01, which amends **D64.1** and moves the directory to Google
Workspace.

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
| **F8** | ✅ **CLOSED by CP-12f2 on 2026-08-27.** ~~The identity stack has no front door.~~ Found at the CP-12f build: Nothing calls `operators.admit()`. Nothing calls `operators.bootstrap()`. Nothing calls `store.operator_session_insert` except the tests. CP-12a to CP-12e verify a session that no route can issue, so every operator route today answers to the shared `breakglass` token alone. **CP-12g must not remove the passphrase before somebody builds the exchange, or the console admits nobody at all** | `[r.path for r in app.routes]` names no sign-in route |

**Read F1 to F7 together and the shape is clear.** The console is protected by a
shared password with no identity behind it. That posture was correct while the
app was dark and nobody had deployed it. The app was deployed on 2026-08-22, so
the posture is now the weakest control in the product.

⚠️ **F8 blocks the cutover.** CP-12g deletes the passphrase. Delete it
before the exchange exists and nobody can sign in at all.

⚠️ **F3 is the one that compounds.** Roles are only worth as much as the record
of who used them. If the log cannot name a person, then a role gate is a control
nobody can check after the fact.

---

## 3. The decisions this rests on — D64 and D70

**D35.3 said the console pins one Microsoft Entra directory, ours.** **D34 then
bought Supabase Auth** for the customer plane, with Microsoft as one of its
providers. Nobody reconciled the two, so the staff gate stayed an interim secret
for four months. **D64 reconciles them.**

**⛔ D70 then corrected the directory itself, on 2026-09-01.** The owner stated
that we have no Entra directory, and that `hathilabs.com` is a Google Workspace
domain with an admin console. So D64.1 named a provider we cannot configure.
D70 moves the provider to Google Workspace and the claim to `hd`.

| # | The decision says | Note |
|---|---|---|
| **D70.1** | **Supabase Auth, with the GOOGLE WORKSPACE provider, authenticates staff.** The `hd` hosted-domain claim replaces the Entra `tid` as check 1 | ⛔ **This amends D64.1.** D35.3's intent stands, which is one directory, ours, admin-managed. Only the mechanism moves |
| **D70.2** | ⛔ **AMENDED by D71.3, 2026-09-02.** **Email OTP was refused for this console outright.** It is now admitted under three conditions together, and §4.1b holds them | The tenant app's Resend OTP has a blast radius of one organization. This console reaches EVERY customer organization. Inbox control would become staff access, with no directory, no offboarding, and nobody who can revoke |
| **D70.3** | **The `hd` claim is load-bearing.** A domain match alone is not enough | Google lets a person create an account on a non-Gmail address, and verifies it by mail. Google then returns `email_verified: true` and NO `hd`. So a domain match alone admits a former employee's alias, a forward, a catch-all address, or a compromised mailbox. `hd` appears only for an account the Workspace admin manages |
| **D71.1** | **Admission has TWO modes, and `OPERATOR_ADMISSION_MODE` picks one.** `directory` is the default and keeps all three checks. `registry` skips checks 1 and 2 | An unset variable must change no box. An unknown value raises a 503, the posture `signin_provider` already takes |
| **D71.2** | **In `registry` mode the operator row is the whole gate** | Owner directive, 2026-09-02. The owner assigns operators Gmail and outside addresses. A Workspace directory cannot describe such a person, so a check that reads one refuses a real operator. Check 3 always carried the sentence that makes this safe — the directory says somebody works here, and the row says they run the platform |
| **D71.3** | ⛔ **This AMENDS D70.2. An email code may admit an operator, and only when three things hold together.** The mode is `registry`, `OPERATOR_ALLOW_EMAIL_OTP` is on, and the operator's own row permits the method | The owner needs a fallback for a person who holds no Google account. D70.2's reasoning is not withdrawn, and D71.4 is what answers it: the weakness now belongs to one named row instead of to the console |
| **D71.4** | **`operator.allowed_methods` pins a person to the methods THAT PERSON may use.** NULL means no restriction | A global code flag weakens the admin most, and the admin adds operators. So a person who reads the admin mailbox adds themselves. The owner keeps `{google}` on their own row, and the contractor carries `{email}` |
| **D71.5** | **The one-time bootstrap pins to `OPERATOR_BOOTSTRAP_EMAIL` in `registry` mode** | The old gate compares the directory claim. That comparison is always false in the new mode, and deleting it hands `admin` to the first stranger who signs in. This is the most dangerous line in D71 |
| **D71.6** | **`registry` mode moves the security boundary to WHO MAY WRITE AN OPERATOR ROW** | Named so nobody rediscovers it. In `directory` mode a mistaken row still admits nobody outside our Workspace. Here the row is the only wall, and spec §5 reserves that write to `admin` |
| **D64.1** | ⛔ **AMENDED by D70.1.** Was: Supabase Auth, with the Microsoft provider, authenticates staff | D35.3's *intent* is kept — one directory, ours. D35.3's *mechanism* is dropped. We do not stand up a second identity integration to say the same thing |
| **D64.2** | **Three checks admit an operator, and all three must pass** | The directory answers *who are you*. The operator registry answers *may you*. This is **D34.4 applied to staff**, not a new idea. ⛔ Check 1 reads `hd` since D70, not `tid` |
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

1. **The directory.** Supabase Auth returns a Google identity. The console
   asserts that the `hd` hosted-domain claim equals `OPERATOR_GOOGLE_HD`.
   ⛔ **Changed 2026-09-01 by D70.** This read *"a Microsoft identity"* and
   `OPERATOR_ENTRA_TENANT_ID` until then.
   **`OPERATOR_SIGNIN_PROVIDER` is the switch** (CP-12h). It holds `azure` or
   `google`, and it defaults to `azure`, so an unset box behaves as it did
   before D70. An unknown name is a **503**, and never a fall back to `azure`.
   The `hd` comparison folds case, because a DNS domain is case-insensitive.
   The Entra `tid` is a GUID, so that path still compares exactly.
   **R7 — the fence is
   `test_operator_identity.py::test_the_entra_tenant_id_still_compares_exactly`**,
   added 2026-09-01. Until then this sentence had no fence at all. A mutation
   that folded case on the `azure` path left the whole suite green.
   ⚠️ **The fence pins built behaviour, and the cost belongs beside it.** An
   Entra directory that returned an upper-case GUID against a lower-case
   `OPERATOR_ENTRA_TENANT_ID` would refuse every operator. D70 says we hold no
   such directory. A reader who revives that path must take the fold as a
   decision, and edit the test.
2. **The domain.** The email domain is in `OPERATOR_STAFF_DOMAINS`.
3. **The registry.** A row exists in `operator` for that email, and its status is
   `active`.

⚠️ **Check 3 is not redundant.** Without it, every person our directory ever
admits becomes a platform operator on their first sign-in. The directory tells
us a person works here. It does not tell us they run the platform.

⚠️ **Check 1 is not redundant either, and check 2 cannot stand in for it.**
Google issues an account on any address it can verify by mail, and such an
account carries **no `hd` claim at all**. It still carries `email_verified:
true`. So a gate that reads the email domain alone admits anybody who receives
mail at a staff domain. That set holds a former employee's alias, a forward, a
catch-all address, and a compromised mailbox.

**`hd` is what makes the account admin-managed.** Google sets it only for an
account inside a Workspace domain, which is an account our admin created and
our admin can delete. That is the same property the Entra `tid` gave us, and it
is the reason D70 could move the mechanism without moving D35.3's intent.

⚠️ **Email OTP was not a way in, and D71.3 narrowed that on 2026-09-02.**
The tenant app offers a Resend 6-digit code. This console refused one
outright. It now admits one under three conditions together. §4.1b holds
them, and the third is the operator's own row.

A console that reaches every customer organization must still never admit a
person on inbox control alone. D71.4 keeps that true for everybody the owner
does not name.

### 4.1b `registry` admission — the second mode (D71)

⛔ **The owner took this on 2026-09-02, and it reverses part of D70.2.**
Read §4.1 first. This section states only what changes.

**Why the directory check had to become optional.** The owner assigns
operators Gmail and outside addresses. `hathilabs.com` describes some staff
and never all of them. So check 1 refuses a real operator, and check 2 refuses
the same person again.

**What `registry` mode does.** `OPERATOR_ADMISSION_MODE=registry` tells
`operators.admit` to skip check 1 and check 2. Check 3 stays, and no mode ever
skips it. `directory` is the default, so a variable nobody sets changes nothing.

🔴 **What the mode costs, stated once so nobody rediscovers it.** In
`directory` mode a mistaken operator row still admits nobody outside our
Workspace. In `registry` mode that row is the only wall. So the question
*"who may write an operator row?"* becomes the whole security boundary of this
console. Spec §5 reserves that write to `admin`, and D71.6 records the shift.

**The email code, and the three conditions it needs.** D70.2 refused an email
code outright. D71.3 admits one, and only when all three of these hold:

1. `OPERATOR_ADMISSION_MODE` is `registry`.
2. `OPERATOR_ALLOW_EMAIL_OTP` is on.
3. The operator's own row permits the `email` method.

⚠️ **Condition 3 is what answers D70.2's reasoning.** A global flag weakens
every operator, and it weakens the `admin` most, because the `admin` adds
operators. A person who reads the admin mailbox therefore adds themselves.
`operator.allowed_methods` moves that weakness onto one named row. The owner
keeps `{google}`, and the contractor who needs the fallback carries `{email}`.

⚠️ **A contradictory pair raises a 503.** `OPERATOR_ALLOW_EMAIL_OTP` on a
`directory` box can admit nobody, because that path demands a claim an email
code never carries. Reading the flag as false would leave the person who set
it believing a fallback works. `admit` reads `accepted_methods`
unconditionally for that reason.

⚠️ **D71.3 opened `email` and nothing else.** `magiclink`, `otp`, `phone` and
`sms` stay outside `accepted_methods` in every mode. `ALLOWED_PROVIDERS` still
holds no passwordless member, because that set names a DIRECTORY and a
directory is a different axis from a method.

🔴 **The bootstrap gate moved, and it is the most dangerous line in D71.**
The old gate reads the directory claim. That comparison is always false in
`registry` mode, so the bootstrap could never fire. The obvious repair is to
delete the clause, and that repair hands `admin` to the first stranger who
signs in. `operators.bootstrap_allowed` keeps the claim comparison in
`directory` mode and pins to `OPERATOR_BOOTSTRAP_EMAIL` in `registry` mode.
A bootstrap email nobody sets admits nobody.

✅ **CP-12j built the page half on 2026-09-02.** `login/page.tsx` shows the
code form BESIDE the directory button, never instead of it. `lib/otp.ts` says
when, and the browser talks to Supabase directly — the same way the OAuth
button already does, so this app gains no new upstream.

🔴 **`should_create_user` must be TRUE, and a production flip proved it.**
Supabase mails a code only to a user that already exists in `auth.users`. That
table held ZERO rows on 2026-09-02, because nobody had ever signed in. So the
first version of `otpStartBody` refused every operator forever, including the
first one.

A Supabase user is not an operator, and that is what makes `true` safe. The
registry answers 403 to a stranger (**D71.2**, **D71.6**), so they gain a login
to nothing. R7 — the fence is
`otp.test.ts::"asks Supabase to CREATE the user, or nobody ever signs in"`.

🔴 **The page hands the anon key to every visitor, and one fence makes that
safe.** `isPublishableKey` refuses a `service_role` JWT and an `sb_secret_`
key, and it refuses any shape it cannot parse. The `service_role` key sits one
line away in the same Supabase dashboard and bypasses row-level security on
every table. A person who pastes the wrong one publishes it to a login page,
and nothing else would look wrong.

### 4.2 Where the identity lives — the Console, not the Next app

**The operator tables belong to the Customer Console's own migration ladder,
`infra/customer_console/`.** The Operator Console stays a browser-facing
front-end with no database of its own.

The reason is the seam rule. The Console is already the cross-tenant plane. It
already holds `control_audit`, the four authentication schemes and the operator
door. A database in the Next app would be a second data plane for one subject.
The repo forbids a second way to do an existing thing.

#### 4.2a Two containers, and which env value goes in each

*Added 2026-09-01, by the second CP-12h repair round. The identity lives in one
plane, but the CONFIGURATION does not.* The API and the Next app are separate
processes with separate env files. The API is `acb-customer-console.service`,
and it reads `apps/services/customer_console/.env`. The Next app reads its own.

| Value | API | Next app | Read at |
|---|---|---|---|
| **`OPERATOR_SIGNIN_PROVIDER`** | ✅ | ✅ | `operators.signin_provider` · `identity.ts::signinProvider` |
| `OPERATOR_SUPABASE_URL` | ✅ | ✅ | `operator_signin` · `login/page.tsx` |
| **`OPERATOR_SUPABASE_ANON_KEY`** | ✅ | ✅ **BOTH since CP-12j** | `operator_signin` · `lib/otp.ts::emailCodeConfig` |
| `OPERATOR_GOOGLE_HD` | ✅ | — | `operators.staff_directory_id` |
| `OPERATOR_STAFF_DOMAINS` | ✅ | — | `operators` |
| `OPERATOR_BOOTSTRAP_EMAIL` | ✅ | — | `operators` |
| `OPERATOR_CONSOLE_ORIGIN` | — | ✅ | `login/page.tsx` |
| `OPERATOR_IDENTITY_ENABLED` | — | ✅ | `identity.ts::identityMode` |
| `OPERATOR_ADMISSION_MODE` | ✅ | — | `operators.admission_mode` (**D71.1**) |
| **`OPERATOR_ALLOW_EMAIL_OTP`** | ✅ | ✅ **BOTH** | `operators.email_otp_allowed` · `lib/otp.ts` (**D71.3**) |

⚠️ **A one-container copy of the switch fails QUIETLY, and this is why the row
is bold.** The Next app builds the Supabase authorize link from it. The API
computes the expected provider from it. The two must agree, and `identity.ts`
has said so since CP-12g. Set it in the API alone, and the page still offers
Microsoft. Set it in the Next app alone, and the gate answers **401** with no
message that names the cause.

🔴 **`OPERATOR_CONSOLE_ORIGIN` is a Next-app value, and sign-in cannot work
without it.** `login/page.tsx` builds the authorize link out of it, and an
unset value prints "Sign-in is not configured on this deployment" in place of
the button. So the Next app needs THREE values before the flag flip. H-56 step
1 named none of them until 2026-09-01.

Fence: none. This is a deployment fact, and no test can see the box.
`HANDOFF.md` H-54 carries the owner's copy of the table, and §10 G2 names the
gate.

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

✅ **The ladder is APPLIED on production. Measured 2026-09-01.** The `operator`
table exists and holds two `active` `admin` rows. Migrations 019, 020 and 021
are recorded as well, so the Console ladder is well past this migration. H-64
carried the apply and the owner closed it.

⛔ **The `directory_subject` comment says *"the Entra object id"*, and the
migration has shipped.** D70 makes that value the **Google subject** instead.
Do not rewrite an applied migration. A later slice corrects the comment in a
new file, or leaves it and states the meaning here.

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
| **CP-12f** | ◐ **BUILT 2026-08-27.** `operator_activity.py` plus `GET /activity` and `GET /activity/actions`. Keyset-paginated, cross-org, `viewer`-readable. The `LEFT JOIN` keeps org-less and purged-company rows visible. 26 R8 tests, **13 mutations killed and 0 survived**. ⚠️ H-7 is reproduced by a test, not assumed. ⚠️ Found **F8** at build | CP-12b | 🟢 **AGENT-SAFE** |
| **CP-12f2** | ◐ **BUILT 2026-08-27. F8 is closed.** `operator_signin.py`, `POST` and `DELETE` `/operators/session`, and the one-time bootstrap through the door. Supabase verifies the token, then `operators.admit` runs the three checks. Done-whens 1 to 6 are reachable for the first time. 53 R8 tests, **19 mutations killed and two PAIRS**. ⚠️ A real bypass was closed at build, and 🔴 the owner must disable identity linking in Supabase. Was: The Supabase sign-in exchange: a route that takes a verified Microsoft identity, calls `operators.admit()`, and mints the `cc_sess_` session CP-12b already verifies. §8.1 done-whens 1 to 6 are UNREACHABLE without it. See **F8** | CP-12a | 🟢 **AGENT-SAFE** to build. 🔴 The provider configuration stays **OWNER-GATE** |
| **CP-12h** | ◐ **BUILT 2026-09-01.** The Google Workspace gate of **D70**. `OPERATOR_SIGNIN_PROVIDER` picks the directory and **defaults to `azure`**, so the built behaviour is unchanged until the owner flips it. `operators.signin_provider` refuses an unknown name with a 503, and `ALLOWED_PROVIDERS` can hold no passwordless provider. `staff_directory_id` reads `OPERATOR_GOOGLE_HD` on the Google path, and it still raises instead of answering `None`. `directory_matches` is the ONE answer to *"did this sign-in come from our directory"*, and a missing claim is always `False`. `_google_hd` reads `hd` from the sign-in identity alone. `_email_is_verified` now reads one identity, which tightens the `azure` path too. The login page drives the button and the authorize link from the same variable. Done-whens 1, 5 and 30 to 33. 117 R8 tests and 624 console tests, 0 skipped, **11 mutations killed**. ⚠️ **The `hd` payload shape is still unmeasured** (H-54 item 3), and the gate fails CLOSED on it | CP-12f2, CP-12g slice 1 | 🟢 **AGENT-SAFE** to build. 🔴 Setting `OPERATOR_SIGNIN_PROVIDER` and `OPERATOR_GOOGLE_HD`, and configuring the Google provider, stay **OWNER-GATE** |
| **CP-12g** | ◐ **SLICE 1 BUILT 2026-08-27, AMENDED 2026-09-01.** The amendment prints a recovery note on the login page. It names `OPERATOR_IDENTITY_ENABLED` and it adds no passphrase form, so done-when 29 holds. The console itself: `identity.ts`, the Operators and Activity surfaces, the Microsoft sign-in flow, six BFF routes, and BOTH F7 fences shown red first. 131 frontend tests, **14 mutations killed**. The fence found four already-merged page reads that dropped the caller session. ⚠️ **Slice 2 is the deletion, and it waits for the owner.** Was: Delete `staff.ts`. Remove `OPERATOR_CONSOLE_STAFF_SECRET`. Add the route-coverage fence that closes **F7**. ⚠️ **Blocked by CP-12f2.** Remove the passphrase before the exchange exists and the console admits nobody | all, and **CP-12f2 first** | 🟢 **AGENT-SAFE** to build. 🔴 The flag flip and the secret removal are **OWNER-GATE** |

### 8.1 Done-when, per ticket

⛔ **Done-whens 1, 5 and 30 to 33 changed or arrived on 2026-09-01 with D70.**
The built code names Microsoft. These state the Google Workspace gate a later
slice must build. Every other done-when below is unchanged and still binds.

**CP-12a**
1. A Google identity whose `hd` is not ours is refused **403**, and the console
   logs the refusal. ⛔ **Rewritten 2026-09-01 (D70).** This read *"a Microsoft
   identity whose `tid` is not ours"*.
2. An email outside `OPERATOR_STAFF_DOMAINS` is refused **403**.
3. An email with no `operator` row is refused **403**, even when checks 1 and 2
   pass.
4. An `operator` row whose status is `suspended` or `deactivated` is refused.
5. An unset `OPERATOR_GOOGLE_HD` fails **closed** with a **503**, the same
   way `staff.ts` does today. ⛔ **Rewritten 2026-09-01 (D70).** This named
   `OPERATOR_ENTRA_TENANT_ID`.
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

**The Google Workspace gate — added 2026-09-01 by D70**

✅ **CP-12h met all four on 2026-09-01.** Each one carries a fence below, and
each fence went red under a mutation before it passed.

30. 🔴 **A Google identity carrying NO `hd` claim is refused 403.** This holds
    even when the email domain is in `OPERATOR_STAFF_DOMAINS` and an active
    `operator` row exists for that email. **This is the personal-Google-account
    attack, and it is the most important case on this list.** A missing claim
    is a refusal, and never a pass.
    ✅ **MET.** `operators.directory_matches` reads an absent claim as `False`.
    Fences: `test_operator_signin.py::test_a_google_identity_with_no_hosted_domain_is_refused_403`
    and `test_operator_identity.py::test_a_google_identity_with_no_hosted_domain_is_refused`.
    Each case also admits the same person WITH the claim, so a broken path
    cannot pass for the wrong reason.
31. **`_email_is_verified` reads `email_verified` only from the SIGN-IN
    provider's identity.** A second linked identity does not satisfy it. ⚠️ The
    built function scans EVERY identity in the payload today
    (`operator_signin.py`), so this done-when names a real gap, not a
    restatement.
    ✅ **MET.** The function takes the provider and reads one identity. It no
    longer accepts a top-level `email_confirmed_at`, which tightens the `azure`
    path too. Fences:
    `test_operator_signin.py::test_a_second_identity_cannot_prove_this_sign_in_s_address`,
    `::test_a_top_level_confirmation_no_longer_stands_in` and
    `::test_the_verified_flag_is_pinned_on_the_entra_path_too`.
32. **The bootstrap gate fires on the `hd` match, and never on a missing
    directory claim.** The gate sits at `main.py:1178` and reads
    `identity.tid == operators.staff_tenant_id()` today. Two `None` values
    compare equal in Python, so an identity with no directory claim would
    consume the one-time bootstrap path. **Show this red first.**
    ✅ **MET.** The gate now calls `operators.directory_matches`, and
    `operators.staff_directory_id` raises instead of answering `None`. Both
    properties must hold, because the hole needs only one of them to fail.
    Fences: `test_operator_identity.py::test_the_directory_getter_never_returns_none`
    and `::test_a_missing_claim_never_matches_the_directory`. Both went red
    under mutation.

    ⚠️ **REPAIRED 2026-09-01. The route fence was INERT, and this entry said
    otherwise.** The sentence here read *"MET, with two guards, and the hole
    needs only one of the two to open"*. That is true of the two helper
    properties. It was **false of the CALL SITE**, which is a single point
    with no second guard behind it. A verifier deleted the `and
    operators.directory_matches(...)` clause in `main.py` and the whole suite
    stayed green, then instrumented `operators.bootstrap` and proved the
    bootstrap really fired.

    The row count was never the property. One `get_engine().begin()`
    transaction wraps the whole route, and the 403 rolls it back, so
    `count(*) FROM operator` reads zero either way.
    `test_operator_signin.py::test_the_bootstrap_never_fires_on_a_missing_directory_claim`
    now asserts on a spy over `operators.bootstrap`, and it fails on that
    mutation. `::test_a_stranger_cannot_consume_the_bootstrap` carries the
    same spy, because it made the same claim.
33. **The allowed sign-in provider set can never hold a passwordless
    provider.** `email`, `magiclink`, `otp`, `phone` and `sms` stay out of it,
    per **D70.2**. **R7 — the fence is
    `tests/unit/test_operator_signin.py::test_no_passwordless_provider_is_ever_allowed`**,
    which reads the allowlist constant and fails on any member of that set.
    ✅ **MET.** `operators.ALLOWED_PROVIDERS` and
    `operators.PASSWORDLESS_PROVIDERS` are the two constants, and
    `operators.signin_provider` refuses any name outside the first one.

    ⚠️ **REPAIRED 2026-09-01. `DIRECTORY_CLAIM`'s VALUES were dead.**
    `ALLOWED_PROVIDERS = frozenset(DIRECTORY_CLAIM)` made the KEYS live. The
    values were not: `_azure_tid` and `_google_hd` wrote `tid` and `hd`
    themselves. A verifier changed `GOOGLE_PROVIDER: "hd"` to `"email"` and the
    whole suite stayed green. **We made the readers consume the table**, rather
    than relabel it as documentation, because CLAUDE.md §5 refuses a second
    copy of a vocabulary that already has an owner. Both readers now call
    `operator_signin._claim_name`. R7 — the fence is
    `test_operator_identity.py::test_the_claim_table_is_what_the_readers_read`,
    which renames both claims and asserts each reader follows.

34. **`OPERATOR_ADMISSION_MODE` defaults to `directory`, so D71 ships dark.**
    An unset variable leaves every box on the D64/D70 three-check path. An
    unknown value raises a 503 and names the variable. **R7 — the fences are**
    `test_operator_signin.py::test_the_default_admission_mode_is_directory`
    and `::test_an_unknown_admission_mode_refuses_rather_than_falls_back`.
    ✅ **MET.** A mutation that flips the default turns **15 tests red**.

35. **In `registry` mode an identity with no operator row is refused.**
    The payload may carry a verified address outside every staff domain, and
    no directory claim at all. Check 3 refuses it alone. **R7 — the fence is**
    `::test_registry_mode_still_refuses_a_stranger_with_no_row`.
    ✅ **MET.**

36. **In `registry` mode a Gmail operator with a row is admitted, and the
    same person is refused in `directory` mode.** The pair is what makes D71 a
    mode rather than a widening. **R7 — the fences are**
    `::test_registry_mode_admits_a_gmail_operator_that_an_admin_added` and
    `::test_directory_mode_still_refuses_that_same_gmail_operator`.
    ✅ **MET.**

37. 🔴 **The bootstrap never fires for a stranger in `registry` mode.**
    `bootstrap_allowed` pins to `OPERATOR_BOOTSTRAP_EMAIL`, and a value nobody sets
    admits nobody. **R7 — the fences are**
    `::test_registry_mode_never_bootstraps_a_stranger`,
    `::test_registry_mode_never_bootstraps_when_no_email_is_named` and
    `::test_directory_mode_bootstrap_is_byte_for_byte_unchanged`.
    ⚠️ **Each one asserts on the CALL through the `bootstrap_calls` spy, and
    carries a positive control.** Done-when 32 recorded why: the route runs in
    one transaction that rolls back on the 403, so a row count reads zero with
    the guard deleted.
    ✅ **MET.** Two mutations, `return True` and an unset email that reads as
    anybody, both go red.

38. **An email code admits nobody until THREE conditions hold together.**
    The mode is `registry`, the flag is on, and a row exists. Registry mode
    alone does not open the inbox path. **R7 — the fences are**
    `::test_an_email_code_is_refused_while_the_flag_is_off`,
    `::test_an_email_code_admits_a_named_operator_when_the_flag_is_on`,
    `::test_an_email_code_still_needs_a_registry_row` and
    `::test_an_unverified_email_code_is_refused`.
    ⚠️ **The refusal is a 401 and not a 403**, because `extract_identity`
    rejects the token before `admit` reads any row. A 403 there would mean the
    console consulted the registry for a method it never admitted.
    ✅ **MET.**

39. **`OPERATOR_ALLOW_EMAIL_OTP` set against a `directory` box raises a 503,
    and never reads as false.** The flag can admit nobody on that path, so
    whoever set it believes a fallback works that does not. **R7 — the fence
    is** `::test_the_otp_flag_contradicting_the_mode_is_a_503`.
    ⚠️ **`admit` reads `accepted_methods` UNCONDITIONALLY** and discards the
    answer on the directory path, because a directory box is exactly where
    somebody sets this flag by mistake.
    ✅ **MET.**

40. **`operator.allowed_methods` restricts the person in BOTH modes and in
    BOTH directions.** A row naming `{google}` refuses an email code. A row
    naming `{email}` refuses Google. NULL, a missing column and an empty array
    all mean no restriction. **R7 — the fences are**
    `::test_a_row_pinned_to_google_refuses_an_email_code`,
    `::test_a_row_pinned_to_email_refuses_google`,
    `::test_the_pin_applies_in_directory_mode_too`,
    `::test_a_null_pin_admits_whatever_the_box_allows`,
    `::test_row_methods_reads_null_and_empty_as_no_restriction` and
    `::test_the_database_refuses_a_pin_that_admits_nobody`.
    ⚠️ **NULL and the empty set are not one answer.** NULL is what every row
    written before migration 022 means, so R6 keeps those rows working. An
    empty array admits nobody, and migration 022 refuses one in the database.
    ✅ **MET.** A mutation that wires the pin into the registry branch alone
    goes red, and so does one that stops folding case.


41. **The login page offers the code form only when it can WORK, and never
    instead of the directory button.** Four states render nothing: the flag is
    off, the project URL is unset, the key is unset, and the key is not
    publishable. **R7 — the fences are** `src/lib/otp.test.ts` (10 cases) and
    `src/app/login/login.test.ts`
    `::"renders NO form for a service_role key"`,
    `::"shows the form BESIDE the directory button, never instead of it"` and
    `::"ships dark — no form until the flag is on"`.
    🔴 **The service_role case is the one that matters.** Rendering the form
    PUBLISHES the key, so the page must refuse before it renders. The fence
    also asserts the secret appears nowhere else in the element tree.
    ⚠️ **A reader with a working code form is NOT stranded**, so the recovery
    note stays off for them and returns when neither door works.
    ✅ **MET.** 642 Operator Console tests pass.

---

## 9. Named deferrals, and what pulls each one in

**These are not oversights.** Each one is written down with the trigger that
turns it into a ticket.

### 9.1 🔴 DEF-1's trigger HAS FIRED — 2026-09-01

**A second admin exists.** A production read on 2026-09-01 found two `active`
`admin` rows in the `operator` table. They are `nithin@hathilabs.com` and
`vjvarada@hathilabs.com`, both created 2026-08-30. DEF-1's trigger reads *"a
second admin exists"*, so the trigger has fired.

**This section exists because DEF-1 says the trigger must not pass unnoticed.**
The table below states triggers, and it had no place to record a firing. That
is the gap this section closes. Nobody may delete this note by deferring DEF-1
again.

⚠️ **This records the firing. It does not build four-eyes approval.** Four-eyes
needs its own slice, its own acceptance and a board row. `work_plan.md` §6.0
**C4** carries the owner half, and **H-93** carries the queue entry.

| # | Deferred | Trigger that pulls it in |
|---|---|---|
| **DEF-1** | 🔴 **FIRED 2026-09-01. See §9.1.** Four-eyes approval on purge, suspend and large credit grants | A **second admin exists**, or the first SOC 2 engagement starts. Four-eyes needs two people to mean anything, and today it would only lock the owner out. ⚠️ This is the control that holds when everything else has failed, so the trigger must not be allowed to pass unnoticed |
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
| **G1** | Configuring the Supabase Auth **Google Workspace** provider, and holding its client secret. ⛔ **Renamed 2026-09-01 by D70.** This said *"Microsoft provider"* | §6.0 B — external accounts and credentials |
| **G2** | Setting `OPERATOR_SIGNIN_PROVIDER`, `OPERATOR_GOOGLE_HD`, `OPERATOR_STAFF_DOMAINS` or `OPERATOR_BOOTSTRAP_EMAIL` on the box. ⛔ **Renamed 2026-09-01 by D70.** This said `OPERATOR_ENTRA_TENANT_ID`. CP-12h added the first name, which is the switch between the two directories. ⚠️ **`OPERATOR_SIGNIN_PROVIDER` goes in TWO env files, and so does `OPERATOR_SUPABASE_URL`.** §4.2a holds the split, and `HANDOFF.md` H-54 holds the owner's table | `env-write` |
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
uv run pytest tests/unit/test_operator_activity.py -q
uv run pytest tests/unit/test_operator_signin.py -q

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
