/**
 * The checkout proxies' shared half: the credential, the gate, the relay.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 8 and its
 * B7 block · `customer_console.md` §6 CP-9 §9.3 / §9.3a for the route shapes.
 *
 * ## Why the gate lives HERE and not at the gateway
 *
 * The workbench's ordinary BFF rule is *"nothing here grants anything, and
 * every real request is authorized again at the gateway"*
 * (`api/auth/me/route.ts:8-10`). **These routes do not go to the gateway.**
 * They call the Customer Console directly with this deployment's own
 * `cc_live_…` organization key, so there is no second authorization anywhere
 * downstream: for the checkout routes the BFF is the only gate there will ever
 * be. That is the whole argument of B7, and it is why the capability is
 * resolved server-side and refused **before** the upstream call rather than by
 * hiding a button.
 *
 * ## No new environment variables
 *
 * `CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_ORG_KEY` are the same two the
 * shipped read proxy reads (`summary/route.ts:34-36`), and the fail-closed 503
 * is the same one. A third configuration surface for one credential is how a
 * deployment ends up half-configured in a way nobody can see.
 *
 * ⚠️ **Read deliberately at call time, not at module load.** `summary/route.ts`
 * captures both into module constants, which is fine for a file nobody tests;
 * it also makes the value unchangeable from a test, which would force the fence
 * to assert against whatever the CI environment happened to hold.
 */
import { NextResponse } from "next/server";

import { GATEWAY_URL, currentIdentity, headersActingAs } from "@/lib/gateway";
import { hasCapability, type Access } from "@/lib/access";

/**
 * The slug minted for this surface. Mirrored in
 * `acb_auth.permissions.CAPABILITIES` and seeded onto `default`'s `admin` by
 * the tenant migration whose fence is
 * `tests/unit/test_billing_purchase_capability.py`.
 */
export const PURCHASE_CAPABILITY = "billing:purchase";

/** Where the Customer Console lives, and this deployment's own key. */
export function consoleConfig(): { url: string; key: string } | null {
  const url = (process.env.CUSTOMER_CONSOLE_URL ?? "").replace(/\/$/, "");
  const key = process.env.CUSTOMER_CONSOLE_ORG_KEY ?? "";
  return url && key ? { url, key } : null;
}

/**
 * Fail CLOSED and say what is missing — `summary/route.ts:44-55`'s wording,
 * kept identical so a misconfigured deployment reads the same sentence on
 * every billing route.
 */
export function notConfigured(): NextResponse {
  return NextResponse.json(
    {
      detail:
        "Billing is not configured for this deployment (CUSTOMER_CONSOLE_URL / CUSTOMER_CONSOLE_ORG_KEY).",
    },
    { status: 503 },
  );
}

export interface Purchaser {
  email: string;
  /** The caller's own organization slug — the one the key was checked against. */
  organization: string;
}

/**
 * The organization this deployment's key belongs to, cached per process.
 *
 * ⚠️ **The cache is per WORKER and the key is fixed by process env**, so a key
 * rotated to a different organization needs a restart to be seen here.
 */
const KEY_ORGANIZATION = new Map<string, string>();

/** Test seam: each fence case scripts its own whoami and must start clean. */
export function resetKeyOrgCache(): void {
  KEY_ORGANIZATION.clear();
}

/**
 * Whose key this is, from the Console's own read-only whoami (`main.py:1211`).
 *
 * `null` means *we could not establish it* — an unreachable Console, a rotated
 * or revoked key (401), a payload without a slug. Never cached: one blip must
 * not leave a live deployment permanently unable to buy.
 */
