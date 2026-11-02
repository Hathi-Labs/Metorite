/**
 * GET and DELETE /api/auth/me/assistant-profile/summaries (WS-63 PZ-35,
 * `personalization.md` §15.5). GET lists the member's chat summaries. DELETE
 * deletes all of them. See `../relay.ts`.
 *
 * The calls take no query and no body. The member is the session's.
 */
import type { NextRequest } from "next/server";
import { relay } from "../relay";

export const dynamic = "force-dynamic";

export const GET = (req: NextRequest) => relay(req, "GET", "/summaries");
export const DELETE = (req: NextRequest) => relay(req, "DELETE", "/summaries");
