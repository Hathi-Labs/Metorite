# The BFF Identity Boundary

**Status:** Implemented · **Date:** 2026-07-30 · **Owner:** vjvarada
**Companion to:** [`memory-clearance.md`](memory-clearance.md) §2.1 (the memory IDOR this
generalises) · [`README.md`](README.md) §8 (Phase 0)

Why omitting an identity had to start failing closed, and what changed so that it does.

---

## 1. The bug

`acb_auth/deps.py` `get_current_user` resolves a caller in three branches:

| Branch | Request | Resolves to |
|---|---|---|
| §1a | valid bearer **+** `X-User-Email` | that member, with their real permissions |
| §1b | valid bearer, **no** identity headers | `system:internal`, `SERVICE_ACCESS` — `*` |
| §2  | no valid bearer | anonymous, `NO_ACCESS` |

§1b is defensible on its own terms, and its comment says so: *"whoever holds the internal
token can already assert any X-User-Email, so a narrower set would be theatre."* That is
true. The distinction it misses is between **asserting** an identity and **omitting** one.

The Next.js BFF holds that token. Its route handlers each carried a copy of this:

```ts
const h = { Authorization: `Bearer ${INTERNAL_TOKEN}` };
const session = await auth();
if (session?.user?.email) h["X-User-Email"] = session.user.email;
return h;                      // ← no session: the bearer travels alone
```

No session is exactly what an unauthenticated HTTP request looks like by the time it
reaches a route handler. So a signed-out request did not degrade to anonymous. It took
branch §1b and arrived upstream holding every permission in the system — satisfying every
`require_permission` and `require_role` on the way.

The shared helper had it in writing, and the comment is worth preserving as the exact
shape of the mistake:

```
// auth() throws outside a request context; an unauthenticated forward is
// resolved to no-access by the gateway, which is the correct outcome.
```

It resolved to *full* access. Everything downstream was reasoning from that sentence.
`api/admin/[...path]` is the clearest case: it deliberately does no authorization of its
own, on the correct principle that "every /admin route there carries its own
require_permission, so duplicating the check here would just create a second place for it
to be wrong." Sound — while an identity is attached. Without one, the delegated check
passes for everybody.

### Reach

At the time of the fix, of 88 gateway-forwarding routes:

- **74** could emit a bearer with no identity (44 never called `auth()` at all; 30 more
  called it only to read the email, and forwarded regardless).
- **38** sat under a `proxy.ts` public prefix — `/api/agent`, `/api/settings/`,
  `/api/integrations/`, `/api/chat/`, `/api/memory/` — and so were reachable with no
  session at all.

That set included agent registration, code-mutation approval, LLM key configuration, and
the integration key inventory. Probed directly with a bearer and no identity headers, the
gateway returned `200` for `/auth/me` (as `system:internal`, roles `["agent_service"]`),
`/integrations/keys`, `/settings/llm`, `/agent/mutations/pending`, and `/actions/pending`.

`/api/auth/me` deserves separate mention: it is what the shell asks to decide which nav
panes to render, and it was answering a signed-out visitor with the service principal's
profile — `email: "system:internal"`, access `*`.

### Why it recurred

This is the third instance of one shape. It was fixed in the memory scope guard
(`memory.py` `_authorize_scope`), then again in `lib/memory.ts`, which forwarded only the
bearer and so bypassed the guard just added. Both fixes were correct and both were local.
The pattern was in ~70 files and in the *default behaviour* of the helper they shared,
so the next route reintroduced it for free.

---

## 2. The rule

**Omitting an identity must fail closed. Asserting one is fine.**

`src/lib/gateway.ts` is now the only module that reads `GATEWAY_INTERNAL_TOKEN`, and it
offers exactly two ways to get a bearer:

| Helper | Identity | Use |
|---|---|---|
| `gatewayHeaders(extra?)` | the signed-in member; **throws** `NoIdentityError` without one | every route |
| `headersActingAs(email, extra?)` | a named, already-verified member; **throws** on blank | library code handed an email by a route |
| `serviceHeaders(reason, extra?)` | none — `SERVICE_ACCESS` | the platform acting as itself |

`serviceHeaders` takes a `reason: string` that is unused at runtime. It exists so every
identity-free call carries, at the call site, the argument for why this request is on
behalf of nobody — and so reviewing a diff that adds one means reading that sentence.

Supporting pieces:

- `currentIdentity()` — the member or `null`. One resolution, including the dev fallback
  for a laptop with no SSO configured.
- `requireIdentity()` — the member, or a 401 `NextResponse` to return directly.
- `proxyToGateway()` — forwards as the member; 401s on its own when there is nobody.

Two details that are load-bearing rather than stylistic:

- **`extra` spreads *before* the identity headers.** It exists so a route can set
  `Content-Type`; it must not become a second answer to "who is asking". A test asserts a
  caller cannot override `X-User-Email` through it — this caught a real defect in the
  first draft of the helper.
- **`NoIdentityError` is thrown, not returned as `null`.** The previous helper returned
  usable headers either way, so forgetting to check was both silent and privilege-granting.
  A throw cannot be forgotten: the route either handles it or 500s, and 500 is a safe
  wrong answer where 200 was not.

### `proxy.ts` is not the boundary

Next's own guidance is that Proxy "should not be used as a full session management or
authorization solution." The public-prefix list was never an access-control policy — it
existed because redirecting a `fetch()` to an HTML sign-in page breaks the client, so
exempting API prefixes was the quickest way to stop that. It then became, by accident, the
only thing standing between an anonymous request and those routes.

