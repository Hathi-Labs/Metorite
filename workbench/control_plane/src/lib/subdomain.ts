/**
 * Per-tenant workspace hostnames — the pure half (WS-29 **MT-1f slice 1**).
 *
 * Spec: `project-docs/specs/saas_multitenancy.md` §11 MT-1f (slice-1 done-when
 * 1-8, owner rulings B1-B7, 2026-08-24) · §1.5's binding rule · R11
 * (`user_management_contract.md`).
 *
 * ## What slice 1 is, in one sentence
 *
 * `acme.metorite.com/projects` is **verified against the session and then sent
 * to `https://app.metorite.com/projects`** unless the caller genuinely belongs
 * to `acme`. Keeping the slug in the address bar is slice 2 (owner ruling B5).
 *
 * ## Why this file is pure, and why the decision lives here rather than in the proxy
 *
 * `proxy.ts` cannot be unit-tested in this tree: vitest here is node-env and
 * `import("@/auth")` cannot load `next-auth` (the measured reason
 * `signin.test.ts` and `signup.test.ts` are source pins). A decision written
 * inside the proxy would therefore have **no executable fence at all** — the
 * exact failure the tree has repaired twice (`emailOtp.ts`'s
 * `codeEntryState`, `confirmPurge.ts`'s type-to-confirm). So the proxy keeps
 * the I/O — reading the `Host` header, resolving the session, asking the
 * gateway who the caller is — and every *rule* is one of the two functions
 * below, driven as a table by `subdomain.test.ts`.
 *
 * ## The three rules that are load-bearing, each learned from the spec's own record
 *
 * 1. **The signed-out path performs NO LOOKUP OF ANY KIND** (owner ruling B4)
 *    and answers with the **same 302 to the apex** the mismatch gets (done-when
 *    6, AMENDED 2026-08-24 in repair round 1). Whether `acme` exists is not a
 *    question an unauthenticated caller may ask a hostname —
 *    `customer_console.md` §5's "no cross-org existence oracle" applied at the
 *    edge. An indistinguishability you get by *never asking* is one no later
 *    refactor can leak; one obtained by comparing two answers is one timing can
 *    unpick. ⚠️ **The clause originally left a signed-out visitor on the
 *    workspace host's own `/signin`, and that is a dead end, not a fallback**:
 *    B2 keeps every Auth.js cookie host-only on `app.<domain>`, so the OAuth
 *    `state`/`pkce` cookies would be written on `acme.<domain>` while B3's
 *    `AUTH_URL` pin sends the callback to `app.<domain>` — a callback that
 *    arrives without the checks it must verify (`InvalidCheck`); without the pin
 *    the same request instead mints a `redirect_uri` on a hostname no IdP knows.
 *    Since B2 also means signed-out is the ONLY state a workspace host can be in
 *    today, that fallthrough made every workspace hostname *a sign-in page that
 *    cannot sign you in*. Redirecting instead is B5's existing design applied to
 *    one more state, and it **strengthens** B4: the `Location` no longer echoes
 *    the caller's own hostname, so the two answers are byte-identical rather
 *    than identical-modulo-the-host-you-typed.
 * 2. **An unresolvable caller organization is treated as a MISMATCH.** Failing
 *    towards the neutral apex discloses nothing; failing towards "serve the
 *    workspace host" would serve a tenant hostname to somebody we could not
 *    place.
 * 3. **Nothing here is the authorization boundary, and nothing here binds a
 *    tenant.** The gateway binds from `resolve_identity(email)` alone
 *    (`deps._with_resolved_access`); a `Host` header is request input, and R11
 *    forbids taking the acting tenant from request input. This module decides
 *    *which hostname you should be looking at*, never *what you may see*.
 */

/**
 * Hostnames a customer may never own, because the platform already does — or
 * intends to (owner ruling B7, 2026-08-24).
 *
 * ⚠️ **This is the CANONICAL list, and it has a second consumer in another
 * language.** `apps/services/gateway/gateway/routes/signup.py` refuses these at
 * the self-serve slug gate, and `tests/unit/test_subdomain_host_vocabulary.py`
 * parses THIS file and pins the two sets equal — the
 * `test_seed_status_colours_match_the_shared_vocabulary` idiom, chosen for the
 * same reason: a hand-copied mirror goes stale and then lies. Editing this array
 * without editing the gateway's set is a red test, in that order.
 *
 * It is not a secret and not an oracle: the set is static, public and identical
 * for every caller, which is exactly what separates it from `SlugTaken`.
 *
 * ⚠️ **WIDENED 2026-08-24 (repair round 1), additively.** The owner's B7 ruling
 * named thirteen labels and every one of them is still here — the eight added
 * (`assets`, `auth`, `billing`, `dev`, `login`, `operator`, `staging`, `ws`)
 * are hostnames the platform already uses or has named work for: `operator` is
 * the Operator Console (D35), `billing` the subscription surface, `auth`/`login`
 * the sign-in hostnames a customer would assume are ours, and
 * `assets`/`ws`/`dev`/`staging` the ordinary infrastructure names. Widening a
 * reserved set is only safe while nobody can already hold one of the new labels:
 * self-serve signup ships dark, so today nobody can. A later addition must first
 * check the org table, because taking a label back is a rename, not a rule.
 */
