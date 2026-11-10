/**
 * PATCH and DELETE /api/auth/me/assistant-profile/facts/[id] (WS-63 PZ-7).
 * See `../../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { idSuffix, relay } from "../../relay";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function PATCH(req: NextRequest, { params }: Ctx): Promise<Response> {
  return relay(req, "PATCH", await idSuffix("facts", params));
}

export async function DELETE(req: NextRequest, { params }: Ctx): Promise<Response> {
  return relay(req, "DELETE", await idSuffix("facts", params));
}
