/**
 * /api/agent/transcribe — the chat's voice input (projects_ai_chat.md §31, VOX-1).
 *
 * GET reads whether the composer shows the mic. POST sends one spoken clip,
 * as the multipart field `file`, and answers `{ text }`. Both go to the
 * gateway's `/chat/transcribe` as the signed-in member. It mirrors
 * `api/agent/workspace/[sessionId]/upload/route.ts`: `requireIdentity`,
 * `gatewayHeaders` and `gatewayFetch` with `FormData`, and no Content-Type,
 * so fetch sets the boundary.
 *
 * The size is checked here before the proxy, so a clip over the cap costs
 * the gateway nothing. The gateway checks it again, with the kind, the
 * length, the flag and the routing switch. The words of each refusal come
 * from the gateway (`detail.message`), and this route passes them on.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { MAX_CLIP_BYTES, NO_ANSWER, NO_CLIP, TOO_LONG } from "@/lib/voiceDictation";

export const dynamic = "force-dynamic";

/** The multipart framing around the clip, as the gateway allows it. */
const FRAMING_BYTES = 64 * 1024;

const UPSTREAM = `${GATEWAY_URL}/chat/transcribe`;

function refusal(status: number, code: string, message: string): NextResponse {
  return NextResponse.json({ detail: { code, message } }, { status });
}

/** The gateway's answer, passed on. A body that is not JSON keeps its status. */
async function relay(res: Response): Promise<NextResponse> {
  const body = await res.json().catch(() => null);
  if (body === null) return refusal(res.ok ? 502 : res.status, "no_answer", NO_ANSWER);
  return NextResponse.json(body, { status: res.status });
}

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(UPSTREAM, {
      method: "GET",
      headers: await gatewayHeaders(),
      cache: "no-store",
    });
    return await relay(res);
  } catch {
    return refusal(502, "no_answer", NO_ANSWER);
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  // No Content-Length: `formData()` would buffer a body of any size.
  const length = req.headers.get("content-length");
  if (length === null || !/^\d+$/.test(length)) {
    return refusal(411, "length_required", NO_CLIP);
  }
  const declared = Number(length);
  if (declared > MAX_CLIP_BYTES + FRAMING_BYTES) return refusal(413, "too_long", TOO_LONG);

  let clip: File;
  try {
    const form = await req.formData();
    const part = form.get("file");
    if (!(part instanceof File) || part.size === 0) return refusal(400, "no_clip", NO_CLIP);
    clip = part;
  } catch {
    return refusal(400, "no_clip", NO_CLIP);
  }
  if (clip.size > MAX_CLIP_BYTES) return refusal(413, "too_long", TOO_LONG);

  try {
    const body = new FormData();
    body.append("file", clip, clip.name || "clip.wav");
    // Don't set Content-Type — fetch will set it with boundary for multipart
    const res = await gatewayFetch(UPSTREAM, {
      method: "POST",
      headers: await gatewayHeaders(),
      body,
    });
    return await relay(res);
  } catch {
    return refusal(502, "no_answer", NO_ANSWER);
  }
}
