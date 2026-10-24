/**
 * GET /api/auth/me/assistant-profile/address — the name the assistant calls
 * the member (WS-63 PZ-4, `personalization.md` §5.2). Proxies the gateway's
 * `/auth/me/assistant-profile/address`. My Day greets with it. See `../relay.ts`.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export const GET = (req: NextRequest) => relay(req, "GET", "/address");
