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

/**
 * `term` (WS-53 §18.16): which field the org's own word for a figure means.
 * The chat opens it when two fields give different totals for the word.
 */
export type QuestionKind = "purpose" | "key_field" | "slot" | "stage_value" | "revenue" | "term";

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
  /** CRM-Q33: what a money field holds, such as "Excludes GST" or "After TDS". */
  evidence?: string[] | null;
};

/** One choice of "Pick another". */
export type QuestionOption = {
  value: string;
  label: string;
  fill?: number | null;
  /** CRM-Q33: what a money field holds. */
  evidence?: string[] | null;
};

/**
 * One answer that a member proposed in the chat (CRM-Q32-8). The server
 * sends the display name, or "A member", and never an email. `value` is
 * `null` for "None of these".
 */
export type QuestionProposal = { name: string; value: string | null; label: string; at: string };

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
  /**
   * The answer in use, for a correction. `null` on an open question. A sum
   * of fields (CRM-Q32-9) is the list of its keys.
   */
  current: string | string[] | null;
  /** Who chose the answer in use: `admin`, `ai` or `code`. */
  by: string | null;
  /** `false` for `revenue`, which has a default. */
  dismissible: boolean;
  options?: QuestionOption[] | null;
  /**
   * CRM-Q32-9: the fields of the picker of a sum, on an `amount` question,
   * when 2 or more currency fields each fill 20% or more. Most filled first.
   */
  sums?: QuestionOption[] | null;
  slot?: string | null;
  count?: number | null;
  value?: string | null;
  field?: string | null;
  /** CRM-Q32-8: the answers that members proposed in the chat, oldest first. */
  proposals?: QuestionProposal[] | null;
  /** CRM-Q33: the fields whose sum the business rules point at. */
  suggested_sum?: string[] | null;
  /**
   * CRM-Q33: on a part that an admin set, who set it (a display name, or
   * "A member", never an email) and when.
   */
  set_by?: string | null;
  set_at?: string | null;
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
  /** `"@auto"`, or an earlier value, which can be a sum (CRM-Q32-9). */
  value?: AnswerValue;
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

/**
 * The value of an answer: a choice, `null` ("None of these"), or, for the
 * `amount` of a module only, a list of 2 to 5 field keys, a sum (CRM-Q32-9).
 */
export type AnswerValue = string | string[] | null;

/** The parts of a correction, as `PUT /crm/catalogue/{module}` takes them. */
export type CatalogueParts = Partial<Record<CataloguePart, AnswerValue>>;

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
//
// Owner feedback, 2026-10-18, on the DEWiN screen: the end user does not
// know the internal name of the AI, and a card that showed only "Collection:
// amount" and some tiles did not say what it asked. So each card now asks a
// full question, says why we ask, and says what the answer changes, with an
// example of a question the founder can then ask. Say "the AI" on screen.
// `crmQ32.test.ts` fails if an exported string names the engine.

export const QUESTIONS_TITLE = "Open questions";
export const QUESTIONS_NOTE =
  "The AI could not decide these on its own. Each answer tells Metorite how to read your data. It applies at once in totals, reports and chat, and you can undo it.";
export const NO_QUESTIONS =
  "No open questions. The AI understands your data, or you answered each question.";
