/**
 * POST /api/accounts/{stash|switch|remove|signout-all} (MT-1k A2).
 *
 * - `stash` keeps the active session in a slot, before "Add another account"
 *   sends the member to /signin. The new sign-in then replaces the active
 *   cookie, and the old account waits in its slot.
 * - `switch {slot}` swaps the active session and the slot.
 * - `remove {slot}` forgets one other account.
 * - `signout-all` empties every slot, and names every account it held, so the
 *   page can clear their chat namespaces before it signs the active one out.
 *
 * All four need the flag, a same-origin request and a good active session
 * (`loadState`). None mints a token: each moves a token Auth.js issued.
 */
import { NextResponse, type NextRequest } from "next/server";
import { clearSlot, loadState, setSession, setSlot, slotFromBody } from "@/lib/accountsServer";
import { SLOT_COUNT, stashIndex } from "@/lib/accountSlots";

export const dynamic = "force-dynamic";

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ action: string }> },
): Promise<NextResponse> {
  const { action } = await params;
  const s = await loadState(req, { write: true });
  if (s instanceof NextResponse) return s;

  if (action === "stash") {
    const res = NextResponse.json({ ok: true });
    setSlot(res, s, stashIndex(s.slots, s.account.email), s.active.value, s.account.exp);
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
    const index = await slotFromBody(req);
    if (index === null) return NextResponse.json({ detail: "bad slot" }, { status: 400 });
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
