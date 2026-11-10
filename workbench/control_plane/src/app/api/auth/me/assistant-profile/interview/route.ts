/**
 * POST and DELETE /api/auth/me/assistant-profile/interview (WS-63 PZ-27).
 * POST with no body is the "No thanks" to the offer: it sets `started_at` only.
 * DELETE resets the whole interview, and the facts stay. See `../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  return relay(req, "POST", "/interview");
}

export async function DELETE(req: NextRequest): Promise<Response> {
  return relay(req, "DELETE", "/interview");
}
