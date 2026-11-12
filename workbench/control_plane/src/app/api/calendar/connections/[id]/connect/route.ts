/**
 * POST /api/calendar/connections/[id]/connect -> gateway
 * `POST /tasks/calendar/connections/{google|outlook}/connect` (WS-CAL CE-10c).
 *
 * The segment is named `id` because Next allows one slug name at a level, and `[id]/sync` shares
 * it. Here the value is a PROVIDER word, and only `google` or `outlook` passes.
 *
 * The gateway answers `{ url }`, the consent URL. This route passes it on only when it is an
 * absolute `https` URL with no user and no password, so the route cannot become an open redirect.
 * A refusal keeps its status (a 404 means the flag is off). The route refuses a request from
 * another site (`sameOrigin`). It never logs the answer, which holds the signed state.
 */
import { NextRequest, NextResponse } from "next/server";

import { isProvider } from "@/app/calendar/lib/calendarIds";
import { sameOrigin } from "@/lib/accountsServer";
import { proxyToGateway } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };
const MAX_URL_CHARS = 4096;

function safeConsentUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > MAX_URL_CHARS) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password) return null;
  return url.toString();
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
  let res: Response;
  try {
    res = await proxyToGateway(`/tasks/calendar/connections/${id}/connect`, { method: "POST" });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }
  if (!res.ok) return res;
  const body: unknown = await res.json().catch(() => null);
  const url = safeConsentUrl((body as { url?: unknown } | null)?.url);
  if (url === null) return NextResponse.json({ detail: "failed" }, { status: 502, headers: NO_STORE });
  return NextResponse.json({ url }, { headers: NO_STORE });
}
