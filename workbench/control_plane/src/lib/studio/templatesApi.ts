/**
 * The client of the ready apps (STU-K1b). One thin layer over
 * `/api/studio/templates` and `/api/studio/packages/from-template`, the
 * BFF proxy of the gateway routes of K1a.
 *
 * It sends no identity and no org. Every call answers a value and never
 * throws. The section hides when the list answers 404 (Studio is off for the
 * org) and when the list is empty.
 */
import { previewHref } from "@/lib/studio/packagePreview";

export interface AppTemplate {
  id: string;
  name: string;
  purpose: string;
}

export type TemplatesView =
  | { kind: "off" }
  | { kind: "ok"; templates: AppTemplate[] };

export type UseResult =
  | { ok: true; href: string }
  | { ok: false; message: string };

export const USE_FAILED_LINE = "The app did not start. Try again.";

function oneSentence(body: unknown, fallback: string): string {
  const b = body as { detail?: unknown; error?: unknown; message?: unknown } | null;
  const d = b?.detail;
  const raw =
    (d && typeof d === "object" ? (d as { message?: unknown }).message : d) ?? b?.message ?? b?.error;
  return typeof raw === "string" && raw ? raw : fallback;
}

/** The list. Any answer but a 200 hides the section, with no error. */
export async function loadTemplatesView(): Promise<TemplatesView> {
  try {
    const res = await fetch("/api/studio/templates", { cache: "no-store" });
    if (!res.ok) return { kind: "off" };
    const body = (await res.json().catch(() => null)) as { templates?: AppTemplate[] } | null;
    const templates = Array.isArray(body?.templates) ? body.templates : [];
    return templates.length > 0 ? { kind: "ok", templates } : { kind: "off" };
  } catch {
    return { kind: "off" };
  }
}

/** Create, publish and install a ready app. A 409 opens the app that the member has. */
export async function startFromTemplate(template: string): Promise<UseResult> {
  let res: Response;
  try {
    res = await fetch("/api/studio/packages/from-template", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template }),
      cache: "no-store",
    });
  } catch {
    return { ok: false, message: "The server did not answer. Try again." };
  }
  const body = (await res.json().catch(() => null)) as
    | { package?: { id?: string }; detail?: { code?: string; package_id?: string } }
    | null;
  if (res.ok && body?.package?.id) return { ok: true, href: previewHref(body.package.id) };
  if (res.status === 409 && body?.detail?.code === "package_exists" && body.detail.package_id) {
    return { ok: true, href: previewHref(body.detail.package_id) };
  }
  return { ok: false, message: oneSentence(body, USE_FAILED_LINE) };
}
