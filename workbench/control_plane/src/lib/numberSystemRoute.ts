/**
 * The two helpers of the number-system routes
 * (`app/api/settings/number-system/route.ts` and `./me/route.ts`). A route
 * file may export only its HTTP handlers, so they live here.
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10.
 */

import { NextRequest, NextResponse } from "next/server";

import { NoIdentityError, proxyToGateway, unauthenticated } from "@/lib/gateway";

/** The body of a PUT, or the answer to send back when it is wrong. */
export async function valueBody(req: NextRequest, allowNull: boolean): Promise<string | NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Body must be JSON" }, { status: 400 });
  }
  const value = (body ?? {}) as { value?: unknown };
  const ok = value.value === "indian" || value.value === "international" || (allowNull && value.value === null);
  if (!ok) {
    return NextResponse.json(
      { detail: `value must be "indian" or "international"${allowNull ? " or null" : ""}` },
      { status: 400 },
    );
  }
  return JSON.stringify({ value: value.value });
}

/** Forward to the gateway, and say so plainly when it cannot answer. */
export async function forward(path: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await proxyToGateway(path, { ...init, signal: AbortSignal.timeout(8_000) });
  } catch (err) {
    if (err instanceof NoIdentityError) return unauthenticated();
    return NextResponse.json({ detail: "The number format is not available now" }, { status: 503 });
  }
}
