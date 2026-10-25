/**
 * POST /api/accounts/{stash|switch|remove|signout-all} (MT-1k A2).
 *
 * - `stash {push_endpoint?}` keeps the active session in a slot, before "Add
 *   another account" sends the member to /signin. The new sign-in then
 *   replaces the active cookie, and the old account waits in its slot. With
 *   every slot full it overwrites the oldest one, and with a `push_endpoint`
 *   it first deletes that account's web push row of this browser, as that
 *   account (WS-66 PN-1, `forgetSlotDevice`).
 * - `switch {slot}` swaps the active session and the slot.
 * - `remove {slot, push_endpoint?}` forgets one other account. With a
 *   `push_endpoint`, it first deletes that account's web push row of this
 *   browser, as that account (WS-66 PN-1, `forgetSlotDevice`).
 * - `signout-all` empties every slot, and names every account it held, so the
 *   page can clear their chat namespaces before it signs the active one out.
 *
 * All four need the flag, a same-origin request and a good active session
 * (`loadState`). None mints a token: each moves a token Auth.js issued.
 */
import { NextResponse, type NextRequest } from "next/server";
import {
  clearSlot,
  jsonBody,
  loadState,
  pushEndpointOf,
  setSession,
  setSlot,
  slotFromBody,
  slotOf,
} from "@/lib/accountsServer";
import { SLOT_COUNT, stashIndex } from "@/lib/accountSlots";
import { GATEWAY_URL, currentIdentity, gatewayFetch, headersActingAs } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/**
 * Delete the web push row of `endpoint` for the account in a slot, AS that
 * account (WS-66 PN-1). Two acts take a slot account off this browser:
 * "Remove from this browser" (`remove`) and a `stash` that overwrites the
 * oldest of the full slots.
 *
 * ⚠️ The second place this tier acts as an email that is not the active
 * session's. `GET /api/accounts` is the first, and its note gives the proof:
 * the slot holds a valid Auth.js session for that email, which `loadState`
 * verified with the session secret. The act is one DELETE of that member's
 * own row, so it can touch no other member and no other org.
 *
 * It never blocks the remove or the stash: the active session must still
 * resolve, the call has 3 seconds, and a refusal or a fault changes nothing.
 */
async function forgetSlotDevice(email: string, endpoint: string): Promise<void> {
  if (!(await currentIdentity())) return;
  try {
    await gatewayFetch(`${GATEWAY_URL}/push/subscriptions`, {
      method: "DELETE",
      headers: headersActingAs(email, { "Content-Type": "application/json" }),
      body: JSON.stringify({ endpoint }),
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    /* the remove goes on */
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ action: string }> },
): Promise<NextResponse> {
  const { action } = await params;
  const s = await loadState(req, { write: true });
  if (s instanceof NextResponse) return s;

  if (action === "stash") {
    const index = stashIndex(s.slots, s.account.email);
    const overwritten = s.slots[index];
    const endpoint = pushEndpointOf(await jsonBody(req));
    // WS-66 PN-1: with every slot full, the stash overwrites the oldest one,
    // and that account leaves this browser. Its push row of this browser goes
    // first, as that account. A slot of the ACTIVE email is the active
    // member's own, and an empty slot holds nobody: both are skipped.
    if (overwritten && endpoint && !same(overwritten.account.email, s.account.email)) {
      await forgetSlotDevice(overwritten.account.email, endpoint);
    }
    const res = NextResponse.json({ ok: true });
    setSlot(res, s, index, s.active.value, s.account.exp);
    return res;
  }

  if (action === "switch") {
    const index = await slotFromBody(req);
    const target = index === null ? null : s.slots[index];
    if (index === null || !target) {
      return NextResponse.json({ detail: "That account is no longer signed in here" }, { status: 409 });
    }
    const res = NextResponse.json({ ok: true, email: target.account.email });
    setSession(res, s, target.raw, target.account.exp);
    setSlot(res, s, index, s.active.value, s.account.exp);
    // Any other slot that holds either email is a leftover. Clear it, so one
    // account never sits in two slots.
    s.slots.forEach((o, i) => {
      if (!o || i === index) return;
      if (same(o.account.email, s.account.email) || same(o.account.email, target.account.email)) {
        clearSlot(res, s, i);
      }
    });
    return res;
  }

  if (action === "remove") {
    const body = await jsonBody(req);
    const index = slotOf(body);
    if (index === null) return NextResponse.json({ detail: "bad slot" }, { status: 400 });
    const target = s.slots[index];
    const endpoint = pushEndpointOf(body);
    // WS-66 PN-1: the removed account's push row of THIS browser goes first,
    // as that account. After the remove nothing can act for it any more. A
    // slot that is a leftover copy of the ACTIVE account is skipped: its row
    // is the active member's own, and the active member stays.
    if (target && endpoint && !same(target.account.email, s.account.email)) {
      await forgetSlotDevice(target.account.email, endpoint);
    }
    const res = NextResponse.json({ ok: true });
    clearSlot(res, s, index);
    return res;
  }

  if (action === "signout-all") {
    const emails = [s.account.email, ...s.slots.flatMap((o) => (o ? [o.account.email] : []))];
    const res = NextResponse.json({ ok: true, emails });
    for (let i = 0; i < SLOT_COUNT; i++) clearSlot(res, s, i);
    return res;
  }

  return NextResponse.json({ detail: "unknown action" }, { status: 404 });
}
