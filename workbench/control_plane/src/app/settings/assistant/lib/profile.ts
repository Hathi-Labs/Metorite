/**
 * The Assistant pane's logic (WS-63 PZ-7, `personalization.md` §4.3, §7).
 *
 * Types, caps, the fetch helpers and the merge rules. No React here, so every
 * rule has a pure test. The routes are the proxies under
 * `/api/auth/me/assistant-profile`.
 *
 * ⚠️ `address` and `style` are written WHOLE: the gateway replaces the
 * sub-object. So a save builds the value from the current server state plus
 * the one edit, drops a cleared key (never "" or null), and takes the
 * answer, the full profile, as the new state.
 */

export const FACT_KINDS = ["about", "routine", "preference", "work", "relationship"] as const;
export type FactKind = (typeof FACT_KINDS)[number];
export const FORMALITIES = ["casual", "neutral", "formal"] as const;
export type Formality = (typeof FORMALITIES)[number];
export const GREETINGS = ["none", "brief", "warm"] as const;
export type Greeting = (typeof GREETINGS)[number];
export const LENGTHS = ["short", "default", "detailed"] as const;
export type Length = (typeof LENGTHS)[number];

/** The gateway's caps (`assistant_profile.py`). */
export const CAPS = {
  name: 40,
  language: 16,
  notes: 5,
  note: 200,
  fact: 300,
  facts: 40,
  alias: 60,
  aliases: 50,
  email: 254,
} as const;

export interface Address {
  name?: string;
  formality?: Formality;
  greeting?: Greeting;
}
export interface Style {
  length?: Length;
  language?: string;
  notes?: string[];
}
export interface Fact {
  id: string;
  text: string;
  kind: FactKind;
  source?: string;
  at?: string;
}
export interface Alias {
  id: string;
  alias: string;
  email: string;
}
export interface Profile {
  v: number;
  learning: boolean;
  address: Address;
  style: Style;
  aliases: Alias[];
  facts: Fact[];
}

export const EMPTY_PROFILE: Profile = {
  v: 1,
  learning: true,
  address: {},
  style: {},
  aliases: [],
  facts: [],
};

export const KIND_LABELS: Record<FactKind, string> = {
  about: "About me",
  routine: "Routine",
  preference: "Preference",
  work: "Work",
  relationship: "Relationship",
};

/** A failed call. `status` 0 means the request never reached the proxy. */
export class ProfileError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** The text of a gateway error body. It is a string, or an array of pydantic errors. */
export function errorText(detail: unknown, status = 0): string {
  if (typeof detail === "string" && detail.trim()) return friendly(detail, status);
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
        ? String((d as { msg: string }).msg).replace(/^Value error, /, "")
        : ""))
      .filter(Boolean);
    if (msgs.length) return msgs.join(" ");
  }
  return friendly("", status);
}

function friendly(text: string, status: number): string {
  if (status === 404) return "That item is gone. Reload the page to see the current list.";
  if (status === 401) return "You are signed out. Sign in again.";
  if (status === 502 || status === 503) return "The assistant profile is not available now. Try again soon.";
  if (text && status !== 502 && status !== 503) return text;
  return "The change did not save. Try again.";
}

async function call<T>(path: string, method: string, body?: unknown): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(`/api/auth/me/assistant-profile${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ProfileError(errorText("", 502), 0);
  }
  if (res.status === 204) return null;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
    throw new ProfileError(errorText(detail, res.status), res.status);
  }
  return data as T;
}

/** Fill the gaps in a stored profile, so a view never meets undefined. */
export function normalize(raw: Partial<Profile> | null | undefined): Profile {
  return {
    v: raw?.v ?? 1,
    learning: raw?.learning !== false,
    address: raw?.address ?? {},
    style: raw?.style ?? {},
    aliases: raw?.aliases ?? [],
    facts: raw?.facts ?? [],
  };
}

export async function fetchProfile(): Promise<Profile> {
  return normalize(await call<Partial<Profile>>("", "GET"));
}
export async function saveLearning(value: boolean): Promise<Profile> {
  return normalize(await call<Partial<Profile>>("", "PUT", { key: "learning", value }));
}
export async function saveAddress(value: Address): Promise<Profile> {
  return normalize(await call<Partial<Profile>>("", "PUT", { key: "address", value }));
}
export async function saveStyle(value: Style): Promise<Profile> {
  return normalize(await call<Partial<Profile>>("", "PUT", { key: "style", value }));
}
export async function forgetAll(): Promise<Profile> {
  await call("", "DELETE");
  return EMPTY_PROFILE;
}
export const addFact = (text: string, kind: FactKind) =>
  call<Fact>("/facts", "POST", { text, kind });
export const patchFact = (id: string, patch: { text: string } | { kind: FactKind }) =>
  call<Fact>(`/facts/${encodeURIComponent(id)}`, "PATCH", patch);
export const deleteFact = (id: string) => call(`/facts/${encodeURIComponent(id)}`, "DELETE");
export const addAlias = (alias: string, email: string) =>
  call<Alias>("/aliases", "POST", { alias, email });
export const patchAlias = (id: string, patch: { alias: string } | { email: string }) =>
  call<Alias>(`/aliases/${encodeURIComponent(id)}`, "PATCH", patch);
export const deleteAlias = (id: string) => call(`/aliases/${encodeURIComponent(id)}`, "DELETE");

/** Drop every key whose value is undefined, "" or an empty list. */
function compact<T extends object>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = typeof v === "string" ? v.trim() : v;
  }
  return out as T;
}

/** The new `address` value: the current one, the edit on top, cleared keys gone. */
export function mergeAddress(current: Address, edit: { [K in keyof Address]?: Address[K] | null | "" }): Address {
  return compact({ ...current, ...edit } as Address);
}

/** The new `style` value, built the same way. */
export function mergeStyle(current: Style, edit: { [K in keyof Style]?: Style[K] | null | "" }): Style {
  return compact({ ...current, ...edit } as Style);
}

/** True when a counter should warn: at or past 80% of its cap. */
export function nearCap(used: number, cap: number): boolean {
  return used >= Math.ceil(cap * 0.8);
}

/** The date part of a stored timestamp, or "" when it is absent or odd. */
export function shortDate(at: string | undefined): string {
  if (!at) return "";
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(at);
  return m ? m[1] : "";
}

/** The words for a fact's source. Aliases carry no source (§4.2). */
export function sourceLabel(source: string | undefined): string {
  if (!source) return "";
  if (source === "settings") return "Added in Settings";
  if (source === "chat") return "Learned in chat";
  if (source === "whatsapp") return "Learned on WhatsApp";
  return `Source: ${source}`;
}
