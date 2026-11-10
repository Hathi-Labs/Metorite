/**
 * GET /api/settings/number-system — the org's number system, mine, and the
 *     one I get (`resolved`)
 * PUT /api/settings/number-system — set the org's value (admin only, the
 *     gateway decides)
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10. The
 * gateway resolves and authorizes. This route checks the value's shape and
 * forwards. The member's own override is `./me/route.ts`.
 */

import { NextRequest, NextResponse } from "next/server";

import { requireIdentity } from "@/lib/gateway";
import { forward, valueBody } from "@/lib/numberSystemRoute";

export const dynamic = "force-dynamic";

const GATEWAY_PATH = "/settings/number-system";

export async function GET(): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  return forward(GATEWAY_PATH);
}

export async function PUT(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await valueBody(req, false);
  if (body instanceof NextResponse) return body;
  return forward(GATEWAY_PATH, { method: "PUT", body });
}
