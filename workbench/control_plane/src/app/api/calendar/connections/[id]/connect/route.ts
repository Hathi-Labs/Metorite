/**
 * POST /api/calendar/connections/[id]/connect -> gateway
 * `POST /tasks/calendar/connections/{google|outlook}/connect` (WS-CAL CE-10c).
 *
 * The segment is named `id` because Next allows one slug name at a level, and `[id]/sync` shares
 * it. Here the value is a PROVIDER word, and only `google` or `outlook` passes.
 *
 * The gateway answers `{ url }`, the consent URL. This route passes it on only when it is an
 * `https` URL on the exact consent host of the provider (`safeConsentUrl`), so the route cannot
 * become an open redirect.
 * The body is optional (CE-10d). The only key it may hold is `login_hint`, the address of one of
 * the member's own mailboxes. The gateway checks that. This route checks only the shape.
 * A refusal keeps its status (a 404 means the flag is off). The route refuses a request from
 * another site (`sameOrigin`). It never logs the answer, which holds the signed state.
 */
import { NextRequest, NextResponse } from "next/server";

import { isPlausibleAddress, isProvider, safeConsentUrl } from "@/app/calendar/lib/calendarIds";
import { sameOrigin } from "@/lib/accountsServer";
import { proxyToGateway } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

/** The optional body. `undefined` means none. `null` means refused. */
async function readHint(req: NextRequest): Promise<{ login_hint: string } | undefined | null> {
  const text = await req.text().catch(() => null);
  if (text === null) return null;
  if (text.trim() === "") return undefined;
  if (text.length > 1024) return null;
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return null;
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const keys = Object.keys(body);
  const hint = (body as Record<string, unknown>).login_hint;
  if (keys.length !== 1 || keys[0] !== "login_hint" || !isPlausibleAddress(hint)) return null;
  return { login_hint: hint };
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const refused = refuseUnsafePath([id]);
  if (refused) return refused;
  if (!isProvider(id)) return NextResponse.json({ detail: "Unknown provider." }, { status: 400 });
  if (!sameOrigin(req)) return NextResponse.json({ detail: "cross_origin" }, { status: 403 });
  const hint = await readHint(req);
  if (hint === null) return NextResponse.json({ detail: "Bad request body." }, { status: 400 });
  let res: Response;
  try {
    res = await proxyToGateway(
      `/tasks/calendar/connections/${id}/connect`,
      hint ? { method: "POST", body: JSON.stringify(hint) } : { method: "POST" },
    );
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
  if (!res.ok) return res;
  const body: unknown = await res.json().catch(() => null);
  const url = safeConsentUrl(id, (body as { url?: unknown } | null)?.url);
  if (url === null) return NextResponse.json({ detail: "failed" }, { status: 502, headers: NO_STORE });
  return NextResponse.json({ url }, { headers: NO_STORE });
}