That split is now explicit: signed-out **page** navigations redirect to `/signin`,
signed-out **API** calls get a 401 with the same body shape as `lib/gateway`'s
`UNAUTHENTICATED`. Only NextAuth's own `/api/auth` endpoints are exempt.

---

## 3. What changed for whom

Routes that were implicitly running as the service principal now run as the actual member,
so gateway routes carrying a real permission gate start enforcing it:

- `POST /settings/llm/key`, `/llm/tier`, `/llm/test`, the enabled/hidden model writes →
  `feature:models`
- `POST /agent` (register), `/agent/{name}/pull`, `DELETE`/`PATCH /agent/{name}` →
  `agents:manage`

Migration 130 backfills legacy `executive` → the `admin` role, whose bundle holds
`feature:*`, `agents:manage`, and `integrations:manage`; `owner` holds `*`. So admins are
unaffected. The reads behind the model picker — `GET /settings/llm`,
`/llm/enabled-models`, `/llm/context-windows`, `/llm/provider-models` — carry no gate, so
ordinary members keep a working picker and lose only the ability to *change* provider
configuration, which they never should have had.

Also removed: three routes resolved the LiteLLM `/v1` key as
`LITELLM_MASTER_KEY ?? GATEWAY_INTERNAL_TOKEN ?? "sk-local-dev-change-me"`. `deps.py`
documents those two secrets as deliberately distinct — the `/v1` key is handed to every
agent, the identity token must never be — and the fallback would have sent the identity
token to the LLM proxy. It was also inert: `require_llm_api_auth` only ever compares
against `LITELLM_MASTER_KEY`, so when that is unset the check is disabled anyway.

---

## 4. Acceptance

`src/lib/gateway.test.ts`, in two halves.

**Behaviour** — the door fails closed: no session is a throw, not a bearer; a blank email
is a throw, not a dropped header; `extra` cannot restate the identity; `serviceHeaders` is
the only bearer-only path.

**The invariant** — a static sweep over every `route.ts`, because this is the half that
has to hold. The first two fixes were behaviourally correct and did not survive contact
with the next route:

1. No route file mentions `GATEWAY_INTERNAL_TOKEN`.
2. No route builds an `Authorization: Bearer ${…}` header except from the two allow-listed
   non-gateway secrets (`LITELLM_KEY` → the `/v1` endpoint, `githubToken` →
   `api.github.com`). A third has to justify itself by name.
3. Every exported handler in a forwarding route resolves an identity before it forwards.
4. Every `serviceHeaders` call gives a non-trivial reason.
5. The sweep found ≥80 routes — so a broken path makes the suite fail rather than pass
   vacuously.

---

## 5. The same rule on the Python side

Enumerating who else holds the internal token — the step §1b's justification names but
nobody had done — turned up the identical omission in the tool clients agents call the
gateway through: `agent-whatsapp-assistant`, `agent-email-assistant`, and
`skill-my-tasks`. All three had:

```python
user = _current_user_email()
if user:
    headers["X-User-Email"] = user     # ← and if not, the bearer alone
```

with `_current_user_email` documenting the outcome as *"Without either, gateway calls are
unscoped."* Unscoped is the one thing they were not: they were scoped to everybody.

`_headers()` now raises rather than returning identity-free headers. Unlike the BFF —
where "no session" always means an unauthenticated request — a Python run can genuinely
have no user, so this needs its own argument. It is this: every surface these clients
reach is inherently per-person. A run with nobody attributed has no inbox, no chats and no
task list to act on. It has nothing to do, not everything. These modules relay raised
exceptions to the agent verbatim, so the message names the variable to set.

Acceptance is `tests/unit/test_agent_gateway_identity.py` (9 tests), in the same two
halves: behaviour, plus a source-level check that the header is never assigned
conditionally. The source check is scoped to `_headers` — `_current_user_email` uses
`if user:` legitimately to walk its fallback chain, and what must never be conditional is
the assignment that decides whether the gateway is told who is asking.

### The remaining §1b callers

One legitimate identity-free caller remains in the repo: `acb_skills.write_artifact`
`_notify`, which PATCHes `/agent/workspace/{sid}` and posts an event. It runs in the
orchestrator process, not in agent-authored script code — `_script_env` grants scripts
integration credentials, not the gateway token — so it is the platform acting as itself in
the sense §1b means.

---

## 6. Not done here

The §1b grant itself is unchanged: a bearer with no identity still resolves to
`SERVICE_ACCESS`. With both the BFF and the Python clients failing closed, the paths that
reach it are platform code holding the token, which is the population §1b's reasoning was
actually about. Narrowing it further — or requiring an explicit service-principal
assertion — is now a much smaller change than it was, but it still belongs in its own,
with the deployment's cron and CI callers confirmed against the list above.

Two adjacent things noticed and deliberately left alone, because neither is this change:

- `scripts/setup_secrets.sh` seeds `GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}` — the two
  secrets `deps.py` calls "deliberately distinct" start life as the same value, which is
  the BO-2 residual #4 separation not taking effect on a fresh install.
- `require_internal_auth` and `_get_internal_token` fail OPEN when no token is configured.
  Documented and deliberate, but it means the invariant here rests on the token actually
  being set.

Verification was per-link rather than end-to-end: the proxy list, the missing `auth()`
calls, and the gateway's response to a bearer-only request were each confirmed directly,
but no full Next-plus-gateway exploit was run.