export const QUESTIONS_UNREAD = "Could not load the questions. Try again.";
export const ANSWERS_UNREAD = "Could not load your answers. Try again.";
export const NO_ANSWERS = "No answer is in use yet. Each answer you give shows here.";
export const TRY_AGAIN = "Try again";
/** "None of these" of a module question (a purpose or a slot). */
export const NONE_LABEL = "None of these";
/** "None of these" of a key field question. */
export const NONE_FIELDS_LABEL = "None of these fields";
export const PICK_FIELD = "Choose a different field";
export const PICK_PURPOSE = "Choose a different kind";
export const NOT_RELEVANT = "Does not apply";
/** The tooltip of "Does not apply". */
export const NOT_RELEVANT_TIP = "Hide this question. It comes back only if your Zoho fields change.";
export const WHY_HEADING = "Why we ask";
export const CHANGES_HEADING = "What your answer changes";
export const ANSWERS_TAB = "Your answers";
export const LET_METORITE = "Let Metorite choose";
export const NONE_VALUE_LABEL = "None";
export const UNDO_LABEL = "Undo";
export const CANCEL_LABEL = "Cancel";
/** The revenue badge: what applies until the admin answers. */
export const REVENUE_DEFAULT_BADGE = "Using Billed for now";
/** The revenue card: what applies until the admin answers. */
export const REVENUE_DEFAULT_LINE = "Until you answer, Metorite counts what you billed.";
/** A slot choice opens the panel of the suggestion before it applies. */
export const SLOT_PREVIEW_LINE = "You see a preview first";

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

/**
 * The singular noun of one record of a module. The wire has no noun, so
 * each card says "record". A later wire field can fill this one place.
 */
export function recordNoun(): string {
  return "record";
}

/** The plain words of each purpose, one for each word of `catalogue.PURPOSES`. */
export const PURPOSE_WORDS: Record<string, { label: string; example: string }> = {
  customer_company: { label: "Customers", example: "Companies or people you sell to" },
  customer_site: { label: "Customer sites", example: "The branches or plants of a customer" },
  person: { label: "People", example: "The contacts at your customers" },
  lead: { label: "Leads", example: "People or companies that can buy from you later" },
  deal: { label: "Deals or quotes", example: "Sales that are still open, each with a value" },
  contract: { label: "Orders or contracts", example: "Sales that the customer agreed to" },
  invoice: { label: "Invoices", example: "The bills that you send to customers" },
  payment_in: { label: "Payments received", example: "The money that customers pay you" },
  receivable: { label: "Money owed to you", example: "The amounts that customers must still pay" },
  payment_out: { label: "Payments you made", example: "The money that you pay to vendors" },
  product: { label: "Products", example: "The items or services that you sell" },
  asset: { label: "Assets", example: "The machines or items at a customer site" },
  vendor: { label: "Vendors", example: "The companies that you buy from" },
  partner: { label: "Partners", example: "Resellers or companies that work with you" },
  activity: { label: "Activities", example: "Visits, calls, tasks or service cases" },
  reference: { label: "Reference lists", example: "Price lists, codes or other lookup data" },
  other: { label: "Something else", example: "None of the kinds above" },
};

/** The plain words of each stage group. */
const STAGE_WORDS: Record<string, string> = { open: "Still open", won: "Won", lost: "Lost" };

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

