/**
 * The shared half of the `/api/accounts/*` routes (MT-1k slice A2). Server only.
 *
 * Every route loads the same state: the flag, the active session cookie and
 * its account, and the slots. Every WRITE route also passes `sameOrigin`
 * (rule 2). `SameSite=Lax` already keeps the cookies off a cross-site POST.
 * The `Sec-Fetch-Site` check is the second lock on the same door.
 */
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_SECRET, isAuthEnabled } from "@/auth";
import {
  SLOT_COUNT,
  activeSessionCookie,
  decodeAccount,
  readSlots,
  slotCookieName,
  type Account,
  type ActiveCookie,
  type Slot,
} from "@/lib/accountSlots";

/** Read at request time, so a flip needs a restart and no rebuild. */
export function switcherEnabled(): boolean {
  return isAuthEnabled && process.env.ACCOUNT_SWITCHER_ENABLED === "true";
}

/**
 * True when the request comes from a page of this ORIGIN.
 *
 * `Sec-Fetch-Site` is set by the browser, and a page cannot set or forge it.
 * Every browser this app supports sends it on a fetch.
 *
 * ⚠️ `same-origin` only, never `same-site`. `operator.metorite.com` is the
 * same site as `app.metorite.com`, and it has no business moving a member's
 * session. A request without the header is refused.
 *
 * ⚠️ Not a `Host` comparison. D51 allows no request-host reader in this tier
 * at all (`subdomain.test.ts`), whatever it is for.
 */
export function sameOrigin(req: NextRequest): boolean {
  return req.headers.get("sec-fetch-site") === "same-origin";
}

export interface SwitcherState {
  active: ActiveCookie;
  account: Account;
  slots: (Slot | null)[];
}

/** The state, or the response that refuses the request. */
export async function loadState(
  req: NextRequest,
  { write }: { write: boolean },
): Promise<SwitcherState | NextResponse> {
  if (!switcherEnabled()) {
    return NextResponse.json({ enabled: false }, { status: write ? 404 : 200 });
  }
  if (write && !sameOrigin(req)) {
    return NextResponse.json({ detail: "cross-origin" }, { status: 403 });
  }
  const active = activeSessionCookie(req.cookies);
  const now = Date.now();
  const account = active ? await decodeAccount(active.value, active.name, SESSION_SECRET, now) : null;
  if (!active || !account) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }
  const slots = await readSlots(req.cookies, active, SESSION_SECRET, now);
  return { active, account, slots };
}

function options(secure: boolean, expSeconds: number) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    expires: new Date(expSeconds * 1000),
  };
}

export function setSlot(res: NextResponse, s: SwitcherState, index: number, raw: string, exp: number) {
  res.cookies.set(slotCookieName(index, s.active.secure), raw, options(s.active.secure, exp));
}

export function clearSlot(res: NextResponse, s: SwitcherState, index: number) {
  res.cookies.set(slotCookieName(index, s.active.secure), "", {
    ...options(s.active.secure, 0),
    maxAge: 0,
  });
}

/** Make `raw` the active session. Same name and attributes Auth.js uses. */
export function setSession(res: NextResponse, s: SwitcherState, raw: string, exp: number) {
  res.cookies.set(s.active.name, raw, options(s.active.secure, exp));
}

/** A slot index from a JSON body, or null. */
export async function slotFromBody(req: NextRequest): Promise<number | null> {
  return slotOf(await jsonBody(req));
}

/** The JSON body of a request, or `{}` when it has none or it is not JSON. */
export async function jsonBody(req: NextRequest): Promise<Record<string, unknown>> {
  try {
    const body = (await req.json()) as unknown;
    return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** The slot index of a parsed body, or null. */
export function slotOf(body: Record<string, unknown>): number | null {
  const n = body.slot;
  return typeof n === "number" && Number.isInteger(n) && n >= 0 && n < SLOT_COUNT ? n : null;
}

/**
 * The push endpoint that a `remove` names (WS-66 PN-1), or null. Only an
 * `https:` URL of at most 2048 characters, the gateway's own limit. The
 * gateway checks it again, and it deletes the slot account's row only.
 */
export function pushEndpointOf(body: Record<string, unknown>): string | null {
  const e = body.push_endpoint;
  if (typeof e !== "string" || e.length > 2048 || !e.startsWith("https://")) return null;
  return e;
}
