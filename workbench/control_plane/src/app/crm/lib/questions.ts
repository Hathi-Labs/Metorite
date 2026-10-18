// ── The open questions of the CRM (WS-53 CRM-Q32-3) ───────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.14. The CRM asks the
// admin each question that Jev could not answer with confidence about the
// data of the org. Each answer becomes a fixed fact for totals and chat.
//
// The gateway builds the questions (`GET /crm/questions`). This file holds
// the wire types, every word of the screens and each rule, as pure
// functions, so the test holds them without a browser. `lib/suggestions.ts`
// holds the words of the suggestions the same way.
//
// No confidence number and no engine word reaches the screen. A fill share
// ("52% filled") is a fact about the data, not a confidence.

import { CrmError } from "./api";

// ── The wire ───────────────────────────────────────────────────────────────

export type QuestionKind = "purpose" | "key_field" | "slot" | "stage_value" | "revenue";

/** `open` (not asked), `unsure`, `stale` (the field is gone), or `answered` (in use). */
export type QuestionReason = "open" | "unsure" | "stale" | "answered";

/** One choice of a question. */
export type QuestionChoice = {
  value: string;
  label: string;
  /** Jev's best guess. */
  best: boolean;
  /** A `key_field` choice: the share of the records that fill the field. */
  fill?: number | null;
  /** Masked samples of a field, or masked names of the records of a module. */
  samples?: string[] | null;
  /** A `slot` choice: the records of the module. */
  records?: number | null;
  /** A `revenue` choice: the labels of its modules. */
  modules?: string[] | null;
};

/** One choice of "Pick another". */
export type QuestionOption = { value: string; label: string; fill?: number | null };

/** One question (§18.14.3, "The wire"). */
export type Question = {
  id: string;
  kind: QuestionKind;
  module: string | null;
  label: string;
  part: string | null;
  reason: QuestionReason;
  records: number;
  money: boolean;
  why: string;
  choices: QuestionChoice[];
  /** "None of these" is a legal answer. */
  none: boolean;
  /** "Pick another" offers `options`. */
  other: boolean;
  /** The answer in use, for a correction. `null` on an open question. */
  current: string | null;
  /** Who chose the answer in use: `admin`, `ai` or `code`. */
  by: string | null;
  /** `false` for `revenue`, which has a default. */
  dismissible: boolean;
  options?: QuestionOption[] | null;
  slot?: string | null;
  count?: number | null;
  value?: string | null;
  field?: string | null;
};

/** `GET /crm/questions`. */
export type QuestionsBody = {
  open: number;
  shown: Question[];
  more: number;
  /** With `all=1`: each part in use, with `current`. */
  answered: Question[];
  /** The approved version of the plan that the read saw. */
  plan: number | null;
};

/** What the Undo of an answer sends (`AnswerOut.undo`). */
export type QuestionUndo = {
  module?: string | null;
  part?: string | null;
  value?: string | null;
  /** A `stage_value`: the answer route of this id again. */
  question?: string | null;
};

/** `POST /crm/questions/{id}/answer`. */
export type AnswerDone = {
  id: string;
  kind: QuestionKind;
  version: number | null;
  line: string | null;
  undo: QuestionUndo | null;
  moved: number | null;
  slot: string | null;
  module: string | null;
};

export type BestGuess = {
  id: string;
  label: string;
  part: string | null;
  value: string;
  value_label: string;
  line: string;
};

/** `POST /crm/questions/accept-best`. */
export type AcceptBestDone = {
  dry_run: boolean;
  items: BestGuess[];
  version: number | null;
  undo: QuestionUndo[];
};

/** The parts that `PUT /crm/catalogue/{module}` takes. */
export type CataloguePart = "purpose" | "amount" | "date" | "status" | "parent" | "revenue";

// ── The keys and the gate ──────────────────────────────────────────────────