/** The labels of the modules of a revenue choice: "Invoices and Bills". */
function moduleList(c: QuestionChoice | undefined, fallback: string): string {
  const names = (c?.modules ?? []).filter((m) => m !== "");
  if (names.length === 0) return fallback;
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The three sentences of a card: the question, why we ask, and what the answer changes. */
export type QuestionCopy = { question: string; why: string; changes: string };

function keyFieldCopy(q: Question): QuestionCopy {
  const m = q.label;
  const r = recordNoun();
  const several = q.choices.length >= 2;
  const stale = q.reason === "stale" ? `The field that Metorite used is gone from ${m} in your Zoho.` : null;
  switch (q.part) {
    case "amount":
      return {
        question: `Which field holds the amount of each ${r} in ${m}?`,
        why:
          stale ??
          (several
            ? `${m} has several money fields, and the AI could not tell which one is the full amount.`
            : `The AI could not tell which field of ${m} holds the full amount.`),
        changes: `Metorite adds up this field for totals of ${m}, for example "How much did we collect this quarter?"`,
      };
    case "date":
      return {
        question: `Which date tells when each ${r} in ${m} happened?`,
        why: stale ?? (several ? `${m} has several date fields.` : `The AI could not tell which date of ${m} to use.`),
        changes: `Metorite groups ${m} by month or quarter on this date, for example "Collections by month this year".`,
      };
    case "status":
      return {
        question: `Which field shows the status of each ${r} in ${m}?`,
        why:
          stale ??
          (several
            ? `${m} has several fields that can hold a status.`
            : `The AI could not tell which field of ${m} holds the status.`),
        changes: `Metorite uses it to split ${m} into open, done and lost, for example "How many bills are unpaid?"`,
      };
    default:
      return {
        question: `Which field links each ${r} in ${m} to its customer?`,
        why:
          stale ??
          (several
            ? `${m} has several fields that can link to a customer.`
            : `The AI could not tell which field of ${m} links to the customer.`),
        changes: `Metorite uses it to answer per-customer questions, for example "Total collected from each client".`,
      };
  }
}

/** The question, why we ask, and what the answer changes, for each kind. */
export function questionCopy(q: Question): QuestionCopy {
  switch (q.kind) {
    case "revenue":
      return {
        question: "When you ask about revenue, what should Metorite count?",
        why: "Your CRM holds both invoices and payments, so 'revenue' can mean either.",
        changes: `Every revenue figure in chat, reports and the weekly summary uses this. ${REVENUE_DEFAULT_LINE}`,
      };
    case "key_field":
      return keyFieldCopy(q);
    case "purpose":
      return {
        question: `What kind of records does ${q.label} hold?`,
        why: `The name '${q.label}' does not tell the AI what its records are.`,
        changes: `The AI then knows which questions ${q.label} can answer.`,
      };
    case "slot": {
      const noun = q.label.toLowerCase();
      return {
        question: `Which module holds your ${noun}?`,
        why: `${q.label} holds no records in your Zoho, so Metorite looks for another module that can take its place.`,
        changes: `That module then shows as ${q.label} in the CRM and in reports. Nothing changes in Zoho.`,
      };
    }
    case "term": {
      const word = q.value ?? "";
      return {
        question: `When someone says "${word}", which field of ${q.label} do they mean?`,
        why: `"${word}" matches more than one field, and the fields give different totals. The chat asked a member until you answer.`,
        changes: `Every answer that uses "${word}" reads the field you choose, for everyone.`,
      };
    }
    case "stage_value": {
      const n = q.count ?? q.records ?? 0;
      const value = q.value ?? "";
      return {
        question: `What does the status '${value}' mean in ${q.label}?`,
        why: `Metorite cannot tell if '${value}' is open, won or lost, so these records show as Unsorted.`,
        changes: `${plural(n, "record with this status moves", "records with this status move")} to that group in reports.`,
      };
    }
  }
}

/** The title of a card: the question, as a full sentence. */
export function questionTitle(q: Question): string {
  return questionCopy(q).question;
}

export type ReasonBadge = { tone: "warning" | "neutral"; label: string };

/** The badge that names the reason. An answered part has none. */
export function reasonBadge(reason: QuestionReason, kind?: QuestionKind): ReasonBadge | null {
  if (reason === "stale") return { tone: "warning", label: "Field gone" };
  if (reason === "open" && kind === "revenue") return { tone: "neutral", label: REVENUE_DEFAULT_BADGE };
  if (reason === "unsure") return { tone: "warning", label: "Needs your answer" };
  if (reason === "open") return { tone: "neutral", label: "Needs your answer" };
  return null;
}

/** "52% of records have a value", or `null` with no fill. */
export function fillLabel(fill: number | null | undefined): string | null {
  if (typeof fill !== "number" || !Number.isFinite(fill)) return null;
  return `${Math.round(Math.max(0, Math.min(1, fill)) * 100)}% of records have a value`;
}

/**
 * One sample, as a person reads it. A plain number gets thousands
 * separators ("53713.8" is "53,713.8"). A date, a masked value and a code
 * with a leading zero stay as they are.
 */
export function sampleText(s: string): string {
  const m = /^(-?)(0|[1-9]\d*)(\.\d+)?$/.exec(s.trim());
  if (!m) return s;
  const whole = Number(m[2]);
  if (!Number.isSafeInteger(whole)) return s;
  return `${m[1]}${whole.toLocaleString("en-IN")}${m[3] ?? ""}`;
}

/** "Examples: 21,600 · 53,713.8", or `null` with no sample. */
export function examplesLine(samples: readonly string[] | null | undefined): string | null {
  const shown = (samples ?? []).filter((s) => s !== "").map(sampleText);
  return shown.length > 0 ? `Examples: ${shown.join(" · ")}` : null;
}

/** The records of a module or a choice: "8,681 records". */
export function recordsLabel(n: number | null | undefined): string | null {
  return typeof n === "number" && n > 0 ? plural(n, "record", "records") : null;
}

/**
 * The records line of a module question: "8,681 records in Collection". A
 * slot, a stage and revenue carry their counts in the choices or the
 * sentences, so they have none (visual review, 2026-10-18).
 */
export function recordsInLabel(q: Question): string | null {
  if (q.kind !== "purpose" && q.kind !== "key_field") return null;
  const n = recordsLabel(q.records);
  return n ? `${n} in ${q.label}` : null;
}

/** What one choice tile shows: its label, its lines, and its examples. */
export type ChoiceView = { label: string; lines: string[]; examples: string | null };

export function choiceView(q: Question, c: QuestionChoice): ChoiceView {
  switch (q.kind) {
    case "purpose": {
      const words = PURPOSE_WORDS[c.value];
      return { label: words?.label ?? c.label, lines: words ? [words.example] : [], examples: null };
    }
    case "stage_value":
      return { label: STAGE_WORDS[c.value] ?? c.label, lines: [], examples: null };
    case "revenue": {
      const line =
        c.value === "payment_in"
          ? `The money received, in ${moduleList(c, "your payments")}`
          : `The amounts on your ${moduleList(c, "invoices")}`;
      return { label: c.label, lines: [line], examples: null };
    }
    case "slot": {
      const lines = [recordsLabel(c.records), SLOT_PREVIEW_LINE].filter((l): l is string => l !== null);
      return { label: c.label, lines, examples: examplesLine(c.samples) };
    }
    default: {
      const fill = fillLabel(c.fill);
      // CRM-Q33: what the field holds, from its arithmetic and its label.
      const evidence = evidenceLine(c.evidence);
      const lines = [fill, evidence].filter((l): l is string => l !== null);
      return { label: c.label, lines, examples: examplesLine(c.samples) };
    }
  }
}

/** The label of "None of these": a key field names its fields. */
export function noneLabel(q: Question): string {
  return q.kind === "key_field" ? NONE_FIELDS_LABEL : NONE_LABEL;
}

/** The label of the one button of "Choose a different". */
export function pickLabel(q: Question): string {
  return q.kind === "purpose" ? PICK_PURPOSE : PICK_FIELD;
}

/** The options of "Choose a different": plain purpose words, or each field with its fill. */
export function pickOptions(q: Question): AnswerPick[] {
  return (q.options ?? []).map((o) => {
    if (q.kind === "purpose") {
      const words = PURPOSE_WORDS[o.value];
      return { value: o.value, label: words?.label ?? o.label, hint: words?.example };
    }
    return { value: o.value, label: o.label, hint: fillLabel(o.fill) ?? undefined };
  });
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
  return `Accept the AI's suggestions (${k})`;
}

export function applyLabel(k: number): string {
  return `Apply ${plural(k, "answer", "answers")}`;
}

/** One line of the dry run: "Collection: amount, IP Amount". */
export function batchLine(item: BestGuess): string {
  return `${item.label}: ${partWord(item.part)}, ${item.value_label}`;
}

export const BATCH_TITLE = "Accept the AI's suggestions";
export const BATCH_NOTE =
  "Each answer becomes a fixed fact. No record changes, and Undo puts back each one.";
export const BATCH_UNREAD = "Could not read the suggestions. Try again.";

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
): Array<{ module: string; parts: CatalogueParts }> {
  const out = new Map<string, CatalogueParts>();
  for (const u of undo) {
    if (!u.module || !u.part) continue;
    const parts = out.get(u.module) ?? {};
    parts[u.part as CataloguePart] = u.value ?? AUTO;
    out.set(u.module, parts);
  }
  return [...out].map(([module, parts]) => ({ module, parts }));
}

