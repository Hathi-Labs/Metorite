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

import { filenameFromDisposition } from "@/lib/export";

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
// ── The interview (PZ-26, PZ-27, `personalization.md` §14.3 and §14.4) ────

/**
 * The areas of the interview, in the order it asks. `AREAS` in
 * `packages/acb_common/acb_common/assistant_profile.py` is the one table.
 * Fence: `interviewAreas.test.ts` reads that file and compares.
 */
export const INTERVIEW_AREAS = [
  { id: "role", label: "Your role", question: "What is your role, and what takes most of your week?" },
  { id: "goals", label: "Your goals", question: "What are the one to three things you most want to move this quarter?" },
  { id: "good_day", label: "A good day", question: "What would make today a win for you?" },
  { id: "decisions", label: "How you decide", question: "Do you want options with a recommendation, or only the answer?" },
  { id: "detail", label: "How much detail", question: "Do you want the headline first, or the full reasoning?" },
  { id: "rhythm", label: "Your rhythm", question: "When do you do deep work, and when must I not interrupt?" },
  { id: "channels", label: "Where to reach you", question: "What must reach you on WhatsApp, and what can wait for the web?" },
  { id: "energy", label: "What you enjoy", question: "Which part of your work do you enjoy most?" },
  { id: "peeves", label: "What annoys you", question: "Is there something that assistants do that annoys you?" },
  { id: "people", label: "People you work with", question: "Who do you work with most?" },
] as const;
export type InterviewAreaId = (typeof INTERVIEW_AREAS)[number]["id"];

/** The state of an area. An area with no entry is open. */
export const INTERVIEW_STATES = ["asked", "answered", "skipped", "never"] as const;
export type InterviewState = (typeof INTERVIEW_STATES)[number];

export interface Interview {
  started_at?: string;
  last_asked_at?: string;
  sitting_at?: string;
  areas: Partial<Record<InterviewAreaId, { state: InterviewState; at?: string }>>;
}

/** The label of an area id, or "" for an id this build does not know. */
export function areaLabel(id: string | undefined): string {
  return INTERVIEW_AREAS.find((a) => a.id === id)?.label ?? "";
}

const AREA_IDS: readonly string[] = INTERVIEW_AREAS.map((a) => a.id);
const STATE_IDS: readonly string[] = INTERVIEW_STATES;

/** Keep the known areas and states. An unknown one drops alone. */
function normalizeInterview(raw: unknown): Interview | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  const out: Interview = { areas: {} };
  for (const key of ["started_at", "last_asked_at", "sitting_at"] as const) {
    if (typeof r[key] === "string") out[key] = r[key];
  }
  const stored = r.areas && typeof r.areas === "object" ? (r.areas as Record<string, unknown>) : {};
  for (const [id, entry] of Object.entries(stored)) {
    if (!AREA_IDS.includes(id) || !entry || typeof entry !== "object") continue;
    const e = entry as { state?: unknown; at?: unknown };
    if (typeof e.state !== "string" || !STATE_IDS.includes(e.state)) continue;
    out.areas[id as InterviewAreaId] = {
      state: e.state as InterviewState,
      ...(typeof e.at === "string" ? { at: e.at } : {}),
    };
  }
  return out;
}

export interface Fact {
  id: string;
  text: string;
  kind: FactKind;
  source?: string;
  at?: string;
  /** The interview area that this fact answers (PZ-26, §14.3). */
  area?: InterviewAreaId;
}
export interface Alias {
  id: string;
  alias: string;
  email: string;
}
export interface Profile {
  v: number;
  learning: boolean;
  /** The interview state (PZ-26). Absent until the member starts or declines. */
  interview?: Interview;
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
  const interview = normalizeInterview(raw?.interview);
  return {
    v: raw?.v ?? 1,
    learning: raw?.learning !== false,
    ...(interview ? { interview } : {}),
    address: raw?.address ?? {},
    style: raw?.style ?? {},
    aliases: raw?.aliases ?? [],
    facts: (raw?.facts ?? []).map(normalizeFact),
  };
}

