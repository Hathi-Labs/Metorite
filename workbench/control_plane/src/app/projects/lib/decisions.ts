/**
 * The decision block of the task panel (WS-ALN ALN-2b,
 * `project_alignment.md` §3.2).
 *
 * A decision is a question with two to four options for ONE named person. The
 * pure rules live here, and `components/DecisionPanel.tsx` draws them. The
 * answer goes through the ONE answer route (`POST /projects/decisions/{id}/
 * answer`), the same route the chat tool `answer_decision` calls. There is no
 * second write path.
 *
 * Only the decider reads a decision. Another member who can open the task
 * reads `waiting_on`: who it waits on, and until when. Never the question, the
 * options or the answer.
 */

export interface DecisionOption {
  key: string;
  label: string;
}

export type DecisionState = "open" | "answered" | "expired";

export interface DecisionRow {
  id: string;
  subject_id: string;
  decider: string;
  options: DecisionOption[];
  question: string;
  asked_by: string;
  state: DecisionState;
  created_at: string | null;
  expires_at: string | null;
  answered_by: string | null;
  answer_key: string | null;
  answer_reason: string | null;
  answered_at: string | null;
}

export interface DecisionWaiting {
  decider: string;
  expires_at: string | null;
  /**
   * AP-9a: `person` for a person ask. Absent while `WAITING_ORGS` does not
   * hold the org, and on an older gateway.
   */
  source: string | null;
}

/**
 * AP-9a: an open person ask that the caller made. The id lets the asker
 * withdraw it. It holds no question and no answer.
 */
export interface OwnAsk {
  id: string;
  decider: string;
  due_at: string | null;
  created_at: string | null;
}

export interface TaskDecisions {
  decisions: DecisionRow[];
  waiting_on: DecisionWaiting[];
  /**
   * `null` while the person ask is off for the org (`WAITING_ORGS`). A list,
   * possibly empty, when both flags hold the org. The form draws from it.
   */
  asks: OwnAsk[] | null;
}

/** The reason box takes this many characters (the server's `REASON_MAX`). */
export const REASON_MAX = 500;

export const DECISION_COPY = {
  heading: "Decision",
  send: "Send answer",
  reasonLabel: "Reason (optional)",
  reasonPlaceholder: "Why this answer?",
  /** The answer is final, so the screen says it before the member sends. */
  finalHint: "Your answer is final and goes on the task as a comment.",
} as const;

function readOptions(raw: unknown): DecisionOption[] {
  if (!Array.isArray(raw)) return [];
  const out: DecisionOption[] = [];
  for (const o of raw) {
    const key = (o as { key?: unknown })?.key;
    const label = (o as { label?: unknown })?.label;
    if (typeof key === "string" && typeof label === "string") out.push({ key, label });
  }
  return out;
}

const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** The route's body, read defensively. A bad row is left out, never thrown. */
export function readTaskDecisions(raw: unknown): TaskDecisions {
  const body = (raw ?? {}) as { decisions?: unknown; waiting_on?: unknown; asks?: unknown };
  const decisions: DecisionRow[] = [];
  for (const r of Array.isArray(body.decisions) ? body.decisions : []) {
    const row = r as Record<string, unknown>;
    const id = text(row?.id);
    const state = row?.state;
    if (!id || (state !== "open" && state !== "answered" && state !== "expired")) continue;
    decisions.push({
      id,
      subject_id: text(row.subject_id) ?? "",
      decider: text(row.decider) ?? "",
      options: readOptions(row.options),
      question: text(row.question) ?? "",
      asked_by: text(row.asked_by) ?? "",
      state,
      created_at: text(row.created_at),
      expires_at: text(row.expires_at),
      answered_by: text(row.answered_by),
      answer_key: text(row.answer_key),
      answer_reason: text(row.answer_reason),
      answered_at: text(row.answered_at),
    });
  }
  const waiting_on: DecisionWaiting[] = [];
  for (const w of Array.isArray(body.waiting_on) ? body.waiting_on : []) {
    const rec = w as Record<string, unknown>;
    const decider = text(rec?.decider);
    if (decider) {
      waiting_on.push({ decider, expires_at: text(rec.expires_at), source: text(rec.source) });
    }
  }
  let asks: OwnAsk[] | null = null;
  if (Array.isArray(body.asks)) {
    asks = [];
    for (const a of body.asks) {
      const rec = a as Record<string, unknown>;
      const id = text(rec?.id);
      const decider = text(rec?.decider);
      if (id && decider) {
        asks.push({ id, decider, due_at: text(rec.due_at), created_at: text(rec.created_at) });
      }
    }
  }
  return { decisions, waiting_on, asks };
}

