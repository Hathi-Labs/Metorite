/**
 * Projects · Questions for me — the rules of the list (WS-55 EA-18b).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §12.8 (D-EA-14).
 *
 * The server decides every row, every count and who may answer
 * (`routes/projects/agent_questions.py`). This module reads the wire
 * defensively and says how the list words what the server sent. It computes
 * no rule of its own: not who sees a question, not who answers it, and not if
 * a row is a typed archive request (`question.kind` is the server's).
 *
 * ⚠️ **One list, three doors.** The pane, the My Day card and the Approvals
 * tab (EA-18c) read this one route. No door keeps a copy.
 *
 * Fence: `agentQuestions.test.ts`.
 */
import type { AgentActivity, AgentOption } from "./agentRuns";

/** The sidebar entry, as `?app=questions` opens it. */
export const QUESTIONS_APP_ID = "questions";
export const QUESTIONS_LABEL = "Questions";
export const QUESTIONS_NOTE = "Agent questions that wait on you";

/** How often the list is read again while the page is open (spec §12.8). */
export const QUESTIONS_POLL_MS = 60_000;

/** How long "Answered by <name>" stays before the list reads again. */
export const ANSWERED_HOLD_MS = 1_500;

/** The largest page the route serves. The list asks for all of it. */
export const QUESTIONS_LIMIT = 200;

export type QuestionScope = "mine" | "everyone";
export type QuestionSort = "oldest" | "newest";
export type QuestionWhy = "created" | "assigned" | "my_agent";
export type QuestionKind = "question" | "archive_request";

/** A step of the project path, from the root to the leaf. */
export interface PathStep {
  id: string;
  name: string;
}

export interface QuestionBody {
  kind: QuestionKind;
  body: string | null;
  options: AgentOption[];
  allow_multiple: boolean;
}

/** One row of `GET /projects/agent-questions`. */
export interface QuestionRow {
  run_id: string;
  activity_id: string;
  task_id: string;
  task_number: number | null;
  task_title: string;
  agent_name: string | null;
  client_via: string | null;
  asked_at: string | null;
  run_started_at: string | null;
  project_path: PathStep[];
  question: QuestionBody;
  can_answer: boolean;
  why: QuestionWhy | null;
}

export interface QuestionsPage {
  rows: QuestionRow[];
  counts: { mine: number; everyone: number | null };
  total: number;
  offset: number;
  limit: number;
  capped: boolean;
}

const WHY_VALUES: readonly string[] = ["created", "assigned", "my_agent"];

function isRow(value: unknown): value is QuestionRow {
  const row = value as Partial<QuestionRow> | null;
  return Boolean(
    row &&
      typeof row.run_id === "string" &&
      typeof row.activity_id === "string" &&
      typeof row.task_id === "string" &&
      row.question &&
      typeof row.question === "object",
  );
}

function row(raw: QuestionRow): QuestionRow {
  const q = raw.question as Partial<QuestionBody>;
  return {
    ...raw,
    task_title: typeof raw.task_title === "string" ? raw.task_title : "",
    project_path: Array.isArray(raw.project_path)
      ? raw.project_path.filter((s) => s && typeof s.id === "string")
      : [],
    question: {
      kind: q.kind === "archive_request" ? "archive_request" : "question",
      body: typeof q.body === "string" ? q.body : null,
      options: Array.isArray(q.options) ? q.options : [],
      allow_multiple: q.allow_multiple === true,
    },
    can_answer: raw.can_answer === true,
    why: WHY_VALUES.includes(String(raw.why)) ? (raw.why as QuestionWhy) : null,
  };
}

