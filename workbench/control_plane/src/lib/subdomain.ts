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
 * 1. **The signed-out path performs NO LOOKUP OF ANY KIND** (owner ruling B4).
 *    Whether `acme` exists is not a question an unauthenticated caller may ask
 *    a hostname — `customer_console.md` §5's "no cross-org existence oracle"
 *    applied at the edge. An indistinguishability you get by *never asking* is
 *    one no later refactor can leak; one obtained by comparing two answers is
 *    one timing can unpick.
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
 */
export const RESERVED_LABELS: readonly string[] = [
  "admin",
  "api",
  "app",
  "cdn",
  "console",
  "docs",
  "help",
  "mail",
  "signin",
  "signup",
  "static",
  "status",
  "www",
];

const RESERVED = new Set(RESERVED_LABELS);

/**
 * The slug's shape, byte-identical to the gateway's `_SLUG_RE`
 * (`routes/signup.py`) and to `SignUpForm.tsx`'s advisory mirror: a DNS-safe
 * label, lowercase alphanumeric with internal hyphens, at most 63 characters.
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
 * against the exact string `"true"` (`auth.ts:163`'s idiom), never truthiness.
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
 * | workspace host, **signed out** | `null` — the ordinary `/signin` path, and identical for a real and an invented slug because nothing was looked up | 6 |
 * | workspace host, signed in, slug **matches** | `null` — pass through | 4 |
 * | workspace host, signed in, slug **differs or is unresolvable** | `https://app.<base><path>` | 5 |
 *
 * The returned location **names no organization** — not the host's, not the
 * caller's. That is done-when 5's second half and it is structural: the only
 * pieces of this string are the fixed apex host and the path the caller already
 * had.
 *
 * `https` is not configurable on purpose. The only deployment where a workspace
 * host can exist is the one behind the owner-gated wildcard certificate; a
 * scheme knob here would be a way to downgrade it.
 */
export function workspaceRedirect(check: WorkspaceCheck): string | null {
  const { hostSlug, signedIn, callerSlug, baseDomain, pathWithQuery } = check;
  if (hostSlug === null) return null;
  // B4 — the signed-out answer must not depend on whether the slug is real, so
  // it is reached before anything could have asked.
  if (!signedIn) return null;
  if (callerSlug !== null && callerSlug === hostSlug) return null;
  return `https://${appHost(baseDomain)}${pathWithQuery}`;
}
