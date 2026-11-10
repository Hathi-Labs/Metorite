/**
 * Messages Metorite starts — what the My Profile section shows (WS-59 RE-1a).
 *
 * Spec: `project-docs/specs/proactive_reach.md` §16.1.
 *
 * The section reads `GET /me/reach/preview`. It draws only for a 200, so a
 * member for whom Reach is dark sees nothing. The preview is what Reach
 * would send now. "Send my brief now" posts `POST /me/reach/brief`, which
 * sends the member's own brief to their own WhatsApp.
 *
 * The decisions are pure and live here, where vitest can reach them.
 * `ReachSection.tsx` only draws what these functions return.
 */

export interface ReachPreview {
  brief: string | null;
  interrupts: string | null;
  window_open: boolean;
  linked: boolean;
  /** RE-2a: the window is closed, so the interrupts wait for the member's next message. */
  held?: boolean;
}

export type ReachView =
  | { kind: "hidden" }
  | {
      kind: "ready";
      brief: string | null;
      interrupts: string | null;
      /** Why the member cannot send now, or null when they can. */
      blocked: string | null;
      /** True when the interrupts wait for the member's next message (RE-2a). */
      held: boolean;
    };

export function reachView(
  res: { status: number; body: unknown } | null
): ReachView {
  if (!res || res.status !== 200) return { kind: "hidden" };
  const body = res.body as Partial<ReachPreview> | null;
  if (!body || typeof body !== "object") return { kind: "hidden" };
  const text = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
  let blocked: string | null = null;
  if (body.linked !== true) {
    blocked = "Link your phone above first.";
  } else if (body.window_open !== true) {
    blocked =
      "Send any message to Metorite on WhatsApp first. WhatsApp lets Metorite write first only within 24 hours of your last message.";
  }
  return {
    kind: "ready",
    brief: text(body.brief),
    interrupts: text(body.interrupts),
    blocked,
    held: body.held === true,
  };
}

/**
 * The words of the result of a send. The gateway answers `{state, reason}`
 * (`gateway/reach/routes.py` `result_body`), and this reads those two keys.
 */
export function sendResult(res: { status: number; body: unknown }): {
  ok: boolean;
  message: string;
} {
  const body = (res.body ?? {}) as { state?: string; reason?: string; detail?: string };
  if (res.status === 200 && body.state === "sent") {
    return { ok: true, message: "Sent. Check WhatsApp." };
  }
  if (res.status === 200 && body.reason === "nothing_to_say") {
    return { ok: true, message: "Nothing needs you right now, so nothing was sent." };
  }
  if (res.status === 200 && body.reason === "already_sent") {
    return { ok: true, message: "Already sent this minute. Check WhatsApp." };
  }
  if (res.status === 200 && body.reason === "window_closed") {
    return {
      ok: false,
      message: "Send any message to Metorite on WhatsApp first, then try again.",
    };
  }
  const why = body.reason ?? body.detail;
  return {
    ok: false,
    message: why ? `Not sent: ${String(why).replace(/_/g, " ")}.` : "Not sent. Try again.",
  };
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchReachPreview(): Promise<{ status: number; body: unknown } | null> {
  try {
    const res = await fetch("/api/me/reach/preview", { cache: "no-store" });
    return { status: res.status, body: await readJson(res) };
  } catch {
    return null;
  }
}

export async function sendBriefNow(): Promise<{ status: number; body: unknown }> {
  try {
    const res = await fetch("/api/me/reach/brief", { method: "POST" });
    return { status: res.status, body: await readJson(res) };
  } catch {
    return { status: 0, body: null };
  }
}