/** Read the route's answer defensively. A shape it cannot read is an empty page. */
export function questionsPage(raw: unknown): QuestionsPage {
  const body = (raw && typeof raw === "object" ? raw : {}) as Partial<QuestionsPage>;
  const rows = Array.isArray(body.rows) ? body.rows.filter(isRow).map(row) : [];
  const counts = (body.counts ?? {}) as Partial<QuestionsPage["counts"]>;
  const everyone = counts.everyone;
  return {
    rows,
    counts: {
      mine: typeof counts.mine === "number" && Number.isFinite(counts.mine) ? counts.mine : 0,
      everyone: typeof everyone === "number" && Number.isFinite(everyone) ? everyone : null,
    },
    total: typeof body.total === "number" ? body.total : rows.length,
    offset: typeof body.offset === "number" ? body.offset : 0,
    limit: typeof body.limit === "number" ? body.limit : QUESTIONS_LIMIT,
    capped: body.capped === true,
  };
}

/** The query of one read. No organization and no address: the session owns both. */
export function questionsQuery(opts: {
  scope?: QuestionScope;
  project?: string | null;
  sort?: QuestionSort;
  limit?: number;
}): string {
  const q = new URLSearchParams();
  q.set("scope", opts.scope ?? "mine");
  if (opts.project) q.set("project", opts.project);
  q.set("sort", opts.sort ?? "oldest");
  q.set("limit", String(opts.limit ?? QUESTIONS_LIMIT));
  return q.toString();
}

/* ── The words ───────────────────────────────────────────────────────────── */

export const QUESTIONS_COPY = {
  title: "Questions",
  allProjects: "All projects",
  projectFilter: "Project",
  sortOldest: "Oldest first",
  sortNewest: "Newest first",
  sortLabel: "Sort",
  emptyMine: "No question waits on you.",
  emptyMineHint: "When an agent asks, it shows here.",
  emptyEveryone: "No agent waits on anyone.",
  loading: "Loading the questions",
  readFailed: "Could not read the questions.",
  off: "Questions are not on for your organization.",
  offHint: "Ask an owner or an admin to turn them on.",
  openTask: "Open task",
  answeredHold: "Answered",
} as const;

/** The tab words: "For me · 3" and "Everyone · 8". */
export function tabLabel(scope: QuestionScope, count: number | null | undefined): string {
  const head = scope === "mine" ? "For me" : "Everyone";
  return typeof count === "number" ? `${head} · ${count}` : head;
}

/** The "Everyone" tab shows only when the server sent its count. */
export function everyoneTabShown(counts: QuestionsPage["counts"] | null | undefined): boolean {
  return typeof counts?.everyone === "number";
}

/** The sidebar badge: the "mine" count, or nothing for zero. */
export function badgeCount(page: Pick<QuestionsPage, "counts"> | null | undefined): number | undefined {
  const n = page?.counts.mine;
  return typeof n === "number" && n > 0 ? n : undefined;
}

/** Why the reader sees a row. A reader with two reasons sees the first the server sent. */
export const WHY_TAG: Readonly<Record<QuestionWhy, string>> = {
  created: "You created this task",
  assigned: "Assigned to you",
  my_agent: "Your agent asked",
};

export function whyTag(why: QuestionWhy | null | undefined): string | null {
  return why ? WHY_TAG[why] : null;
}

/** "Showing the oldest 200", or "the newest 200" under the other sort. */
export function cappedLine(
  page: Pick<QuestionsPage, "capped" | "rows">,
  sort: QuestionSort,
): string | null {
  if (!page.capped) return null;
  return `Showing the ${sort === "newest" ? "newest" : "oldest"} ${page.rows.length}`;
}

/** The empty state of a tab. */
export function emptyCopy(scope: QuestionScope): { message: string; hint?: string } {
  return scope === "mine"
    ? { message: QUESTIONS_COPY.emptyMine, hint: QUESTIONS_COPY.emptyMineHint }
    : { message: QUESTIONS_COPY.emptyEveryone };
}

/** The sentence of a row the reader may not answer. */
export function readOnlyLine(who: string): string {
  return `${who} or an admin can answer this`;
}

