/**
 * GET /api/auth/me/assistant-profile/block — "Read as text" (WS-63 PZ-20a,
 * `personalization.md` §9). Proxies the gateway's
 * `/auth/me/assistant-profile/block`. See `../relay.ts`.
 *
 * The call takes no query and no body. The member is the session's.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export const GET = (req: NextRequest) => relay(req, "GET", "/block");
