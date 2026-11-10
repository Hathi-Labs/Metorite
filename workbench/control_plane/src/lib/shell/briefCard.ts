/**
 * The data side of the brief card (`core_brief.md` §5 items 1 and 3, BR-2c-2).
 *
 * `GET /api/brief/today?kind=brief` answers the stored run of the member.
 * A 404 means the brief is off, not ready, or the member opted out. The card
 * then draws nothing, so `fetchBrief` answers `null` for it. Any other
 * failure throws, and the card draws nothing and logs once.
 *
 * Pure, so node-env vitest can hold it. Fence: `briefCard.test.ts`.
 */

export const BRIEF_KEY = "/api/brief/today?kind=brief";

export interface BriefClaim {
  position: number;
  section: string;
  text: string;
  source_app: string;
  source_kind: string;
  href: string;
}

export interface BriefToday {
  kind: string;
  local_date: string;
  state: string;
  lead: string;
  lead_source: string;
  ready_at: string | null;
  claims: BriefClaim[];
}

/** Read the body defensively. Anything unusable gives `null`. */
export function readBrief(body: unknown): BriefToday | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (typeof b.lead !== "string" || b.lead === "" || typeof b.local_date !== "string") return null;
  const claims = Array.isArray(b.claims) ? b.claims : [];
  const read: BriefClaim[] = [];
  for (const c of claims) {
    if (!c || typeof c !== "object") continue;
    const r = c as Record<string, unknown>;
    if (typeof r.text !== "string" || r.text === "") continue;
    read.push({
      position: typeof r.position === "number" ? r.position : read.length,
      section: typeof r.section === "string" ? r.section : "",
      text: r.text,
      source_app: typeof r.source_app === "string" ? r.source_app : "",
      source_kind: typeof r.source_kind === "string" ? r.source_kind : "",
      href: typeof r.href === "string" ? r.href : "",
    });
  }
  read.sort((a, b2) => a.position - b2.position);
  return {
    kind: typeof b.kind === "string" ? b.kind : "brief",
    local_date: b.local_date,
    state: typeof b.state === "string" ? b.state : "",
    lead: b.lead,
    lead_source: typeof b.lead_source === "string" ? b.lead_source : "",
    ready_at: typeof b.ready_at === "string" ? b.ready_at : null,
    claims: read,
  };
}

/** The brief, `null` on a 404 (no card), and a throw on any other failure. */
export async function fetchBrief(fetchImpl: typeof fetch = fetch): Promise<BriefToday | null> {
  const res = await fetchImpl(BRIEF_KEY, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`The brief could not load (${res.status}).`);
  return readBrief(await res.json());
}

/** The member's calendar date as `YYYY-MM-DD`, from the page's clock. */
export function localDateOf(now: Date): string {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `2026-10-22` as "22 Oct". Anything else comes back as it came. */
export function shortDate(localDate: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDate);
  if (!m) return localDate;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${Number(m[3])} ${month}` : localDate;
}

/**
 * The label of a brief that is not today's, such as "Yesterday's brief, 22 Oct".
 * `null` when the brief is today's, or the page has no clock yet.
 */
export function staleLabel(localDate: string, now: Date | null): string | null {
  if (!now || localDate === localDateOf(now)) return null;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const word = localDate === localDateOf(yesterday) ? "Yesterday's brief" : "Earlier brief";
  return `${word}, ${shortDate(localDate)}`;
}