/* ── Age ─────────────────────────────────────────────────────────────────── */

/** "just now", "12 m", "3 h" or "2 d". The age of one moment. */
export function ageText(iso: string | null | undefined, now: number): string | null {
  const at = Date.parse(iso ?? "");
  if (Number.isNaN(at)) return null;
  const mins = Math.max(0, Math.floor((now - at) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} m`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

/** "2 h 41 m", "12 m" or "2 d 3 h". A length of time. */
export function durationText(iso: string | null | undefined, now: number): string | null {
  const at = Date.parse(iso ?? "");
  if (Number.isNaN(at)) return null;
  const mins = Math.max(0, Math.floor((now - at) / 60_000));
  if (mins < 1) return "under a minute";
  if (mins < 60) return `${mins} m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ${mins % 60} m`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

/** "Claude Code asked 3 h ago". */
export function askedLine(r: Pick<QuestionRow, "agent_name" | "asked_at">, now: number): string {
  const agent = r.agent_name || "An agent";
  const age = ageText(r.asked_at, now);
  return age === "just now" ? `${agent} asked just now` : age ? `${agent} asked ${age} ago` : `${agent} asked`;
}

/** "Run started 3 h ago · waiting 2 h 41 m". */
export function runLine(
  r: Pick<QuestionRow, "run_started_at" | "asked_at">,
  now: number,
): string | null {
  const started = ageText(r.run_started_at, now);
  const waiting = durationText(r.asked_at, now);
  const parts: string[] = [];
  if (started) parts.push(started === "just now" ? "Run started just now" : `Run started ${started} ago`);
  if (waiting) parts.push(`waiting ${waiting}`);
  return parts.length ? parts.join(" · ") : null;
}

/* ── Grouping ────────────────────────────────────────────────────────────── */

/** "Metorite › Metorite Application › AI Studio". */
export function pathLabel(path: readonly PathStep[]): string {
  return path.map((s) => s.name).join(" › ");
}

export interface QuestionGroup {
  key: string;
  path: PathStep[];
  rows: QuestionRow[];
}

/**
 * Rows under the project path of their task, in the order the server sent
 * them. A group stands where its first row stood. A row whose path is empty
 * (every parent hidden) gathers in one group with no heading.
 */
export function groupByProject(rows: readonly QuestionRow[]): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const r of rows) {
    const key = r.project_path.map((s) => s.id).join("/");
    const held = groups.get(key);
    if (held) held.rows.push(r);
    else groups.set(key, { key, path: r.project_path, rows: [r] });
  }
  return [...groups.values()];
}

/** "2 questions", "1 question". */
export function questionsCount(n: number): string {
  return n === 1 ? "1 question" : `${n} questions`;
}

/* ── The answer ──────────────────────────────────────────────────────────── */

/** The open question of a row, as the shared answer card reads an elicitation. */
export function questionActivity(r: QuestionRow): AgentActivity {
  return {
    id: r.activity_id,
    seq: 0,
    type: "elicitation",
    content: { body: r.question.body },
    signal: "select",
    signal_metadata: { options: r.question.options, allow_multiple: r.question.allow_multiple },
    created_at: r.asked_at,
  };
}

/**
 * Where the answer of a row goes. The server's `question.kind` decides: a typed
 * archive request goes to `archive-approval`, and never to `/activities`, which
 * answers 422 for it (spec §12.8).
 */
export function answerRoute(r: Pick<QuestionRow, "question">): "activities" | "archive-approval" {
  return r.question.kind === "archive_request" ? "archive-approval" : "activities";
}

/** The path of a project in the Projects app, for a name in a heading. */
export function projectHref(id: string): string {
  return `/projects?project=${encodeURIComponent(id)}`;
}

/** The deep link of a task: the shape the board and the bell read. */
export function taskHref(id: string): string {
  return `/projects?task=${encodeURIComponent(id)}`;
}
