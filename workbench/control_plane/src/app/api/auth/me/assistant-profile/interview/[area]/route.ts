/**
 * DELETE /api/auth/me/assistant-profile/interview/[area] (WS-63 PZ-27).
 * It resets one area, and the facts stay. See `../../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { areaSuffix, relay } from "../../relay";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ area: string }> };

export async function DELETE(req: NextRequest, { params }: Ctx): Promise<Response> {
  return relay(req, "DELETE", await areaSuffix(params));
}
