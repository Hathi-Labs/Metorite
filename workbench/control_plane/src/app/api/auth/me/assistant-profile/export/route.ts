/**
 * GET /api/auth/me/assistant-profile/export — the member's own profile as a
 * file (WS-63 PZ-9, `personalization.md` §13.3 item 7). Proxies the gateway's
 * `/auth/me/assistant-profile/export`. See `../relay.ts`.
 *
 * `?format=text` asks for the text. Any other value asks for JSON, so no
 * query text of the browser reaches the gateway URL.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export const GET = (req: NextRequest) =>
  relay(req, "GET", req.nextUrl.searchParams.get("format") === "text" ? "/export?format=text" : "/export");
