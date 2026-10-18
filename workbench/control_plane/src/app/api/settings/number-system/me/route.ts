/**
 * PUT /api/settings/number-system/me — set my own number system, or clear
 * it with `null` ("Use the organization's setting").
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10. Any
 * signed-in member may set their own. The gateway takes the member from the
 * session, never from this body.
 */

import { NextRequest, NextResponse } from "next/server";

import { requireIdentity } from "@/lib/gateway";
import { forward, valueBody } from "@/lib/numberSystemRoute";

export const dynamic = "force-dynamic";

export async function PUT(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await valueBody(req, true);
  if (body instanceof NextResponse) return body;
  return forward("/settings/number-system/me", { method: "PUT", body });
}