async function keyOrganization(config: {
  url: string;
  key: string;
}): Promise<string | null> {
  const cacheKey = `${config.url}|${config.key}`;
  const cached = KEY_ORGANIZATION.get(cacheKey);
  if (cached) return cached;

  try {
    const res = await fetch(`${config.url}/me`, {
      headers: { Authorization: `Bearer ${config.key}` },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { slug?: unknown };
    const slug = typeof body?.slug === "string" ? body.slug : "";
    if (!slug) return null;
    KEY_ORGANIZATION.set(cacheKey, slug);
    return slug;
  } catch {
    return null;
  }
}

/**
 * The signed-in member, IF they may spend the company's money.
 *
 * Returns a `NextResponse` to hand straight back on refusal:
 *
 *     const who = await requirePurchaser();
 *     if (who instanceof NextResponse) return who;
 *
 * Three properties, each one a criterion rather than an implementation detail:
 *
 * 1. **401 for a signed-out caller**, 403 for a signed-in one without the
 *    capability. Two statuses because they are two different facts and the
 *    page's copy differs: one says sign in, the other says ask an owner.
 * 2. **The capability comes from the gateway's `/auth/me`**, resolved
 *    server-side over the session's identity through `headersActingAs` — the
 *    same hop `api/auth/me/route.ts` already makes. Never from a header, a
 *    body or a client-supplied claim (`user_management_contract.md` R3/R11).
 *    `Access.capabilities` is the server's RESOLVED answer, so an owner
 *    holding `*` passes without holding the literal string
 *    (`admin/me.py:164-166`).
 * 3. **It refuses before the Customer Console is touched.** A 403 issued after
 *    the money route was hit is a different and worse bug, which is why the
 *    fence asserts the Console was never fetched rather than merely asserting
 *    the status.
 *
 * ⚠️ **Every failure of the resolution itself is a 403, not a 500 or a 200.**
 * A gateway that is down, a non-JSON answer, a payload with no `capabilities`
 * array — all of them mean *we could not establish that this person may buy*,
 * and the safe answer to that is no. The read path's habit of degrading to
 * `NO_ACCESS` and rendering (`api/auth/me/route.ts`) is right for deciding
 * what to draw and wrong for deciding whether to spend.
 *
 * ## 4. The caller's organization must BE the key's *(added 2026-08-19)*
 *
 * The capability answers *"may this person buy?"*; the credential decides
 * *"whose account is charged"* — and `org_placement` is **N organizations to
 * one deployment**, so those are two different organizations until something
 * compares them. Nothing did. The day capability seeding parameterizes (B7
 * clause 2's org-provisioning ticket), an admin of org B passes this gate and
 * buys **into org A**: seats granted to the key's organization, from a person
 * with no standing in it, and no refusal anywhere on the path.
 *
 * So the caller's slug (`/auth/me`'s `organization.slug` — the CALLER's, per
 * `admin/me.py:112-133`) is compared against the key's, learned from the
 * Console's read-only whoami. Mismatch is a **403 naming the caller's own
 * organization** and never the key's: which tenant owns this deployment's
 * billing account is a cross-tenant fact the caller has no need for. An
 * unresolvable key organization is the **unavailable 503** — never a pass, and
 * never an admission of what the deployment is or is not wired to.
 */
export async function requirePurchaser(): Promise<Purchaser | NextResponse> {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  let access: Partial<Access> | null = null;
  try {
    const res = await fetch(`${GATEWAY_URL}/auth/me`, {
      headers: headersActingAs(identity.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (res.ok) access = (await res.json()) as Partial<Access>;
  } catch {
    // Fall through to the refusal below: an unreachable gateway is not
    // permission to buy.
    access = null;
  }

  const capabilities = Array.isArray(access?.capabilities)
    ? access.capabilities
    : [];
  if (!hasCapability({ capabilities } as Access, PURCHASE_CAPABILITY)) {
    return NextResponse.json(
      {
        detail:
          "You do not have permission to make purchases for this organization.",
      },
      { status: 403 },
    );
  }

  // Everything below runs only for a HOLDER, which is why the configuration
  // 503 can appear here at all: a member without the capability has already
  // been refused and cannot learn whether this deployment is wired to a
  // Console.
  const config = consoleConfig();
  if (!config) return notConfigured();

  const callerOrg =
    typeof access?.organization?.slug === "string"
      ? access.organization.slug
      : "";
  if (!callerOrg) {
    // `admin/me.py:135-140` degrades to `organization: {}` when the tenant
    // lookup fails. Right for rendering the app; wrong for spending.
    return NextResponse.json(
      {
        detail:
          "We could not confirm which organization you belong to, so this purchase was refused.",
      },
      { status: 403 },
    );
  }

  const keyOrg = await keyOrganization(config);
  if (!keyOrg) return consoleUnavailable();

  if (keyOrg !== callerOrg) {
    return NextResponse.json(
      {
        detail:
          `Purchases made here are billed to a different organization than yours (${callerOrg}), ` +
          "so this purchase was refused. Ask an owner to check this deployment's billing key.",
      },
      { status: 403 },
    );
  }

  return { email: identity.email, organization: callerOrg };
}

/**
 * Statuses whose upstream body is relayed to the browser VERBATIM.
 *
 * ⚠️ **This is the opposite of what the read proxy does, deliberately.**
 * `summary/route.ts:68-75` drops the upstream body because `GET /billing/summary`
 * is written for an operator and can name internal state. The checkout routes
 * are not: they are reached on the customer's own organization key, and their
 * refusals ARE the product — SC-4g done-when 4's partition (three distinct
 * reasons for `expired` / `revoked` / `exhausted`, one byte-identical shape for
 * {unknown, wrong-org}) only reaches the customer if this hop passes it on. A
 * surface that re-derived those reasons would be a second opinion about a
 * partition the server was built to hold.
 *
 * Everything else is generalised, and 503 is why the list is a list rather than
 * "any 4xx": `POST /billing/orders` answers 503 **naming the missing Razorpay
 * variables** when the provider is unconfigured, which is this deployment's
 * configuration and not the customer's business.
 */
export const RELAYED_STATUSES: ReadonlySet<number> = new Set([400, 404, 409]);

/**
 * Hand the Console's answer to the browser under the relay policy above.
 *
 * JSON in both directions: every route here is JSON-only (unlike the CSV
 * proxies, whose bytes matter — see `src/lib/export.test.ts`).
 *
 * ⚠️ **An upstream 401 is never relayed as a 401** *(added 2026-08-19)*. The
 * Console issues 401 for a missing, malformed, unknown or revoked **API key**
 * (`customer_console/auth.py:188-220`) — this deployment's own credential, held
 * by this server and not by the person at the keyboard. Passed through, it
 * reaches `redeemRefusal`'s `unauthenticated` arm and tells a signed-in
 * purchaser to *sign in* for a fault they cannot see and cannot fix, while the
 * real cause (a rotated key) goes unreported. It becomes a **502**, which is
 * exactly the conclusion the sibling read proxy reached first and carries the
 * argument for: `summary/route.ts:73` maps upstream 401||403 the same way. One
 * policy, mirrored rather than extracted because that route is merged code with
 * a known-open board finding on it; `checkout.test.ts` asserts the two still
 * agree, so the mirror cannot go stale silently.
 */
export async function relayConsole(res: Response): Promise<NextResponse> {
  const body = await res.json().catch(() => null);
  if (res.ok) return NextResponse.json(body ?? {}, { status: res.status });
  if (RELAYED_STATUSES.has(res.status) && body !== null) {
    return NextResponse.json(body, { status: res.status });
  }
  if (res.status === 401) {
    return NextResponse.json(
      { detail: "Billing is temporarily unavailable." },
      { status: 502 },
    );
  }
  return NextResponse.json(
    { detail: `Could not reach billing (${res.status})` },
    { status: res.status },
  );
}

/** A Console outage reads as "billing is unavailable", never as a crash. */
export function consoleUnavailable(): NextResponse {
  return NextResponse.json(
    { detail: "Billing is temporarily unavailable." },
    { status: 503 },
  );
}

/**
 * The headers every Console call carries.
 *
 * `X-CC-Member` is **attribution only** — it refines within the organization
 * the key already pinned and can never move the call to another one. ⚠️ The
 * Console does not read it on the write path today (`create_order` audits with
 * `actor="organization"`), so the audit trail records which organization
 * bought and not which person: SC-4a finding F-H, a WS-31 column ask, recorded
 * rather than worked around here. The surface already holds the identity and
 * already sends it.
 */
export function consoleHeaders(key: string, email: string): HeadersInit {
  return {
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
    "X-CC-Member": email,
  };
}