/** A fact keeps its `area` only when this build knows the area. */
function normalizeFact(f: Fact): Fact {
  if (f.area === undefined || AREA_IDS.includes(f.area)) return f;
  const { area: _unknown, ...rest } = f;
  return rest;
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
  return EMPTY_PROFILE; // no `interview` key: the gateway clears it with the rest
}
/** "No thanks": the gateway sets `started_at` and marks no area. */
export const startInterview = () => call<Interview>("/interview", "POST");
/** Reset one area. The facts stay. */
export const resetArea = (id: InterviewAreaId) => call(`/interview/${encodeURIComponent(id)}`, "DELETE");
/** Reset the whole interview, so the offer can come again. The facts stay. */
export const resetInterview = () => call("/interview", "DELETE");
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

/**
 * The next value of a draft field when the server value or the reset epoch may
 * have changed. Null keeps the draft.
 *
 * A new epoch (Forget everything) always takes the server value. Otherwise a
 * draft that still equals the server value it started from follows the new
 * one, and a draft the member changed is theirs and stays.
 */
export function reconcileDraft(
  draft: string,
  seen: { server: string; epoch: number },
  server: string,
  epoch: number,
): string | null {
  if (epoch !== seen.epoch) return server;
  if (server === seen.server) return null;
  return draft === seen.server ? server : null;
}

/** The languages the picker offers, as BCP 47 tags the gateway accepts. */
export const LANGUAGES: Record<string, string> = {
  en: "English",
  hi: "Hindi",
  kn: "Kannada",
  ta: "Tamil",
  te: "Telugu",
  mr: "Marathi",
  bn: "Bengali",
  gu: "Gujarati",
  ml: "Malayalam",
};

/**
 * The name the assistant uses when the profile holds none (D-PZ-5): the
 * member's preferred name, then the first word of the display name. Null when
 * neither is known.
 */
export function fallbackAddress(
  preferred: string | null | undefined,
  display: string | null | undefined,
): { name: string; from: "preferred" | "display" } | null {
  const p = (preferred ?? "").trim();
  if (p) return { name: p, from: "preferred" };
  const first = (display ?? "").trim().split(/\s+/)[0];
  return first ? { name: first, from: "display" } : null;
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

// ── The export (PZ-9) ─────────────────────────────────────────────────────

export const EXPORT_FORMATS = ["json", "text"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

/** The file name of each export, when the server names none. */
export const EXPORT_FALLBACK: Record<ExportFormat, string> = {
  json: "assistant-profile.json",
  text: "assistant-profile.txt",
};

/** The proxy path of one export. */
export function exportPath(format: ExportFormat): string {
  return `/api/auth/me/assistant-profile/export${format === "text" ? "?format=text" : ""}`;
}

/**
 * Fetch the member's own profile as a file. The bytes stay a `Blob`, and the
 * name is the server's, read off `Content-Disposition` (`src/lib/export.ts`).
 * A failed call throws a `ProfileError`, as every other call here does.
 */
export async function fetchExport(format: ExportFormat): Promise<{ body: Blob; filename: string }> {
  let res: Response;
  try {
    res = await fetch(exportPath(format), { cache: "no-store" });
  } catch {
    throw new ProfileError(errorText("", 502), 0);
  }
  if (!res.ok) {
    const data: unknown = await res.json().catch(() => null);
    const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
    throw new ProfileError(errorText(detail, res.status), res.status);
  }
  return {
    body: await res.blob(),
    filename: filenameFromDisposition(res.headers.get("content-disposition"), EXPORT_FALLBACK[format]),
  };
}

// ── The interview list (PZ-27) ────────────────────────────────────────────

/** The profile with one area back to open. The facts stay. */
export function withoutArea(p: Profile, id: InterviewAreaId): Profile {
  if (!p.interview) return p;
  const { [id]: _gone, ...areas } = p.interview.areas;
  return { ...p, interview: { ...p.interview, areas } };
}

/** The profile with no interview, so the offer can come again. The facts stay. */
export function withoutInterview(p: Profile): Profile {
  const { interview: _gone, ...rest } = p;
  return rest;
}

/** What the list shows for one area: its label and the state words. */
export const STATE_LABELS: Record<InterviewState | "open", string> = {
  open: "Not asked yet",
  asked: "Asked",
  answered: "Answered",
  skipped: "Skipped",
  never: "Stopped asking",
};