export const RESERVED_LABELS: readonly string[] = [
  "admin",
  "api",
  "app",
  "assets",
  "auth",
  "billing",
  "cdn",
  "console",
  "dev",
  "docs",
  "help",
  "login",
  "mail",
  "operator",
  "signin",
  "signup",
  "staging",
  "static",
  "status",
  "ws",
  "www",
];

const RESERVED = new Set(RESERVED_LABELS);

/**
 * The slug's shape: a DNS-safe label, lowercase alphanumeric with internal
 * hyphens, at most 63 characters.
 *
 * ⚠️ **One home, and no copies of it in this language** (repair round 1,
 * 2026-08-24). `SignUpForm.tsx` **imports this constant** — it used to carry a
 * hand-copied literal, the kind of mirror `workbench/control_plane/AGENTS.md`
 * rule 5 names ("a mirror goes stale and then lies"). The gateway's
 * `_SLUG_RE` (`routes/signup.py`) is the other-language twin and is the real
 * fence for a submitted slug; `tests/unit/test_subdomain_host_vocabulary.py`
 * pins the two patterns equal AND scans the form for a re-grown local literal.
 *
 * A shape check is not the whole rule — `api` passes it, which is precisely the
 * live defect owner ruling B7 closes. Shape first, then the reserved set.
 */
export const SLUG_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** The one host every tenant is sent back to. Never a tenant's own. */
export function appHost(baseDomain: string): string {
  return `app.${normaliseDomain(baseDomain)}`;
}

function normaliseDomain(domain: string): string {
  return (domain ?? "").trim().toLowerCase().replace(/^\.+/, "").replace(/\.+$/, "");
}

/**
 * The workspace label carried by a `Host` header, or `null` when the host names
 * no workspace (slice-1 done-when 1).
 *
 * `null` — meaning "this is not a workspace hostname, carry on unchanged" — for
 * every one of:
 *
 * * the apex itself (`metorite.com`) and anything outside it (`evil.com`);
 * * `app.` and `api.`, and every other **reserved** label (B7);
 * * depth ≠ 1 (`a.b.metorite.com`) — a customer owns one label, not a tree,
 *   and a wildcard certificate covers exactly one level anyway;
 * * an empty label (`.metorite.com`) and anything failing {@link SLUG_RE},
 *   including a 64-character label.
 *
 * Case is folded and a port is stripped, because a `Host` header legitimately
 * carries both (`ACME.Metorite.com:3001`). A bracketed IPv6 literal is not a
 * workspace host and returns `null` rather than being parsed.
 */
export function slugFromHost(
  host: string | null | undefined,
  baseDomain: string,
): string | null {
  const base = normaliseDomain(baseDomain);
  if (!base) return null;

  let h = (host ?? "").trim().toLowerCase();
  if (!h) return null;
  // `[::1]:3001` — an address literal, never a workspace name.
  if (h.startsWith("[")) return null;
  // A Host header may carry the port; the name is what is before it.
  const colon = h.indexOf(":");
  if (colon !== -1) h = h.slice(0, colon);
  // A fully-qualified `acme.metorite.com.` is the same host.
  h = h.replace(/\.+$/, "");
  if (!h) return null;

  if (h === base) return null;
  const suffix = `.${base}`;
  if (!h.endsWith(suffix)) return null;

  const label = h.slice(0, h.length - suffix.length);
  // Depth: exactly one label below the base domain — a customer owns one label,
  // not a tree, and a wildcard certificate covers exactly one level anyway.
  //
  // ⚠️ **Belt-and-braces, and measured so** (2026-08-24): removing this line
  // changes NO behaviour, because `SLUG_RE` below rejects both a dot and the
  // empty string, so `a.b.metorite.com` and `.metorite.com` already answer
  // `null`. It is kept because "one label" is a rule someone should be able to
  // find, and it is honestly LABELLED redundant rather than presented as the
  // guard — R7: a rule with no test that can fail on it is advisory. What IS
  // fenced is the redundancy itself (`subdomain.test.ts`: "SLUG_RE alone
  // refuses a dotted or empty label"), so widening the charset to admit a dot
  // reds there rather than silently promoting this line to load-bearing.
  if (label === "" || label.includes(".")) return null;
  if (!SLUG_RE.test(label)) return null;
  if (RESERVED.has(label)) return null;
  return label;
}

