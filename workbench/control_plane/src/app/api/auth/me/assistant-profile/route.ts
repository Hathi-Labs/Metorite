/**
 * GET, PUT and DELETE /api/auth/me/assistant-profile — the member's assistant
 * profile (WS-63 PZ-7, `personalization.md` §4.3). Proxies the gateway's
 * `/auth/me/assistant-profile`. See `relay.ts`.
 */
import type { NextRequest } from "next/server";
import { relay } from "./relay";

export const dynamic = "force-dynamic";

export const GET = (req: NextRequest) => relay(req, "GET");
export const PUT = (req: NextRequest) => relay(req, "PUT");
export const DELETE = (req: NextRequest) => relay(req, "DELETE");
