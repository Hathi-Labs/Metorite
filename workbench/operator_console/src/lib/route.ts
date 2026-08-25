// SERVER-ONLY BFF helpers shared by the operator console's `/api/operator/*`
// route handlers. Two jobs: gate every route on the interim staff secret, and
// relay the Console's own response verbatim so the operator token never leaves
// the server and a refusal stays a refusal.

import { cookies } from "next/headers";
import {
  requireStaff,
  STAFF_COOKIE,
  StaffForbidden,
  StaffUnconfigured,
} from "./staff";
import { ConsoleUnconfigured, type ConsoleResult } from "./console";

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Returns a refusal Response when the caller is not staff, or null to proceed.
// Fails closed: an unconfigured gate 503s, a bad/missing secret 401s.
export async function gateStaff(): Promise<Response | null> {
  const jar = await cookies();
  try {
    requireStaff(jar.get(STAFF_COOKIE)?.value);
    return null;
  } catch (e) {
    if (e instanceof StaffUnconfigured) {
      return json(503, { error: "operator console staff gate is not configured" });
    }
    if (e instanceof StaffForbidden) {
      return json(401, { error: "platform-staff authentication required" });
    }
    throw e;
  }
}

// Relay the Console's status + JSON body to the browser unchanged. The operator
// token is on the OUTGOING request only and is never present in `result.body`.
export function relay(result: ConsoleResult): Response {
  return new Response(result.body, {
    status: result.status,
    headers: { "content-type": "application/json" },
  });
}

// Run a Console call behind the staff gate and relay it; an unconfigured Console
// 503s rather than issuing an unauthenticated cross-org call.
export async function proxyToConsole(
  call: () => Promise<ConsoleResult>,
): Promise<Response> {
  const refusal = await gateStaff();
  if (refusal) return refusal;
  try {
    return relay(await call());
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