/**
 * The most questions one read holds (`MAX_LIMIT` of the gateway). The
 * section reads them all at once, so the batch can take each best guess,
 * and the screen shows `SHOWN` of them.
 */
export const READ_LIMIT = 200;

/** The cards the section shows before "Show <m> more" (§18.14.7). */
export const SHOWN = 5;

/**
 * The cache key of the open questions. The page, the rail and the section
 * share this one read. `invalidate("crm:")` after a write wakes it.
 */
export const QUESTIONS_KEY = "crm:questions";

/** The cache key of "Your answers" (`all=1`). */
export const ANSWERS_KEY = "crm:questions:all";

/** The value of a part that puts back the choice of Metorite. */
export const AUTO = "@auto";

/** The org-level item of the meaning of revenue. */
export const ORG_MODULE = "@org";

/**
 * The read of the questions. `off` is a 404, a 403 or a 409 of
 * `GET /crm/questions`: the org is outside `CRM_QUESTIONS_ORGS`, the
 * caller is not an admin, or the CRM is not a Mirror. Each hides the
 * section, the rail count and the "Needs you" item (§18.14.5).
 */
export type QuestionsRead = { off: true } | { off: false; body: QuestionsBody };

/** The statuses of the read that mean "no questions here". */
const OFF_STATUSES = new Set([403, 404, 409]);

/** A failed read as `off`, when it says the questions are not for this caller. */
export function readQuestions(fetch: () => Promise<QuestionsBody>): Promise<QuestionsRead> {
  return fetch().then(
    (body) => ({ off: false as const, body }),
    (err: unknown) => {
      if (err instanceof CrmError && OFF_STATUSES.has(err.status)) return { off: true as const };
      throw err;
    },
  );
}

/**
 * The gate of the section. `unknown` while the first read runs: the
 * section waits, so a Mirror org outside the list never sees the row come
 * and go. `on` after an answer, and after a failure that is not `off`, so
 * the section can say that it could not load.
 */
export type QuestionsGate = "unknown" | "off" | "on";

export function questionsGate(read: QuestionsRead | undefined, failed: boolean): QuestionsGate {
  if (read === undefined) return failed ? "on" : "unknown";
  return read.off ? "off" : "on";
}

/** The open count of the rail and the default section. 0 when off or unknown. */
export function openCount(read: QuestionsRead | undefined): number {
  return read && !read.off ? Math.max(0, read.body.open) : 0;
}

/** The count a rail row shows: the open count above 0, else nothing. */
export function railCount(n: number): number | null {
  return n > 0 ? n : null;
}

// ── The words ──────────────────────────────────────────────────────────────

export const QUESTIONS_TITLE = "Open questions";
export const QUESTIONS_NOTE =
  "Jev was not sure about these. Your answer becomes a fixed fact for totals and chat.";
export const NO_QUESTIONS =
  "No open questions. Jev is sure about your data, or you answered each question.";
export const QUESTIONS_UNREAD = "Could not load the questions. Try again.";
export const ANSWERS_UNREAD = "Could not load your answers. Try again.";
export const NO_ANSWERS = "No answer is in use yet. Each answer you give shows here.";
export const TRY_AGAIN = "Try again";
export const NONE_LABEL = "None of these";
export const PICK_FIELD = "Pick another field";
export const PICK_PURPOSE = "Pick another";
export const NOT_RELEVANT = "Not relevant";
export const ANSWERS_TAB = "Your answers";
export const LET_METORITE = "Let Metorite choose";
export const NONE_VALUE_LABEL = "None";
export const UNDO_LABEL = "Undo";
export const CANCEL_LABEL = "Cancel";
/** The revenue card: what applies until the admin answers. */
export const REVENUE_DEFAULT_LINE = "Until you answer, Metorite uses Billed.";

/** The tab of the open questions: "Open (3)". */
export function openTabLabel(n: number): string {
  return `Open (${n})`;
}

