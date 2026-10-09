/**
 * POST /api/agent/respond-input
 *
 * Answers a native Copilot SDK `ask_user` prompt for a running agent.
 *
 * The agent's run is BLOCKED inside the SDK's `on_user_input_request`
 * handler (the gateway parked it on a Future after emitting a
 * `user_input_requested` SSE event).  Posting the answer here unblocks the
 * run so it continues in the SAME stream — the answer is never queued as a
 * separate chat message.
 *
 * WS-51 S2: the answer belongs to the account that drew the card. The chat
 * sends that account's address as `as`. When the browser is now signed in
 * to ANOTHER account (a switch in another tab changes the cookie under this
 * one), the route refuses with 409 `answer_in_other_account` and calls no
 * gateway, so the answer never reaches the other account's org. `as` can only
 * refuse, never grant: the gateway still checks `can_send` in the room, under
 * the session's own tenant. Fence: `src/lib/respondInput.test.ts`.
 */

import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { otherAccountRefusal } from "@/lib/respondInput";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface RespondInputRequest {
  request_id: string;
  answer: string;
  was_freeform?: boolean;
  /** Thread the parked run belongs to — lets the gateway relay the answer to
   *  whichever worker owns the run (P1-2 cross-worker control bus). */
  thread_id?: string;
  /** The account that drew the card (WS-51 S2). Refuses only, never grants. */
  as?: string;
}

export async function POST(req: NextRequest) {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const session = await auth();
  if (!session) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  let body: RespondInputRequest;
  try {
    body = (await req.json()) as RespondInputRequest;
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!body.request_id || typeof body.answer !== "string") {
    return new Response(
      JSON.stringify({ error: "request_id and answer are required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const refused = otherAccountRefusal(body.as, me.email);
  if (refused) return refused;

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/agent/respond-input`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        request_id: body.request_id,
        answer: body.answer,
        was_freeform: body.was_freeform ?? true,
        thread_id: body.thread_id ?? null,
      }),
    });

    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: `Gateway unreachable: ${String(err)}` }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}
