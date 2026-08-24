/**
 * WS-30 SC-2c — `POST /api/admin/members/invite` → gateway `POST /admin/members`,
 * then (dark) one notification email.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2c (done-whens 1-6) ·
 * `work_plan.md` §3 **D49** · `user_management_contract.md` **R11**.
 *
 * ## Why a route file at all, when `/api/admin/[...path]` already forwards this
 *
 * Next resolves the more specific segment first, so this file **shadows** the
 * catch-all for exactly this path and the catch-all is untouched. The rejected
 * alternative was an `if (path[0] === "members")` inside the catch-all: a proxy
 * whose job is *"forward /admin/\* unchanged"* stops being reviewable the moment
 * one path behaves differently, and the next special case goes in beside it.
 * Authorization stays the gateway's, exactly as the catch-all's header says.
 *
 * ## What the flag does and does not gate
 *
 * `MEMBER_INVITE_EMAIL_ENABLED` (+ a present `RESEND_API_KEY`) gates **the
 * email only**. The invite itself, and CP-2f's Console membership mirror one
 * tier down, are unconditional: the mirror is the structural half of D49 and
 * gating it on a mail flag would mean the fix for the `console-empty` funnel
 * only worked on boxes that also send mail. With the flag unset this route is
 * byte-identical to the catch-all it shadows, plus two response fields.
 *
 * ## R11 at this hop
 *
 * The outbound body is **rebuilt** from `{email, display_name, roles}`. An
 * `org`, `organization_id` or `actor_email` the browser adds is dropped and
 * never forwarded — the acting identity is the session's, established by
 * `proxyToGateway`'s `gatewayHeaders`, and the tenant is the gateway's answer.
 *
 * ## No credential of its own
 *
 * This route holds **no** Console key and **no** deployment key, and mints no
 * `Authorization` header: the Resend bearer is built inside `lib/emailOtp.ts`'s
 * `resendSender`, which is outside the route sweep, and the gateway bearer is
 * `lib/gateway.ts`'s. `src/lib/gateway.test.ts`'s three-name allow-list must
 * stay unchanged — that is SC-2c done-when 6.
 */
import { NextRequest, NextResponse } from "next/server";

import {
  GATEWAY_URL,
  headersActingAs,
  proxyToGateway,
  requireIdentity,
} from "@/lib/gateway";
import { resendSender } from "@/lib/emailOtp";
import {
  type InviteEmailEnv,
  isInviteEmailConfigured,
  sendInviteEmail,
} from "@/lib/inviteEmail";

export const dynamic = "force-dynamic";

/** The ONLY keys that reach the gateway. Everything else is dropped (R11). */
const FORWARDED_KEYS = ["email", "display_name", "roles"] as const;

function outboundBody(raw: Record<string, unknown>): Record<string, unknown> {
  // Rebuilt, never filtered-in-place: a deny-list needs updating every time the
  // browser learns a new field, and the field it misses is the one that matters.
  const out: Record<string, unknown> = {};
  for (const key of FORWARDED_KEYS) {
    if (raw[key] !== undefined) out[key] = raw[key];
  }
  return out;
}

/**
 * The caller's organization display name, from the GATEWAY — never the body.
 *
 * `/auth/me` resolves the organization from the authenticated identity
 * (`routes/admin/me.py`), so this is the same server-derived fact the Members
 * header renders. Taking it from the request instead would let an authenticated
 * admin put arbitrary text into a message sent from our own verified sender.
 *
 * Returns `""` when it cannot be established — the caller then does not send,
 * rather than inventing a name for the one thing the email is about.
 *
 * ⚠️ Called ONLY inside the lit arm, so a deployment with the flag unset makes
 * exactly one gateway call, as it did before this route existed.
 */
async function organizationName(email: string): Promise<string> {
  try {
    const res = await fetch(`${GATEWAY_URL}/auth/me`, {
      headers: headersActingAs(email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return "";
    const body = (await res.json()) as {
      organization?: { display_name?: string; slug?: string };
    };
    return (
      body.organization?.display_name?.trim() ||
      body.organization?.slug?.trim() ||
      ""
    );
  } catch {
    return "";
  }
}

export async function POST(req: NextRequest): Promise<Response> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  let raw: Record<string, unknown> = {};
  try {
    raw = (await req.json()) as Record<string, unknown>;
  } catch {
    raw = {};
  }

  let res: Response;
  try {
    res = await proxyToGateway("/admin/members", {
      method: "POST",
      body: JSON.stringify(outboundBody(raw)),
    });
  } catch {
    return NextResponse.json({ detail: "Gateway unreachable." }, { status: 502 });
  }

  const text = await res.text();
  if (!res.ok) {
    // A refused invite mails nobody — the send lives inside this arm's negation
    // and there is exactly one of it (done-when 3).
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  let member: unknown = null;
  try {
    member = JSON.parse(text);
  } catch {
    member = null;
  }

  // ── The email channel's OUTCOME, distinguishable (review round 1, F2) ──────
  // "disabled" = this deployment does not send invite mail (flag/key unset) —
  //              the shipped default, NOT a failure, and the UI must not imply
  //              one. "sent" = exactly one message went out. "failed" = the
  //              deployment IS armed and the send did not happen (transport
  //              error, or the org name could not be established) — the one
  //              case the admin should act on ("tell them to sign in").
  const env = process.env as unknown as InviteEmailEnv;
  let emailChannel: "disabled" | "sent" | "failed" = "disabled";
  if (isInviteEmailConfigured(env)) {
    emailChannel = "failed";
    const to = String(raw.email ?? "").trim();
    const orgName = to ? await organizationName(me.email) : "";
    if (to && orgName) {
      try {
        await sendInviteEmail(resendSender(String(env.RESEND_API_KEY)), {
          to,
          orgName,
          env,
        });
        emailChannel = "sent";
      } catch {
        // The membership is already written on both planes. Failing the
        // response would tell the admin the invite did not happen when it did,
        // and a retry here would risk a second message for a write that cannot
        // be repeated. So: report it, and stop.
        emailChannel = "failed";
      }
    }
  }

  return NextResponse.json({
    invited: true,
    // Kept for compatibility with the first-shipped shape; `email_channel` is
    // the field the UI branches on (a bare boolean cannot distinguish
    // dark-by-config from armed-and-failed, which is F2's whole finding).
    email_sent: emailChannel === "sent",
    email_channel: emailChannel,
    member,
  });
}