/** What each part is called on a card. */
const PART_WORDS: Record<string, string> = {
  purpose: "purpose",
  amount: "amount",
  date: "date",
  status: "status",
  parent: "customer",
  revenue: "revenue",
};

export function partWord(part: string | null | undefined): string {
  return part ? (PART_WORDS[part] ?? part) : "";
}

/** The top line of a card: "Collection: amount". */
export function questionTitle(q: Question): string {
  switch (q.kind) {
    case "revenue":
      return "Revenue: billed or collected";
    case "slot":
      return `${q.label}: where the records are`;
    case "stage_value":
      return `${q.label}: the stage "${q.value ?? ""}"`;
    default:
      return `${q.label}: ${partWord(q.part)}`;
  }
}

export type ReasonBadge = { tone: "warning" | "neutral"; label: string };

/** The badge that names the reason. An answered part has none. */
export function reasonBadge(reason: QuestionReason): ReasonBadge | null {
  if (reason === "unsure") return { tone: "warning", label: "Not sure" };
  if (reason === "stale") return { tone: "warning", label: "Field gone" };
  if (reason === "open") return { tone: "neutral", label: "Not asked" };
  return null;
}

/** "52% filled", or `null` with no fill. */
export function fillLabel(fill: number | null | undefined): string | null {
  if (typeof fill !== "number" || !Number.isFinite(fill)) return null;
  return `${Math.round(Math.max(0, Math.min(1, fill)) * 100)}% filled`;
}

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

/** The records of a module or a choice: "8,681 records". */
export function recordsLabel(n: number | null | undefined): string | null {
  return typeof n === "number" && n > 0 ? plural(n, "record", "records") : null;
}

/** A `slot` choice: "Preview Commercials as Deals". */
export function slotChoiceLabel(q: Question, c: QuestionChoice): string {
  return `Preview ${c.label} as ${q.label}`;
}

/** The label of the one button of "Pick another". */
export function pickLabel(q: Question): string {
  return q.kind === "purpose" ? PICK_PURPOSE : PICK_FIELD;
}

// ── The batch ──────────────────────────────────────────────────────────────

/** A question joins a batch: a `purpose` or a `key_field` with Jev's best guess (§18.14.6). */
export function joinsBatch(q: Question): boolean {
  return (q.kind === "purpose" || q.kind === "key_field") && q.choices.some((c) => c.best);
}

/** The ids of the batch, in the order of the list. */
export function batchIds(questions: readonly Question[]): string[] {
  return questions.filter(joinsBatch).map((q) => q.id);
}

/** The button shows when 2 or more questions have a best guess. */
export function batchShown(ids: readonly string[]): boolean {
  return ids.length >= 2;
}

export function batchLabel(k: number): string {
  return `Accept Jev's best guesses (${k})`;
}

export function applyLabel(k: number): string {
  return `Apply ${plural(k, "answer", "answers")}`;
}

/** One line of the dry run: "Collection: amount, IP Amount". */
export function batchLine(item: BestGuess): string {
  return `${item.label}: ${partWord(item.part)}, ${item.value_label}`;
}

export const BATCH_TITLE = "Accept Jev's best guesses";
export const BATCH_NOTE =
  "Each answer becomes a fixed fact. No record changes, and Undo puts back each one.";
export const BATCH_UNREAD = "Could not read the best guesses. Try again.";

/** The toast after a batch. */
export function batchDoneLine(k: number): string {
  return `Saved ${plural(k, "answer", "answers")}.`;
}

/**
 * The Undo of a batch: ONE `PUT /crm/catalogue/{module}` for each module,
 * with `"@auto"` for each of its parts (§18.14.6).
 */
