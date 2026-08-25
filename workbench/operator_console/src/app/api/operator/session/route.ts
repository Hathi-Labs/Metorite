import { cookies } from "next/headers";
import {
  isStaff,
  STAFF_COOKIE,
  StaffUnconfigured,
} from "@/lib/staff";
import { json, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/session — the INTERIM staff sign-in. Validates the shared
// secret server-side and, on success, sets an httpOnly cookie. The secret is
// checked here and NEVER echoed back. Replaced wholesale by the staff Entra
// directory when the owner stands it up (D35.3) — this is the placeholder that
// makes the app testable dark.
export async function POST(request: Request): Promise<Response> {
  const body = (await readJsonBody(request)) as { secret?: string };
  let ok: boolean;
  try {
    ok = isStaff(body.secret ?? null);
  } catch (e) {
    if (e instanceof StaffUnconfigured) {
      return json(503, { error: "operator console staff gate is not configured" });
    }
    throw e;
  }
  if (!ok) return json(401, { error: "invalid staff secret" });

  const jar = await cookies();
  jar.set(STAFF_COOKIE, (body.secret ?? "").trim(), {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
  });
  return json(200, { ok: true });
}

// DELETE /api/operator/session — sign out.
export async function DELETE(): Promise<Response> {
  const jar = await cookies();
  jar.delete(STAFF_COOKIE);
  return json(200, { ok: true });
}
