/**
 * BFF: present a discount code — `POST /billing/orders/{id}/redeem` (SC-4g).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 8 and 9 ·
 * `customer_console.md` §6 CP-9 §9.3(2).
 *
 * The second of B7's two write proxies, and the one that can move value: a
 * verified operator-issued code bringing `total_paise` to 0 fulfils through the
 * same `payments.fulfil()` the capture path calls. What makes that safe is that
 * **the code is the pre-authorization** — a customer cannot mint one — and what
 * makes it gated here is that presenting one is still spending: it consumes a
 * redemption of a code the operator issued, against this organization's order.
 *
 * ⚠️ **The code is a bearer secret and this hop is the last place it exists in
 * our tree.** It is forwarded and never logged, never echoed into a response
 * and never persisted; only its PREFIX is stored anywhere, and only by the
 * Console (SC-4g (i), `keys.py`). Nothing here may grow a log line that takes
 * the body.
 *
 * ⚠️ **The refusal bodies are relayed verbatim** (`RELAYED_STATUSES` in
 * `../../../_console`). SC-4g done-when 4's partition — three distinct reasons
 * for `expired` / `revoked` / `exhausted`, one byte-identical shape for
 * {unknown, wrong-org} — is a property of the server's answers, and a proxy
 * that summarised them would either lose the three or leak the two apart.
 */
import { NextResponse, type NextRequest } from "next/server";

import {
  consoleConfig,
  consoleHeaders,
  consoleUnavailable,
  notConfigured,
  relayConsole,
  requirePurchaser,
} from "../../../_console";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const config = consoleConfig();
  if (!config) return notConfigured();

  const { id } = await params;
  const payload = (await req.json().catch(() => null)) as {
    code?: string;
  } | null;
  const code = (payload?.code ?? "").trim();
  if (!code) {
    return NextResponse.json({ detail: "Enter a code." }, { status: 400 });
  }

  try {
    const res = await fetch(
      `${config.url}/billing/orders/${encodeURIComponent(id)}/redeem`,
      {
        method: "POST",
        headers: consoleHeaders(config.key, who.email),
        // Rebuilt, not forwarded: `RedeemRequest` forbids extra fields.
        body: JSON.stringify({ code }),
        cache: "no-store",
      },
    );
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
