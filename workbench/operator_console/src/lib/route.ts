// SERVER-ONLY BFF helpers shared by the operator console's `/api/operator/*`
// route handlers. Two jobs: gate every route, and relay the Console's own
// response verbatim so a refusal stays a refusal.
//
// ⚠️ **CP-12g put a second path through here, and the difference matters.**
//
// `interim` — the shared passphrase. The BFF then calls the Console with the
//   shared operator token, which CP-12e renamed `breakglass`: it bypasses the
//   role matrix and the elevation window, and logs a WARNING on every use.
//
// `session` — the caller's own `cc_sess_` token is forwarded. The Console
//   applies the §5 matrix to THEM and names them in every audit row.
//
// `identity.ts` owns the switch. This file owns what each path may do.

import { cookies } from "next/headers";
import {
  requireStaff,
  STAFF_COOKIE,
  StaffForbidden,
  StaffUnconfigured,
} from "./staff";
import { SESSION_COOKIE, looksLikeSession, usesSessions } from "./identity";
import { ConsoleUnconfigured, type ConsoleResult, type Deps } from "./console";

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// What the caller presented, once the gate has admitted them.
export type Gate =
  | { ok: true; authToken?: string }
  | { ok: false; refusal: Response };

// ⚠️ **Fails closed on every branch.** An unconfigured gate 503s, a missing or
// wrong credential 401s, and there is no path that reaches the Console with
// nothing.
export async function gate(): Promise<Gate> {
  const jar = await cookies();

  if (usesSessions()) {
    const token = jar.get(SESSION_COOKIE)?.value ?? null;
    if (!looksLikeSession(token)) {
      // Covers "no cookie", "expired and cleared", and "a stale passphrase
      // cookie left over from the interim path". All three mean sign in again.
      return {
        ok: false,
        refusal: json(401, { error: "operator session required" }),
      };
    }
    // ⚠️ The caller's OWN token, never the shared one. See `console.ts`.
    return { ok: true, authToken: (token as string).trim() };
  }

  try {
    requireStaff(jar.get(STAFF_COOKIE)?.value);
    // No `authToken`: the interim path deliberately uses the shared operator
    // token, because there is no per-person credential to use instead. That
    // is F1, and it is why this path is temporary.
    return { ok: true };
  } catch (e) {
    if (e instanceof StaffUnconfigured) {
      return {
        ok: false,
        refusal: json(503, {
          error: "operator console staff gate is not configured",
        }),
      };
    }
    if (e instanceof StaffForbidden) {
      return {
        ok: false,
        refusal: json(401, {
          error: "platform-staff authentication required",
        }),
      };
    }
    throw e;
  }
}

// Kept for callers that only need the yes/no. Returns a refusal Response, or
// null to proceed.
export async function gateStaff(): Promise<Response | null> {
  const result = await gate();
  return result.ok ? null : result.refusal;
}

// Relay the Console's status + JSON body to the browser unchanged. Neither the
// operator token nor the caller's session is ever present in `result.body`.
export function relay(result: ConsoleResult): Response {
  return new Response(result.body, {
    status: result.status,
    headers: { "content-type": "application/json" },
  });
}

// Run a Console call behind the gate and relay it; an unconfigured Console
// 503s rather than issuing an unauthenticated cross-org call.
//
// ⚠️ The callback RECEIVES the deps rather than closing over nothing, so the
// caller's session reaches `callConsole`. A route that ignores the argument
// silently proxies as break-glass, and `route_gate.test.ts` fails any route
// file that does.
export async function proxyToConsole(
  call: (deps: Deps) => Promise<ConsoleResult>,
): Promise<Response> {
  const result = await gate();
  if (!result.ok) return result.refusal;
  try {
    return relay(await call({ authToken: result.authToken }));
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      return json(503, { error: "customer console is not configured" });
    }
    throw e;
  }
}

export async function readJsonBody(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    return {};
  }
}