/**
 * The state of a row NOW. The server fixes `state` at read time, so an open
 * row whose expiry passed since the read draws as expired without a new read.
 */
export function stateNow(row: DecisionRow, nowMs: number): DecisionState {
  if (row.state !== "open") return row.state;
  const end = row.expires_at ? Date.parse(row.expires_at) : NaN;
  return Number.isFinite(end) && end <= nowMs ? "expired" : "open";
}

/** The rows to draw: every open one first, then the newest of the rest. */
export function rowsToShow(rows: readonly DecisionRow[], nowMs: number): DecisionRow[] {
  const open = rows.filter((r) => stateNow(r, nowMs) === "open");
  const rest = rows.filter((r) => stateNow(r, nowMs) !== "open");
  return [...open, ...rest.slice(0, 1)];
}

/** Whether the block has anything to say. */
export function hasDecisionBlock(d: TaskDecisions | null, nowMs: number): boolean {
  if (!d) return false;
  return rowsToShow(d.decisions, nowMs).length > 0 || d.waiting_on.length > 0;
}

/** "Closes 12 Oct, 14:30": the expiry in the member's own zone. */
export function closesText(iso: string | null, verb = "Closes"): string {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return "";
  const day = at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const clock = at.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${verb} ${day}, ${clock}`;
}

export function labelOf(row: DecisionRow, key: string | null): string {
  return row.options.find((o) => o.key === key)?.label ?? key ?? "";
}

/** The line for a decision that waits on another member. */
export function waitingLine(w: DecisionWaiting, nameOf: (email: string) => string): string {
  const end = closesText(w.expires_at, "It closes");
  if (w.source === "person") return `${nameOf(w.decider)} has an ask.${end ? ` ${end}.` : ""}`;
  return `A decision waits on ${nameOf(w.decider)}.${end ? ` ${end}.` : ""}`;
}

/** The line for an expired decision: what happened, then the next step. */
export function expiredLine(row: DecisionRow, nameOf: (email: string) => string): string {
  const who = row.asked_by ? nameOf(row.asked_by) : "the person who asked";
  return `This decision expired and takes no answer now. Ask ${who} to ask again if it still matters.`;
}

/** The failure copy of the answer: what happened and what to do next. */
export function answerFailure(
  status: number | undefined,
  message: string | undefined,
): { text: string; reload: boolean } {
  if (status === 409 && message === "decision_expired") {
    return {
      text: "This decision expired before your answer arrived. Ask the person who asked to ask again.",
      reload: true,
    };
  }
  if (status === 409) {
    return { text: "This decision already has an answer. The block shows it now.", reload: true };
  }
  if (status === 404) {
    return {
      text: "This decision is not yours to answer, or it no longer exists. The block shows what is left.",
      reload: true,
    };
  }
  if (status === 403) {
    return {
      text: "A person answers a decision, not an agent connection. Answer it in Metorite.",
      reload: false,
    };
  }
  return { text: "Could not send your answer. Your pick is still here. Try again.", reload: false };
}

/** The body of the one answer route. The member comes from the session. */
export function answerBody(key: string, reason: string): { answer_key: string; reason?: string } {
  const note = reason.trim();
  return note ? { answer_key: key, reason: note.slice(0, REASON_MAX) } : { answer_key: key };
}
