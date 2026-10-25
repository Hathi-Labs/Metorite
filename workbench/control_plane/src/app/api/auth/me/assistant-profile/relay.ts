/**
 * The one relay for `/api/auth/me/assistant-profile/**` (WS-63 PZ-7).
 *
 * Each route file names its verb and its gateway path. This helper does the
 * rest: it resolves the session identity, forwards the body as text, and
 * passes the gateway's status through. A 204 stays a 204. A 422 or a 404 keeps
 * its JSON body. A gateway that cannot be reached answers 502 and never an
 * empty profile. The export (PZ-9) is a file, so the relay also passes the
 * gateway's `Content-Disposition` and `Cache-Control` through.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R5, R11), and the gateway forbids an extra field.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

/** A profile is at most 6,000 characters. The cap stops a body the gateway would refuse. */
const MAX_BODY = 16_384;

const PATH = "/auth/me/assistant-profile";

/** The gateway headers that a download needs. Nothing else passes. */
const PASSED_HEADERS = ["Content-Disposition", "Cache-Control"] as const;

export async function relay(
  req: NextRequest,
  method: "GET" | "PUT" | "POST" | "PATCH" | "DELETE",
  suffix: string | null = "",
): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  // An id that `idSuffix` refused. Answer as the gateway does for an absent id.
  if (suffix === null) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  const hasBody = method === "PUT" || method === "POST" || method === "PATCH";
  let body: string | undefined;
  if (hasBody) {
    body = await req.text();
    if (body.length > MAX_BODY) {
      return NextResponse.json({ detail: "body_too_large" }, { status: 413 });
    }
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}${PATH}${suffix}`, {
      method,
      headers: await gatewayHeaders(hasBody ? { "Content-Type": "application/json" } : {}),
      body: hasBody ? body || "null" : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (res.status === 204) return new Response(null, { status: 204 });
    const text = await res.text();
    const headers: Record<string, string> = {
      "Content-Type": res.headers.get("content-type") ?? "application/json",
    };
    for (const name of PASSED_HEADERS) {
      const value = res.headers.get(name);
      if (value) headers[name] = value;
    }
    return new Response(text, { status: res.status, headers });
  } catch {
    return NextResponse.json({ detail: "assistant_profile_unavailable" }, { status: 502 });
  }
}

/**
 * The ids the gateway makes: a letter and hex (`f…`, `a…`). Anything else is
 * refused before it reaches a URL. `encodeURIComponent("..")` is `..`, and
 * `fetch` then folds `/facts/..` into the profile root, so a fact DELETE
 * would become "forget everything" (PZ-7 verify, P2).
 */
const ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The id segment for the gateway path, or `null` for an id of the wrong shape. */
export async function idSuffix(
  kind: "facts" | "aliases",
  params: Promise<{ id: string }>,
): Promise<string | null> {
  const { id } = await params;
  return ID.test(id) ? `/${kind}/${id}` : null;
}
