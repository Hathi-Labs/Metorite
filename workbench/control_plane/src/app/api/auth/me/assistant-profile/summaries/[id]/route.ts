/**
 * DELETE /api/auth/me/assistant-profile/summaries/[id] (WS-63 PZ-35). Deletes
 * the summary of one chat. A chat of another member is a 404 at the gateway.
 * See `../../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { idSuffix, relay } from "../../relay";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function DELETE(req: NextRequest, { params }: Ctx): Promise<Response> {
  return relay(req, "DELETE", await idSuffix("summaries", params));
}