export function batchUndoCalls(
  undo: readonly QuestionUndo[],
): Array<{ module: string; parts: Partial<Record<CataloguePart, string | null>> }> {
  const out = new Map<string, Partial<Record<CataloguePart, string | null>>>();
  for (const u of undo) {
    if (!u.module || !u.part) continue;
    const parts = out.get(u.module) ?? {};
    parts[u.part as CataloguePart] = u.value ?? AUTO;
    out.set(u.module, parts);
  }
  return [...out].map(([module, parts]) => ({ module, parts }));
}

// ── The cap of 5 ───────────────────────────────────────────────────────────

/** The cards on screen, and the count that "Show <m> more" names. */
export function visibleQuestions(
  body: QuestionsBody,
  expanded: boolean,
): { cards: Question[]; more: number } {
  const all = body.shown;
  if (expanded) return { cards: all, more: 0 };
  const cards = all.slice(0, SHOWN);
  return { cards, more: Math.max(0, body.open - cards.length) };
}

export function moreLabel(m: number): string {
  return `Show ${m} more`;
}

// ── An answer, its toast and its Undo ──────────────────────────────────────

/** The toast after an answer. */
export function savedLine(q: Question, done: AnswerDone, value: string | null): string {
  if (done.line) return `Saved. ${done.line}`;
  if (q.kind === "stage_value") {
    const n = done.moved ?? q.count ?? 0;
    return `Saved. ${plural(n, "record leaves", "records leave")} Unsorted.`;
  }
  if (q.kind === "revenue") {
    return `Saved. Revenue is now ${value === "payment_in" ? "Collected" : "Billed"}.`;
  }
  if (value === null && q.kind === "key_field") {
    return `Saved. No field holds the ${partWord(q.part)} of ${q.label}.`;
  }
  if (value === null && q.kind === "slot") {
    return `Saved. Metorite will not ask about ${q.label} again, unless your Zoho changes.`;
  }
  if (value === null && q.kind === "purpose") return `Saved. ${q.label} now has the purpose Other.`;
  return "Saved.";
}

/** The toast after "Not relevant". */
export const DISMISSED_LINE = "Metorite will not ask this again, unless your Zoho changes.";

export const UNDONE_LINE = "Undone. The choice before is back.";

/** The toast after the Undo of a dismissal. */
export const ASKED_AGAIN_LINE = "Undone. The question is open again.";

/** A 404 `not_dismissed`: nothing to undo. A quiet line, not an error. */
export const NOT_DISMISSED_LINE = "Nothing to undo. The question is already open.";

/** The 404 of an Undo of a dismissal that does not exist. */
export function notDismissed(err: unknown): boolean {
  return err instanceof CrmError && err.status === 404 && err.code === "not_dismissed";
}

/**
 * What the Undo of an answer sends (§18.14.6, the table). `put`: the PUT of
 * the catalogue with the part. `answer`: the answer route of the same id
 * with `"@auto"` (a `stage_value`). `undismiss`: the DELETE of the
 * dismissal, for "None of these" on a slot. `null`: no Undo, because a slot
 * module goes through the panel of the suggestion and its own Undo.
 *
 * An open question puts back the choice of Metorite (`"@auto"`). A part
 * that the admin chose before gets that choice back.
 */
export type UndoCall =
  | { kind: "put"; module: string; parts: Partial<Record<CataloguePart, string | null>> }
  | { kind: "answer"; id: string; value: string }
  | { kind: "undismiss"; id: string };

export function undoOf(q: Question, done: AnswerDone, value: string | null = null): UndoCall | null {
  // "None of these" on a slot records a dismissal. Its Undo removes it.
  if (q.kind === "slot" && value === null) return { kind: "undismiss", id: q.id };
  const u = done.undo;
  if (!u) return null;
  if (u.question) return { kind: "answer", id: u.question, value: u.value ?? AUTO };
  if (!u.module || !u.part) return null;
  const before = q.reason === "answered" && q.by === "admin" ? q.current : AUTO;
  return { kind: "put", module: u.module, parts: { [u.part]: before } };
}

// ── Your answers (the corrections of CRM-Q31-4) ────────────────────────────

