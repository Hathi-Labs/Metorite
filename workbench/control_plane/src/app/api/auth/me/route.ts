/**
 * GET /api/auth/me
 *
 * The caller's identity and effective access, proxied from the gateway's
 * /auth/me. Every signed-in member calls this; it is what the sidebar filters
 * and the route guard checks against.
 *
 * A gateway that is down returns NO_ACCESS rather than 500, so the shell still
 * renders. That is a UX choice, not a security one — nothing here grants
 * anything, and every real request is authorized again at the gateway.
 *
 * A caller with NO session gets the same NO_ACCESS, and this is the one route
 * where that matters more than the 401 the others return. It previously
 * forwarded the bearer alone, so the gateway answered as the service principal
 * and an anonymous request received `email: "system:internal"` with `*` — the
 * shell then rendered every nav pane to a signed-out visitor. Answering
 * "nobody, and nothing" is both truthful and what the shell wants: this route
 * is asked *whether* to render a signed-in UI, so it must be able to say no.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, currentIdentity, headersActingAs, gatewayFetch } from "@/lib/gateway";
import { NO_ACCESS } from "@/lib/access";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const me = await currentIdentity();
  if (!me) return NextResponse.json(NO_ACCESS, { status: 200 });
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me`, {
      headers: headersActingAs(me.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return NextResponse.json(NO_ACCESS, { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json(NO_ACCESS, { status: 200 });
  }
}