// ── The proposals of the members (CRM-Q32-8) ───────────────────────────────
//
// Owner direction, 2026-10-18: each member who meets unclear data in the chat
// can say what it means. A CRM admin's choice is the answer. Another member's
// choice is a proposal, and an admin confirms it here with one click.

export const CONFIRM_LABEL = "Confirm";
export const PROPOSALS_HEADING = "Proposed by your team";
export const SORT_LABEL = "Sort the questions";
export const SORT_IMPACT = "By impact";
export const SORT_PROPOSED = "Proposed first";

/** The order of the open tab: by impact (the read), or the proposed ones first. */
export type QuestionSort = "impact" | "proposed";

export const SORT_OPTIONS: ReadonlyArray<{ value: QuestionSort; label: string }> = [
  { value: "impact", label: SORT_IMPACT },
  { value: "proposed", label: SORT_PROPOSED },
];

/** The proposals of a question, or none. */
export function proposalsOf(q: Question): QuestionProposal[] {
  return Array.isArray(q.proposals) ? q.proposals : [];
}

/** True when a question of the list holds a proposal, so the sort can show. */
export function anyProposed(questions: readonly Question[]): boolean {
  return questions.some((q) => proposalsOf(q).length > 0);
}

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago", or the date. */
export function whenLabel(at: string, now: Date = new Date()): string {
  const then = new Date(at);
  const ms = now.getTime() - then.getTime();
  if (!Number.isFinite(ms)) return "";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${plural(minutes, "minute", "minutes")} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour", "hours")} ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${plural(days, "day", "days")} ago`;
  return then.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** "Proposed by Asha Rao · 3 hours ago". The name is never an email. */
export function proposalLine(p: QuestionProposal, now: Date = new Date()): string {
  const when = whenLabel(p.at, now);
  return when ? `Proposed by ${p.name} · ${when}` : `Proposed by ${p.name}`;
}

/** What a proposal would answer: the label of its value. */
export function proposalValueLabel(q: Question, p: QuestionProposal): string {
  if (p.value === null) return q.kind === "key_field" ? NONE_FIELDS_LABEL : NONE_LABEL;
  if (q.kind === "purpose" && PURPOSE_WORDS[p.value]) return PURPOSE_WORDS[p.value].label;
  return p.label || p.value;
}

/** The accessible name of a Confirm button: what it answers, and for whom. */
export function confirmLabel(q: Question, p: QuestionProposal): string {
  return `Confirm ${proposalValueLabel(q, p)}, proposed by ${p.name}, for ${q.label}`;
}

/** The newest time of a proposal of a question, or 0 with none. */
function newestProposal(q: Question): number {
  return proposalsOf(q).reduce((best, p) => Math.max(best, Date.parse(p.at) || 0), 0);
}

/**
 * The open questions in the order of the sort. "Proposed first" puts each
 * question with a proposal first, the newest proposal first, and keeps the
 * order of impact inside each part. "By impact" is the order of the read.
 */
export function sortQuestions(questions: readonly Question[], sort: QuestionSort): Question[] {
  if (sort !== "proposed") return [...questions];
  const proposed = questions.filter((q) => proposalsOf(q).length > 0);
  const rest = questions.filter((q) => proposalsOf(q).length === 0);
  return [...proposed.sort((a, b) => newestProposal(b) - newestProposal(a)), ...rest];
}

// ── The cap of 5 ───────────────────────────────────────────────────────────

/** The cards on screen, and the count that "Show <m> more" names. */
export function visibleQuestions(
  body: QuestionsBody,
  expanded: boolean,
  sort: QuestionSort = "impact",
): { cards: Question[]; more: number } {
  const all = sortQuestions(body.shown, sort);
  if (expanded) return { cards: all, more: 0 };
  const cards = all.slice(0, SHOWN);
  return { cards, more: Math.max(0, body.open - cards.length) };
}

export function moreLabel(m: number): string {
  return `Show ${m} more`;
}

// ── An answer, its toast and its Undo ──────────────────────────────────────

/** The toast after an answer. */
export function savedLine(q: Question, done: AnswerDone, value: AnswerValue): string {
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
  | { kind: "put"; module: string; parts: CatalogueParts }
  | { kind: "answer"; id: string; value: string }
  | { kind: "undismiss"; id: string };

export function undoOf(q: Question, done: AnswerDone, value: AnswerValue = null): UndoCall | null {
  // "None of these" on a slot records a dismissal. Its Undo removes it.
  if (q.kind === "slot" && value === null) return { kind: "undismiss", id: q.id };
  const u = done.undo;
  if (!u) return null;
  if (u.question) return { kind: "answer", id: u.question, value: typeof u.value === "string" ? u.value : AUTO };
  if (!u.module || !u.part) return null;
  // A sum the admin chose before comes back as its list (CRM-Q32-9).
  const before = q.reason === "answered" && q.by === "admin" ? q.current : AUTO;
  return { kind: "put", module: u.module, parts: { [u.part]: before } };
}

// ── Your answers (the corrections of CRM-Q31-4) ────────────────────────────

/** Who chose a part in use. */
export function byLine(q: Question, now: Date = new Date()): string {
  if (q.by === "admin") {
    // CRM-Q33: who set it and when, by display name, never an email.
    if (!q.set_by) return "You chose";
    const when = q.set_at ? whenLabel(q.set_at, now) : "";
    return when ? `Set by ${q.set_by} · ${when}` : `Set by ${q.set_by}`;
  }
  if (q.current === null) return "Not set";
  return "Metorite chose";
}

// ── Answer first, clarify over time (CRM-Q33) ──────────────────────────────

/** "Excludes GST · After TDS", or `null` with no evidence. */
export function evidenceLine(evidence: string[] | null | undefined): string | null {
  const words = (evidence ?? []).filter((w) => w.trim() !== "");
  return words.length > 0 ? words.join(" · ") : null;
}

/** The words of the choice that the business rules point at. */
export const RULE_SUM_LINE = "Your business rules point at these fields together.";

/**
 * The sum that the business rules point at, as one choice of the card:
 * its keys and its label ("IP Basic Amount + Rent Basic Amount"). `null`
 * with fewer than two fields, or on a question that is not an amount.
 */
export function suggestedSum(q: Question): { keys: string[]; label: string } | null {
  const keys = q.suggested_sum ?? [];
  if (q.kind !== "key_field" || q.part !== "amount" || keys.length < 2) return null;
  const labelOf = (key: string) =>
    q.choices.find((c) => c.value === key)?.label ?? q.options?.find((o) => o.value === key)?.label ?? key;
  return { keys, label: `Use the sum: ${keys.map(labelOf).join(" + ")}` };
}

/** The label of the answer in use: a field, a purpose, Billed or "None". */
export function currentLabel(q: Question): string {
  if (q.current === null) return NONE_VALUE_LABEL;
  if (Array.isArray(q.current)) return sumLabel(q, q.current);
  const found =
    q.choices.find((c) => c.value === q.current) ?? q.options?.find((o) => o.value === q.current);
  if (q.kind === "purpose" && PURPOSE_WORDS[q.current]) return PURPOSE_WORDS[q.current].label;
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
  const own = (q.options && q.options.length > 0 ? q.options : q.choices).map((o) => {
    const words = q.kind === "purpose" ? PURPOSE_WORDS[o.value] : undefined;
    return words
      ? { value: o.value, label: words.label, hint: words.example }
      : { value: o.value, label: o.label, hint: fillLabel(o.fill) ?? undefined };
  });
  const none = q.kind === "key_field" ? [{ value: NONE_PICK, label: NONE_VALUE_LABEL }] : [];
  // CRM-Q32-9: an amount that can be a sum offers the picker of a sum.
  const sum = sumOffered(q) ? [{ value: SUM_PICK, label: SUM_CHOICE_LABEL, hint: SUM_CHOICE_LINE }] : [];
  return [...base, ...own, ...none, ...sum];
}

/** The value a `SelectButton` shows for a part in use. */
export function pickValue(q: Question): string {
  if (q.by !== "admin") return AUTO;
  if (Array.isArray(q.current)) return SUM_PICK;
  return q.current === null ? NONE_PICK : q.current;
}

/** The value a pick sends: `null` for "None". */
export function pickToValue(pick: string): string | null {
  return pick === NONE_PICK ? null : pick;
}

/** The value that puts a part back as it was before a correction. */
export function correctionUndo(q: Question): AnswerValue {
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

// ── A sum of fields (CRM-Q32-9b, §18.14.10) ────────────────────────────────
//
// Some modules keep one amount in parts. DEWiN's Collection splits a payment
// into Rent Net Amount, IP Basic Amount and EB Net Amount, and each alone
// covers 52% of the records or less. So the `amount` question offers "These
// fields add up to the amount". The admin ticks 2 to 5 fields in a picker,
// the sum check shows how many records they cover, and a pair that holds
// the same money gets a warning. The warning never blocks the save.

/** The fewest and the most fields of a sum (`catalogue.SUM_MIN`, `SUM_MAX`). */
export const SUM_MIN = 2;
export const SUM_MAX = 5;

/** The wait after the last tick before the sum check runs. */
export const SUM_CHECK_DELAY = 400;

/** The value of the sum in a `SelectButton` of "Your answers". */
export const SUM_PICK = "@sum";

export const SUM_CHOICE_LABEL = "These fields add up to the amount";
export const SUM_CHOICE_LINE = "Choose 2 to 5 fields that together make one amount";
export const SUM_DIALOG_TITLE = "Add up fields for the amount";
export const SUM_SAVE = "Save the sum";
export const SUM_CHANGE = "Change";
export const SUM_CHECKING = "Checking these fields.";
export const SUM_CHECK_FAILED = "Could not check these fields. You can still save.";
export const SUM_FIELDS_LABEL = "Fields to add up";

/** The question of the picker: "Which fields add up to the amount of each record in Collection?" */
export function sumQuestion(q: Question): string {
  return `Which fields add up to the amount of each ${recordNoun()} in ${q.label}?`;
}

/** Why we ask, in the picker. */
export function sumWhy(q: Question): string {
  return `${q.label} can keep one amount in parts, in separate fields, so one field alone can miss money.`;
}

/** What the answer changes, in the picker. */
export function sumChanges(q: Question): string {
  return `Totals of ${q.label} add up these fields for each record, for example "How much did we collect this quarter?"`;
}

/** The card offers a sum: the `amount` of a module, with 2 or more fields in `sums`. */
export function sumOffered(q: Question): boolean {
  return q.kind === "key_field" && q.part === "amount" && (q.sums?.length ?? 0) >= SUM_MIN;
}

/** A value is a sum: a list of keys. */
export function isSum(value: AnswerValue | undefined): value is string[] {
  return Array.isArray(value);
}

/** The label of one key of the module, from the fields that the question knows. */
function keyLabel(q: Question, key: string): string {
  return (
    q.sums?.find((o) => o.value === key)?.label ??
    q.options?.find((o) => o.value === key)?.label ??
    q.choices.find((c) => c.value === key)?.label ??
    key
  );
}

/** "A", "A and B", or "A, B and C". */
export function namesOf(labels: readonly string[]): string {
  if (labels.length < 2) return labels.join("");
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/** "Sum of Rent Net Amount, IP Basic Amount and EB Net Amount". */
export function sumLabel(q: Question, keys: readonly string[]): string {
  return `Sum of ${namesOf(keys.map((k) => keyLabel(q, k)))}`;
}

/** The ticks the picker opens with: the sum in use, else none. */
export function sumStart(q: Question): string[] {
  if (!isSum(q.current)) return [];
  const known = new Set((q.sums ?? []).map((o) => o.value));
  return q.current.filter((k) => known.has(k));
}

/**
 * One tick. A ticked field comes off. A new field goes on, unless 5 are
 * ticked. The list keeps the order of `sums`, so the same set is the same
 * list, and the read cache of the sum check meets it again.
 */
export function toggleSum(q: Question, picked: readonly string[], key: string): string[] {
  const on = new Set(picked);
  if (on.has(key)) on.delete(key);
  else if (on.size < SUM_MAX) on.add(key);
  const order = (q.sums ?? []).map((o) => o.value);
  return order.filter((k) => on.has(k));
}

/** A box is off limits: 5 are ticked, and it is not one of them. */
export function sumBlocked(picked: readonly string[], key: string): boolean {
  return !picked.includes(key) && picked.length >= SUM_MAX;
}

/** The set can be saved and checked: 2 to 5 different keys. */
export function sumReady(keys: readonly string[]): boolean {
  return keys.length >= SUM_MIN && keys.length <= SUM_MAX && new Set(keys).size === keys.length;
}

/** The line under the boxes, which says how many to tick. */
export function sumHint(n: number): string {
  if (n === 0) return "Tick 2 to 5 fields.";
  if (n === 1) return "Tick one more field.";
  if (n >= SUM_MAX) return `${SUM_MAX} fields are ticked. That is the most a sum takes.`;
  return `${n} fields are ticked. You can tick up to ${SUM_MAX}.`;
}

/** What one box of the picker shows: the label, the share of values, and the examples. */
export function sumFieldView(q: Question, o: QuestionOption): ChoiceView {
  const fill = fillLabel(o.fill);
  const samples = q.choices.find((c) => c.value === o.value)?.samples;
  return { label: o.label, lines: fill ? [fill] : [], examples: examplesLine(samples) };
}

/** `GET /crm/questions/{id}/sum-check?fields=`. */
export type SumCheck = {
  id: string;
  module: string;
  fields: string[];
  records: number;
  covered: number;
  overlap: number;
  capped: boolean;
  /** "These 3 fields cover 8,458 of 8,681 records (97%)." */
  line: string;
  pairs: Array<{ fields: string[]; labels: string[]; both: number; equal: number }>;
  /** "A and B hold the same value on 90% of records. A sum counts it twice." */
  warnings: string[];
};

/** The cache key of one sum check. It starts with `crm:`, so a write wakes it. */
export function sumCheckKey(id: string, keys: readonly string[]): string {
  return `crm:questions:sum:${id}:${keys.join(",")}`;
}

/** What the picker says about the ticked set. */
export type SumCheckView =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "done"; line: string; warnings: string[] };

export function sumCheckView(
  ready: boolean,
  check: SumCheck | undefined,
  error: unknown,
): SumCheckView {
  if (!ready) return { state: "idle" };
  if (check) return { state: "done", line: check.line, warnings: check.warnings };
  if (error) return { state: "failed", message: questionError(error, SUM_CHECK_FAILED) };
  return { state: "loading" };
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