/** Who chose a part in use. */
export function byLine(q: Question): string {
  if (q.by === "admin") return "You chose";
  if (q.current === null) return "Not set";
  return "Metorite chose";
}

/** The label of the answer in use: a field, a purpose, Billed or "None". */
export function currentLabel(q: Question): string {
  if (q.current === null) return NONE_VALUE_LABEL;
  const found =
    q.choices.find((c) => c.value === q.current) ?? q.options?.find((o) => o.value === q.current);
  return found?.label ?? q.current;
}

/** The value of "None" in a `SelectButton`, which takes strings only. */
export const NONE_PICK = "@none";

export type AnswerPick = { value: string; label: string; hint?: string };

/**
 * The choices of a part in use: "Let Metorite choose", each option of the
 * part, and "None" for a key part. The value in use comes first after the
 * one choice of Metorite.
 */
export function answerPicks(q: Question): AnswerPick[] {
  const base: AnswerPick[] = [{ value: AUTO, label: LET_METORITE }];
  const own = (q.options && q.options.length > 0 ? q.options : q.choices).map((o) => ({
    value: o.value,
    label: o.label,
    hint: fillLabel(o.fill) ?? undefined,
  }));
  const none = q.kind === "key_field" ? [{ value: NONE_PICK, label: NONE_VALUE_LABEL }] : [];
  return [...base, ...own, ...none];
}

/** The value a `SelectButton` shows for a part in use. */
export function pickValue(q: Question): string {
  if (q.by !== "admin") return AUTO;
  return q.current === null ? NONE_PICK : q.current;
}

/** The value a pick sends: `null` for "None". */
export function pickToValue(pick: string): string | null {
  return pick === NONE_PICK ? null : pick;
}

/** The value that puts a part back as it was before a correction. */
export function correctionUndo(q: Question): string | null {
  return q.by === "admin" ? q.current : AUTO;
}

/** One module of "Your answers", with its parts in use. */
export type AnswerGroup = { module: string; label: string; parts: Question[] };

const PART_ORDER: Record<string, number> = {
  purpose: 0,
  amount: 1,
  date: 2,
  status: 3,
  parent: 4,
};

/** The answered parts, one group for each module, in the order of the read. Revenue is its own group. */
export function answerGroups(answered: readonly Question[]): AnswerGroup[] {
  const groups = new Map<string, AnswerGroup>();
  for (const q of answered) {
    const key = q.kind === "revenue" ? ORG_MODULE : (q.module ?? q.id);
    const label = q.kind === "revenue" ? "Revenue" : q.label;
    const group = groups.get(key) ?? { module: key, label, parts: [] };
    group.parts.push(q);
    groups.set(key, group);
  }
  for (const g of groups.values()) {
    g.parts.sort((a, b) => (PART_ORDER[a.part ?? ""] ?? 9) - (PART_ORDER[b.part ?? ""] ?? 9));
  }
  return [...groups.values()];
}

/** The module and the part that a correction writes. */
export function correctionTarget(q: Question): { module: string; part: CataloguePart } | null {
  if (q.kind === "revenue") return { module: ORG_MODULE, part: "revenue" };
  if (!q.module || !q.part) return null;
  return { module: q.module, part: q.part as CataloguePart };
}

// ── Errors ─────────────────────────────────────────────────────────────────

export const QUESTION_MOVED =
  "This question changed after you opened it. The list is up to date again.";
export const PLAN_MOVED =
  "The data map changed after you opened this. Nothing was saved. Look at the question again.";

/** The sentence of a failed write. A 409 says what moved, in plain words. */
export function questionError(err: unknown, fallback: string): string {
  if (err instanceof CrmError && err.status === 409) {
    if (err.code === "question_moved") return QUESTION_MOVED;
    if (err.code === "plan_moved") return PLAN_MOVED;
  }
  return err instanceof CrmError && err.message ? err.message : fallback;
}
