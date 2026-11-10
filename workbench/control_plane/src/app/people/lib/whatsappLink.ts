/**
 * Chat on WhatsApp — what the My Profile section shows (WS-47 WAC-1).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §5.2.
 *
 * The section lives on My Profile and adds no nav entry. It reads
 * `GET /me/whatsapp-link` and draws only when that answer says `enabled`.
 * Every refusal (the channel is dark, the org is not on the list, no
 * `feature:chat`, no display number) draws NOTHING, because a member who
 * cannot use the channel has no use for a sentence about it.
 *
 * The decisions are pure and live here, because this package has no DOM
 * renderer: vitest runs in node, so a rule inside a component has no fence.
 * `WhatsAppLinkSection.tsx` only draws what these functions return.
 *
 * The QR code renders in the browser, from the same `wa.me` link, with the
 * `qrcode` package. The server sends no image. A QR code must be dark on
 * light for a camera to read it, so it is a PNG data URL drawn in an `<img>`,
 * like a photo, and not a themed SVG. No raw-HTML sink is involved.
 */

import { toDataURL } from "qrcode";

export type LinkStatus = "pending" | "active" | "revoked";

export interface WhatsAppLink {
  /** The row id, and the React key: one org can hold two active links. */
  id: string;
  organization_id: string;
  organization_name: string | null;
  status: LinkStatus;
  linked_at: string | null;
  is_current: boolean;
  expires_at: string | null;
  phone_hint: string | null;
}

export interface LinkState {
  enabled: boolean;
  display_number: string | null;
  code_ttl_minutes: number;
  links: WhatsAppLink[];
}

export interface IssuedCode {
  code: string;
  expires_at: string;
  link: string;
  display_number: string;
  code_ttl_minutes: number;
}

/** What the section draws. `hidden` draws nothing at all. */
export type SectionView =
  | { kind: "hidden" }
  | { kind: "ready"; links: LinkRow[]; ttlMinutes: number };

export interface LinkRow {
  key: string;
  label: string;
  tone: "success" | "warning" | "neutral";
  detail: string;
}

/**
 * The section's view from the GET. Anything but a 200 that says `enabled`
 * hides the section: 404 (dark), 403 (no `feature:chat`), 401, 5xx, a
 * network failure (`null`) and `enabled: false` (no display number).
 */
export function sectionView(
  res: { status: number; body: unknown } | null
): SectionView {
  if (!res || res.status !== 200) return { kind: "hidden" };
  const body = res.body as Partial<LinkState> | null;
  if (!body || body.enabled !== true) return { kind: "hidden" };
  const links = Array.isArray(body.links) ? body.links : [];
  return {
    kind: "ready",
    links: links.filter((l) => l.status !== "revoked").map(linkRow),
    ttlMinutes: body.code_ttl_minutes ?? 15,
  };
}

function linkRow(link: WhatsAppLink): LinkRow {
  const org = link.organization_name || "this organization";
  if (link.status === "active") {
    const phone = link.phone_hint ? `phone ending ${link.phone_hint}` : "your phone";
    return {
      key: link.id,
      label: link.is_current ? "Linked · current" : "Linked",
      tone: "success",
      detail: `${phone}, for ${org}`,
    };
  }
  return {
    key: link.id,
    label: "Waiting for your message",
    tone: "warning",
    detail: link.expires_at
      ? `The last link works until ${clockTime(link.expires_at)}.`
      : "The last link is still open.",
  };
}

/** A short local time, for "works until 14:05". */
export function clockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * True when *issued* is a well-formed answer of the POST. The page shows the
 * link and the QR code only for one. A `wa.me` link to anywhere else is
 * refused, so a wrong answer can never put a foreign link on the page.
 */
export function isIssuedCode(issued: unknown): issued is IssuedCode {
  const i = issued as Partial<IssuedCode> | null;
  return (
    !!i &&
    typeof i.code === "string" &&
    typeof i.expires_at === "string" &&
    typeof i.link === "string" &&
    i.link.startsWith("https://wa.me/")
  );
}

/** The PNG data URL of the QR code of *link*. Rendered in the browser. */
export function qrDataUrl(link: string): Promise<string> {
  return toDataURL(link, {
    errorCorrectionLevel: "M",
    margin: 2,
    width: 192,
  });
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/** `GET /api/me/whatsapp-link`. `null` when the request itself failed. */
export async function fetchLinkState(): Promise<{
  status: number;
  body: unknown;
} | null> {
  try {
    const res = await fetch("/api/me/whatsapp-link", { cache: "no-store" });
    return { status: res.status, body: await readJson(res) };
  } catch {
    return null;
  }
}

/** `POST /api/me/whatsapp-link/code`. Throws a plain sentence on a refusal. */
export async function issueCode(): Promise<IssuedCode> {
  const res = await fetch("/api/me/whatsapp-link/code", { method: "POST" });
  const body = await readJson(res);
  if (!res.ok || !isIssuedCode(body)) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(
      typeof detail === "string" ? detail : "Could not make a link. Try again."
    );
  }
  return body;
}