/**
 * The flag, read the way every ship-dark flag in this tree is read: an equality
 * against the exact string `"true"` (`auth.ts:389`'s idiom — the
 * `SELF_SERVE_SIGNUP_ENABLED` read inside the `signIn` callback; re-anchored
 * 2026-08-24, the long-cited `:163` is a comment about the OTP provider and not
 * a flag site at all), never truthiness.
 *
 * An operator who writes `SUBDOMAIN_WORKSPACE_ENABLED=false` while debugging
 * must get OFF, and every truthy-string reading arms it instead. Flipping it on
 * a live deployment is 🔴 OWNER-GATE (`work_plan.md` §6).
 */
export function isWorkspaceEnabled(flag: string | undefined): boolean {
  return flag === "true";
}

/**
 * The workspace label this request should be checked against, or `null` when
 * there is nothing to check (slice-1 done-when 3).
 *
 * Flag OFF ⇒ always `null`, so `app.metorite.com` **and** `acme.metorite.com`
 * behave exactly as they did before this ticket — and, because the proxy only
 * resolves a session and asks the gateway when this returns non-`null`, an
 * un-flipped deployment issues **no extra request at all**.
 */
export function workspaceHostSlug(
  flag: string | undefined,
  host: string | null | undefined,
  baseDomain: string,
): string | null {
  if (!isWorkspaceEnabled(flag)) return null;
  return slugFromHost(host, baseDomain);
}

/** Everything the redirect decision is allowed to see. */
export interface WorkspaceCheck {
  /** {@link workspaceHostSlug}'s answer for this request. */
  hostSlug: string | null;
  /** Whether THIS request carried a resolved session. */
  signedIn: boolean;
  /**
   * The caller's organization slug as the GATEWAY resolved it, or `null` when
   * it could not be resolved. Never a value the browser supplied (R11).
   */
  callerSlug: string | null;
  baseDomain: string;
  /** `pathname` + `search`, exactly as it arrived. */
  pathWithQuery: string;
}

/**
 * Where to send this request, or `null` to leave the proxy pipeline unchanged
 * (slice-1 done-when 4, 5 and 6).
 *
 * | state | answer | clause |
 * |---|---|---|
 * | not a workspace host (flag off, apex, reserved, malformed) | `null` | 3 |
 * | workspace host, **signed out** | `https://app.<base><path>` — the SAME answer, byte-identical for a real and an invented slug because nothing was looked up | 6 |
 * | workspace host, signed in, slug **matches** | `null` — pass through | 4 |
 * | workspace host, signed in, slug **differs or is unresolvable** | `https://app.<base><path>` | 5 |
 *
 * The returned location **names no organization** — not the host's, not the
 * caller's. That is done-when 5's second half and it is structural: the only
 * pieces of this string are the fixed apex host and the path the caller already
 * had. Since 2026-08-24 that is also what makes done-when 6 byte-identical: an
 * answer built from a fixed host cannot echo the hostname the caller invented.
 *
 * `https` is not configurable on purpose. The only deployment where a workspace
 * host can exist is the one behind the owner-gated wildcard certificate; a
 * scheme knob here would be a way to downgrade it.
 */
export function workspaceRedirect(check: WorkspaceCheck): string | null {
  const { hostSlug, signedIn, callerSlug, baseDomain, pathWithQuery } = check;
  if (hostSlug === null) return null;

  const apex = `https://${appHost(baseDomain)}${pathWithQuery}`;

  // B4 + B5 — the signed-out answer must not depend on whether the slug is
  // real, so it is decided HERE, before anything could have asked, and it is the
  // same apex 302 a mismatch gets. Leaving this caller on the workspace host's
  // own `/signin` was the shipped shape until 2026-08-24 and it is a dead end:
  // under B2 the Auth.js cookies are host-only on the apex, so a sign-in started
  // here writes its `state`/`pkce` on a host the pinned callback never returns
  // to. See the module docstring's rule 1 for the full chain.
  if (!signedIn) return apex;

  if (callerSlug !== null && callerSlug === hostSlug) return null;
  return apex;
}
