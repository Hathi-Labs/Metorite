import { cookies } from "next/headers";
import { isStaff, STAFF_COOKIE, StaffUnconfigured } from "@/lib/staff";
import { SESSION_COOKIE, usesSessions } from "@/lib/identity";
import { json, readJsonBody } from "@/lib/route";
import {
  ConsoleUnconfigured,
  exchangeSession,
  readOperatorSession,
  revokeSession,
} from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// ⚠️ **The one route in this app that is deliberately ungated.** It is the
// door, so it cannot require what it issues. Its own body is the proof:
// either the shared passphrase (interim) or a Supabase access token the
// Console verifies with the issuer (CP-12f2).
//
// `identity.ts` chooses which. Both are here at once because the console has
// been live on the passphrase since 2026-08-22, and removing it before the
// owner has finished H-54 would lock the team out of a running console.

//: Applied to BOTH cookies. `httpOnly` keeps them out of `document.cookie`,
//: `secure` keeps them off plain HTTP, and `sameSite: lax` stops another site
//: driving a cross-org console with the operator's own cookie.
const COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax",
  secure: true,
  path: "/",
} as const;

type Body = { secret?: string; access_token?: string };

// GET → who am I, per the Console (method, actor, role). Always for the
// CALLER: the Console reads the operator from the session it was handed.
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => readOperatorSession(d));
}

export async function POST(request: Request): Promise<Response> {
  const body = (await readJsonBody(request)) as Body;
  const jar = await cookies();

  if (usesSessions()) {
    const accessToken = (body.access_token ?? "").trim();
    if (!accessToken) {
      return json(400, { error: "access_token is required" });
    }

    let result;
    try {
      result = await exchangeSession(accessToken);
    } catch (e) {
      if (e instanceof ConsoleUnconfigured) {
        return json(503, { error: "customer console is not configured" });
      }
      throw e;
    }

    // ⚠️ Relay the Console's refusal VERBATIM. It answers 401 for a token it
    // does not trust, 403 for somebody who is not an operator, and 503 for an
    // unconfigured box. Flattening those to one status would hide the only
    // signal that tells "sign in again" apart from "you are not staff".
    if (result.status !== 200) {
      return new Response(result.body, {
        status: result.status,
        headers: { "content-type": "application/json" },
      });
    }

    const issued = JSON.parse(result.body) as {
      token: string;
      expires_at: string | null;
      operator: { email: string; role: string };
    };

    // The token is written to the cookie and NOT returned to the browser. It
    // is a bearer credential for a cross-customer console, so the less of it
    // that reaches client JavaScript the better.
    jar.set(SESSION_COOKIE, issued.token, {
      ...COOKIE_OPTIONS,
      ...(issued.expires_at
        ? { expires: new Date(issued.expires_at) }
        : {}),
    });
    // A stale interim cookie beside a live session is a second way in. Drop it.
    jar.delete(STAFF_COOKIE);

    return json(200, { ok: true, operator: issued.operator });
  }

  // ── The interim path (F1, F2, F5 — all of them still true here) ──────────
  let ok: boolean;
  try {
    ok = isStaff(body.secret ?? null);
  } catch (e) {
    if (e instanceof StaffUnconfigured) {
      return json(503, {
        error: "operator console staff gate is not configured",
      });
    }
    throw e;
  }
  if (!ok) return json(401, { error: "invalid staff secret" });

  jar.set(STAFF_COOKIE, (body.secret ?? "").trim(), COOKIE_OPTIONS);
  return json(200, { ok: true });
}

// Sign out.
//
// ⚠️ On the session path this REVOKES the row before it clears the cookie,
// which is what makes sign-out mean something. The interim path can only ask
// the browser to forget a passphrase that stays valid for everybody — that is
// **F5**, and it is the difference the whole slice is for.
export async function DELETE(): Promise<Response> {
  const jar = await cookies();

  if (usesSessions()) {
    const token = jar.get(SESSION_COOKIE)?.value;
    if (token) {
      try {
        await revokeSession({ authToken: token });
      } catch (e) {
        if (!(e instanceof ConsoleUnconfigured)) throw e;
        // An unconfigured Console cannot revoke. Clearing the cookie is still
        // right, and the session expires on its own.
      }
    }
  }

  jar.delete(SESSION_COOKIE);
  jar.delete(STAFF_COOKIE);
  return json(200, { ok: true });
}
