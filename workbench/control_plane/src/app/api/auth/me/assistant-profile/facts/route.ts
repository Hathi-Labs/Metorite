/**
 * POST /api/auth/me/assistant-profile/facts — add one entry (WS-63 PZ-7).
 * See `../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export const POST = (req: NextRequest) => relay(req, "POST", "/facts");
