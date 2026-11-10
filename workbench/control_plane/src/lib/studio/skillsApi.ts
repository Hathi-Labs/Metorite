/**
 * The client of the skill routes (STU-F2b). One thin layer over
 * `/api/studio/*`, the BFF proxy of the gateway `/studio` routes of F2a.
 *
 * It sends no identity and no org. The proxy and the gateway take those from
 * the session. Every call answers a value and never throws, so a page reads
 * `status` and `detail` and never a stack.
 */

export interface Skill {
  id: string;
  name: string;
  slug: string;
  description: string;
  visibility: string;
  state: string;
  current_version: number | null;
  created_at: string;
  updated_at: string;
}

export interface SkillBody {
  description: string;
  instructions: string;
  actions?: string[];
}

export interface SkillVersion {
  skill_id: string;
  version: number;
  body: SkillBody;
  content_hash?: string;
  created_at: string;
}

export interface CatalogAction {
  id: string;
  title: string;
  source: string;
  class: string;
  [key: string]: unknown;
}

export type Reply<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; detail: string };

/** The detail of an error body, as one string. */
function detailOf(body: unknown, status: number): string {
  const b = body as { detail?: unknown; error?: unknown } | null;
  const raw = b?.detail ?? b?.error;
  if (typeof raw === "string" && raw) return raw;
  if (raw !== undefined && raw !== null) return JSON.stringify(raw);
  return `The request failed (${status}).`;
}

/**
 * Each function below sends its own `fetch` with a literal path and a literal
 * verb, so the chat parity fence can read every request (`chat_parity.py`).
 */
async function send<T>(request: () => Promise<Response>): Promise<Reply<T>> {
  let res: Response;
  try {
    res = await request();
  } catch {
    return { ok: false, status: 0, detail: "The server did not answer. Try again." };
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) return { ok: false, status: res.status, detail: detailOf(body, res.status) };
  return { ok: true, status: res.status, data: body as T };
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function listSkills(): Promise<Reply<{ skills: Skill[] }>> {
  return send(() => fetch("/api/studio/skills", { cache: "no-store" }));
}

export function getSkill(id: string): Promise<Reply<{ skill: Skill; version: SkillVersion | null }>> {
  const key = encodeURIComponent(id);
  return send(() => fetch(`/api/studio/skills/${key}`, { cache: "no-store" }));
}

export function createSkill(input: {
  name: string;
  description: string;
  body: SkillBody;
}): Promise<Reply<{ skill: Skill; version: SkillVersion | null }>> {
  return send(() =>
    fetch("/api/studio/skills", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(input),
      cache: "no-store",
    }),
  );
}

export function saveSkillVersion(
  id: string,
  body: SkillBody,
): Promise<Reply<{ skill: Skill; version: SkillVersion }>> {
  const key = encodeURIComponent(id);
  return send(() =>
    fetch(`/api/studio/skills/${key}/versions`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ body }),
      cache: "no-store",
    }),
  );
}

/** Publish and Resume send `published`. Pause sends `paused`. */
export function moveSkill(
  id: string,
  state: "published" | "paused",
): Promise<Reply<{ skill: Skill }>> {
  const key = encodeURIComponent(id);
  return send(() =>
    fetch(`/api/studio/skills/${key}/state`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ state }),
      cache: "no-store",
    }),
  );
}

export function listCatalogActions(): Promise<Reply<{ actions: CatalogAction[] }>> {
  return send(() => fetch("/api/studio/actions", { cache: "no-store" }));
}

// ── The gallery reads the list as a state ──────────────────────────────────

/**
 * What the Skills section says when the org is not on `STUDIO_ORGS` (D179).
 * It says what happened and what to do next. It never shows a raw error.
 */
export const SKILLS_OFF_COPY = {
  message: "Skills are not open for your organization yet.",
  hint: "Metorite turns them on for your organization soon. Your apps and workflows work as before.",
} as const;

export type SkillsView =
  /** The org is outside STUDIO_ORGS (404), or nobody is signed in (401), or the member lacks access (403). */
  | { kind: "off" }
  | { kind: "error"; detail: string }
  | { kind: "ok"; skills: Skill[] };

/** What the list shows when the call failed for a reason other than "off". */
export const LIST_FAILED = "The skills did not load. Try again.";

/**
 * A 404 is the normal answer on every org until `STUDIO_ORGS` lists it, so it
 * is "off": the present empty state, no button and no error. So are a 401 and
 * a 403. A miss of the network (status 0) and a 5xx are errors the member can
 * retry.
 */
export function viewOfList(reply: Reply<{ skills: Skill[] }>): SkillsView {
  if (reply.ok) return { kind: "ok", skills: reply.data.skills ?? [] };
  if (reply.status === 404 || reply.status === 401 || reply.status === 403) {
    return { kind: "off" };
  }
  return { kind: "error", detail: LIST_FAILED };
}

export async function loadSkillsView(): Promise<SkillsView> {
  return viewOfList(await listSkills());
}
