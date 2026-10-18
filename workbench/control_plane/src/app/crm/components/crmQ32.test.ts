// The open questions on screen (WS-53 CRM-Q32-3, and the frontend half of
// CRM-Q32-4), spec crm_platform.md §18.14.5, §18.14.6 and §18.14.9. The runner
// has no DOM, so each view is rendered to a string, and a wire that only the
// page holds is read from its source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { RECOMMENDED_LABEL } from "@/components/RecommendedBadge";
import { clearAll, peek, put } from "@/lib/dataCache";

import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import {
  AUTO,
  LET_METORITE,
  NONE_PICK,
  NO_QUESTIONS,
  PLAN_MOVED,
  QUESTIONS_NOTE,
  QUESTIONS_UNREAD,
  QUESTION_MOVED,
  REVENUE_DEFAULT_LINE,
  SHOWN,
  answerGroups,
  answerPicks,
  applyLabel,
  batchIds,
  batchLabel,
  batchLine,
  batchShown,
  batchUndoCalls,
  byLine,
  correctionTarget,
  correctionUndo,
  currentLabel,
  fillLabel,
  openCount,
  pickToValue,
  pickValue,
  questionError,
  questionTitle,
  questionsGate,
  railCount,
  readQuestions,
  reasonBadge,
  savedLine,
  undoOf,
  visibleQuestions,
  type AnswerDone,
  type Question,
  type QuestionsBody,
  type QuestionsRead,
} from "../lib/questions";
import { defaultSection, shownSection, visibleSections } from "../lib/settingsSections";
import CrmRail, { SettingsSectionRows } from "./CrmRail";
import {
  BatchBody,
  OpenQuestionsView,
  QuestionCard,
  YourAnswersView,
  type AnswersHandlers,
  type QuestionHandlers,
} from "./OpenQuestions";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const noop = () => undefined;

// ── Fixtures, in the shape of `crm_sync/questions.wire` ─────────────────────

const base = (over: Partial<Question>): Question => ({
  id: "key:Payment_Received:amount",
  kind: "key_field",
  module: "Payment_Received",
  label: "Collection",
  part: "amount",
  reason: "unsure",
  records: 8681,
  money: true,
  why: "Totals of Collection use this field.",
  choices: [],
  none: true,
  other: true,
  current: null,
  by: null,
  dismissible: true,
  options: [],
  ...over,
});

const KEY = base({
  choices: [
    { value: "IP_Amount", label: "IP Amount", fill: 0.52, samples: ["12400", "8000", "3250"], best: true },
    { value: "Amount_Due", label: "Amount Due", fill: 0.31, samples: ["900"], best: false },
  ],
  options: [
    { value: "IP_Amount", label: "IP Amount", fill: 0.52 },
    { value: "Amount_Due", label: "Amount Due", fill: 0.31 },
    { value: "Grand_Total", label: "Grand Total", fill: 0.05 },
  ],
});

const DATE = base({
  id: "key:Payment_Received:date",
  part: "date",
  reason: "stale",
  why: "Totals of Collection by month or quarter use this date.",
  choices: [{ value: "Paid_On", label: "Paid On", fill: 0.9, samples: ["2026-01-02"], best: true }],
  options: [{ value: "Paid_On", label: "Paid On", fill: 0.9 }],
});

const PURPOSE = base({
  id: "purpose:Visits",
  kind: "purpose",
  module: "Visits",
  label: "Visits",
  part: "purpose",
  reason: "open",
  records: 412,
  money: false,
  why: "Totals and chat answers about Visits wait for this answer.",
  choices: [{ value: "activity", label: "Activity", best: true }],
  options: [
    { value: "activity", label: "Activity" },
    { value: "other", label: "Other" },
  ],
});

const SLOT = base({
  id: "slot:deal",
  kind: "slot",
  module: null,
  label: "Deals",
  part: null,
  reason: "unsure",
  records: 0,
  why: "Deals holds no records. Commercials could stand in for it.",
  choices: [
    { value: "Commercials", label: "Commercials", records: 24, samples: ["A•••", "B•••"], best: true },
  ],
  other: false,
  options: null,
  slot: "deal",
});

const STAGE = base({
  id: "stage:deal:3f9a",
  kind: "stage_value",
  module: "Potentials",
  label: "Deals",
  part: null,
  reason: "open",
  records: 12,
  why: "12 records with this value show as Unsorted.",
  choices: [
    { value: "open", label: "Open", best: false },
    { value: "won", label: "Won", best: false },
    { value: "lost", label: "Lost", best: false },
  ],
  none: false,
  other: false,
  options: null,
  slot: "deal",
  count: 12,
  value: "Negotiation",
  field: "Stage",
});

const REVENUE = base({
  id: "revenue",
  kind: "revenue",
  module: null,
  label: "Revenue",
  part: null,
  reason: "open",
  records: 8681,
  why: "Each answer about revenue uses this. Until you answer, Metorite uses Billed.",
  choices: [
    { value: "invoice", label: "Billed", modules: ["Invoices"], best: false },
    { value: "payment_in", label: "Collected", modules: ["Collection"], best: false },
  ],
  none: false,
  other: false,
  dismissible: false,
  options: null,
});

const body = (shown: Question[], over: Partial<QuestionsBody> = {}): QuestionsBody => ({
  open: shown.length,
  shown,
  more: 0,
  answered: [],
  plan: 41,
  ...over,
});

const ON = (b: QuestionsBody): QuestionsRead => ({ off: false, body: b });

const handlers = (over: Partial<QuestionHandlers> = {}): QuestionHandlers => ({
  busy: null,
  onAnswer: noop,
  onDismiss: noop,
  onSlot: noop,
  ...over,
});

const answersHandlers: AnswersHandlers = { busy: null, onChange: noop, onRetry: noop };

const card = (q: Question, over: Partial<QuestionHandlers> = {}) =>
  renderToStaticMarkup(createElement(QuestionCard, { q, handlers: handlers(over) }));

const view = (props: Partial<Parameters<typeof OpenQuestionsView>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(OpenQuestionsView, {
      read: ON(body([KEY])),
      failed: false,
      onRetry: noop,
      tab: "open",
      onTab: noop,
      expanded: false,
      onExpand: noop,
      handlers: handlers(),
      onBatch: noop,
      answers: { body: null, failed: false, handlers: answersHandlers },
      ...props,
    }),
  );

/** The text a person reads, and the names a reader of the screen hears. */
const textOf = (html: string) =>
  [
    html.replace(/<[^>]+>/g, " "),
    ...[...html.matchAll(/(?:aria-label|title)="([^"]*)"/g)].map((m) => m[1]),
  ].join(" ");

/** No engine word and no confidence number (§18.14.9, CRM-Q31-4 item 2). */
const ENGINE_WORDS = /\bdraft\b|\bversion\b|\brole\b|\bslot\b|overlay|\bplan\b|confiden|\branked\b|0\.\d/i;

// ── Item 1: the section, gated by the read ─────────────────────────────────

describe("the section and its gate (item 1)", () => {
  it("shows to an admin in Mirror mode, before Suggestions, only while the read is on", () => {
    const on = visibleSections({ isAdmin: true, mode: "mirror", questions: "on" }).map((s) => s.id);
    expect(on.indexOf("questions")).toBe(on.indexOf("suggestions") - 1);
    for (const questions of ["off", "unknown", undefined] as const) {
      expect(visibleSections({ isAdmin: true, mode: "mirror", questions }).map((s) => s.id)).not.toContain(
        "questions",
      );
    }
    // A member and a Native CRM never see it, also with the gate on.
    expect(visibleSections({ isAdmin: false, mode: "mirror", questions: "on" }).map((s) => s.id)).not.toContain(
      "questions",
    );
    expect(visibleSections({ isAdmin: true, mode: "native", questions: "on" }).map((s) => s.id)).not.toContain(
      "questions",
    );
  });

  it("reads a 404 questions_off, a 403 and a 409 as off, and throws any other failure", async () => {
    const fail = (status: number, code?: string) => () =>
      Promise.reject(new CrmError("no", status, code));
    expect(await readQuestions(fail(404, "questions_off"))).toEqual({ off: true });
    expect(await readQuestions(fail(403))).toEqual({ off: true });
    expect(await readQuestions(fail(409, "not_mirror"))).toEqual({ off: true });
    await expect(readQuestions(fail(500))).rejects.toThrow("no");
    const b = body([KEY]);
    expect(await readQuestions(() => Promise.resolve(b))).toEqual({ off: false, body: b });
  });

  it("holds the gate unknown while the first read runs, and on after a failure the section can show", () => {
    expect(questionsGate(undefined, false)).toBe("unknown");
    expect(questionsGate(undefined, true)).toBe("on");
    expect(questionsGate({ off: true }, false)).toBe("off");
    expect(questionsGate(ON(body([])), false)).toBe("on");
    expect(openCount({ off: true })).toBe(0);
    expect(openCount(undefined)).toBe(0);
    expect(openCount(ON(body([KEY], { open: 9 })))).toBe(9);
  });

  it("opens the section first for an admin with open questions, and Setup with none", () => {
    const ctx = { isAdmin: true, mode: "mirror" as const, questions: "on" as const };
    expect(defaultSection({ ...ctx, openQuestions: 3 })).toBe("questions");
    expect(defaultSection({ ...ctx, openQuestions: 0 })).toBe("setup");
    expect(defaultSection({ ...ctx, questions: "off", openQuestions: 3 })).toBe("setup");
    expect(defaultSection({ isAdmin: false, mode: "mirror", questions: "on", openQuestions: 3 })).toBe("stages");
    // A link to the section of an org outside the list draws the default.
    expect(shownSection("questions", { ...ctx, questions: "off" })).toBe("setup");
  });

  it("wires one read to the gate, the rail count, the default and the card in the page", () => {
    const page = read("../page.tsx");
    expect(page).toContain('const questions = useQuestions(ctx.isAdmin && ctx.mode === "mirror");');
    expect(page).toContain("questions: questions.gate,");
    expect(page).toContain("openQuestions: questionsOpen,");
    expect(page).toContain('questions: questions.gate === "on" ? railCount(questionsOpen) : null,');
    expect(page).toContain('questions={questions.gate === "on" ? <OpenQuestionsCard /> : undefined}');
    // The page and the section share ONE read of GET /crm/questions.
    const comp = read("./OpenQuestions.tsx");
    expect(comp.match(/useCachedResource<QuestionsRead>\(enabled \? QUESTIONS_KEY/g)?.length).toBe(1);
    expect(page).not.toContain("listQuestions");
    expect(comp.match(/api\.listQuestions\(\{ limit: READ_LIMIT \}\)/g)?.length).toBe(1);
    expect(read("./PipelineSettings.tsx")).toContain('{readOnly && show("questions") && questions}');
  });

  it("draws nothing when the read says off", () => {
    expect(view({ read: { off: true } })).toBe("");
  });
});

// ── Items 2 and 7: the cards of each kind ──────────────────────────────────

describe("a key field card", () => {
  const html = card(KEY);

  it("names the module and the part, the reason and why it matters", () => {
    expect(questionTitle(KEY)).toBe("Collection: amount");
    expect(html).toContain("Collection: amount");
    expect(html).toContain("Not sure");
    expect(html).toContain("Totals of Collection use this field.");
    expect(html).toContain("8,681 records");
  });

  it("draws each choice with its fill and its samples, and marks Jev's best", () => {
    expect(html).toContain("IP Amount");
    expect(html).toContain("52% filled");
    expect(html).toContain("12400 · 8000 · 3250");
    expect(html.match(new RegExp(RECOMMENDED_LABEL, "g"))?.length).toBe(1);
    // The samples sit in muted mono text, under the label.
    expect(html).toMatch(/font-mono[^"]*text-muted-foreground[^>]*>12400/);
  });

  it("offers None of these, Pick another field and Not relevant", () => {
    expect(html).toContain("None of these");
    expect(html).toContain("Pick another field");
    expect(html).toContain("Not relevant");
  });

  it("stacks the choices on a phone, and sets three across from sm", () => {
    expect(html).toMatch(/<ul class="grid gap-1\.5 sm:grid-cols-3"/);
  });

  it("disables each control while an answer runs", () => {
    const busy = card(KEY, { busy: KEY.id });
    expect(busy.match(/disabled=""/g)?.length).toBeGreaterThanOrEqual(4);
  });

  it("carries no engine word and no confidence number", () => {
    expect(textOf(html)).not.toMatch(ENGINE_WORDS);
  });
});

describe("the other kinds", () => {
  it("names a gone field and a part that was not asked", () => {
    expect(reasonBadge("stale")).toEqual({ tone: "warning", label: "Field gone" });
    expect(reasonBadge("open")).toEqual({ tone: "neutral", label: "Not asked" });
    expect(reasonBadge("answered")).toBeNull();
    expect(card(DATE)).toContain("Field gone");
    expect(card(PURPOSE)).toContain("Not asked");
  });

  it("offers a purpose with Pick another", () => {
    const html = card(PURPOSE);
    expect(html).toContain("Visits: purpose");
    expect(html).toContain("Activity");
    expect(html).toContain("Pick another");
    expect(html).not.toContain("Pick another field");
  });

  it("previews a module as a core list", () => {
    const html = card(SLOT);
    expect(html).toContain("Preview Commercials as Deals");
    expect(html).toContain("24 records");
    expect(html).toContain("A••• · B•••");
    expect(html).toContain("None of these");
    expect(textOf(html)).not.toMatch(ENGINE_WORDS);
  });

  it("draws a stage value with its count and Open, Won and Lost", () => {
    const html = card(STAGE);
    expect(html).toContain("Deals: the stage &quot;Negotiation&quot;");
    expect(html).toContain("12 records with this value show as Unsorted.");
    for (const word of ["Open", "Won", "Lost"]) expect(html).toContain(`>${word}<`);
    expect(html).not.toContain("None of these");
  });

  it("draws revenue as two choices with their modules, and takes no dismissal", () => {
    const html = card(REVENUE);
    expect(html).toContain("Billed");
    expect(html).toContain("Collected");
    expect(html).toContain(">Invoices<");
    expect(html).toContain(">Collection<");
    expect(html).toContain(REVENUE_DEFAULT_LINE);
    // The count of one module would read as the size of revenue.
    expect(html).not.toContain("records");
    expect(html).not.toContain("Not relevant");
    expect(html).not.toContain("None of these");
  });

  it("shows only Pick another field when no candidate is left", () => {
    const html = card(base({ choices: [], options: [{ value: "Total", label: "Total" }] }));
    expect(html).not.toContain('aria-label="Choices"');
    expect(html).toContain("Pick another field");
  });

  it("reads a fill as a share of filled records", () => {
    expect(fillLabel(0.52)).toBe("52% filled");
    expect(fillLabel(1.4)).toBe("100% filled");
    expect(fillLabel(null)).toBeNull();
  });
});

// ── Item 2 and 7: the section, its states and the cap of 5 ─────────────────

describe("the section", () => {
  it("opens with the title, the note and the two tabs", () => {
    const html = view({ read: ON(body([KEY, PURPOSE], { open: 2 })) });
    expect(html).toContain("Open questions");
    expect(html).toContain(QUESTIONS_NOTE);
    expect(html).toContain("Open (2)");
    expect(html).toContain("Your answers");
    expect(html).toContain('role="tablist"');
  });

  it("draws the empty state with no open question", () => {
    const html = view({ read: ON(body([])) });
    expect(html).toContain(NO_QUESTIONS);
    expect(html).not.toContain('aria-label="Choices"');
  });

  it("draws the error state with Try again, and a skeleton while it loads", () => {
    const failed = view({ read: undefined, failed: true });
    expect(failed).toContain(QUESTIONS_UNREAD);
    expect(failed).toContain("Try again");
    const loading = view({ read: undefined, failed: false });
    expect(loading).not.toContain(QUESTIONS_UNREAD);
    expect(loading).toContain("animate-pulse");
  });

  it("shows 5 cards, then Show <m> more, and all of them after a click", () => {
    const many = Array.from({ length: 7 }, (_, i) => base({ id: `key:M${i}:amount`, label: `M${i}` }));
    const b = body(many, { open: 7 });
    expect(visibleQuestions(b, false).cards).toHaveLength(SHOWN);
    expect(visibleQuestions(b, false).more).toBe(2);
    const html = view({ read: ON(b) });
    expect(html.match(/data-question="key_field"/g)).toHaveLength(5);
    expect(html).toContain("Show 2 more");
    const all = view({ read: ON(b), expanded: true });
    expect(all.match(/data-question="key_field"/g)).toHaveLength(7);
    expect(all).not.toContain("more</button>");
  });

  it("counts more than the read holds from the open count", () => {
    const b = body([KEY, PURPOSE], { open: 9 });
    expect(visibleQuestions(b, false)).toEqual({ cards: [KEY, PURPOSE], more: 7 });
  });
});

// ── Item 6: the batch ──────────────────────────────────────────────────────

describe("the batch of Jev's best guesses", () => {
  it("takes a purpose and a key field with a best guess, and never a slot, a stage or revenue", () => {
    const ids = batchIds([KEY, DATE, PURPOSE, SLOT, STAGE, REVENUE, base({ id: "key:X:date", choices: [] })]);
    expect(ids).toEqual([KEY.id, DATE.id, PURPOSE.id]);
    expect(batchShown(ids)).toBe(true);
    expect(batchShown([KEY.id])).toBe(false);
  });

  it("shows the button above the cards with 2 or more, and opens with the ids", () => {
    let opened: string[] | null = null;
    const html = view({ read: ON(body([KEY, PURPOSE])), onBatch: (ids) => (opened = ids) });
    expect(batchLabel(2)).toBe("Accept Jev's best guesses (2)");
    expect(html).toContain("Accept Jev&#x27;s best guesses (2)");
    expect(html.indexOf("best guesses")).toBeLessThan(html.indexOf('data-question="key_field"'));
    expect(view({ read: ON(body([KEY, SLOT])) })).not.toContain("best guesses");
    expect(opened).toBeNull();
  });

  it("lists the dry run, one line for each question", () => {
    const items = [
      { id: KEY.id, label: "Collection", part: "amount", value: "IP_Amount", value_label: "IP Amount", line: "Totals of Collection now use IP Amount." },
      { id: PURPOSE.id, label: "Visits", part: "purpose", value: "activity", value_label: "Activity", line: "Visits is now a activity module." },
    ];
    expect(batchLine(items[0])).toBe("Collection: amount, IP Amount");
    const html = renderToStaticMarkup(createElement(BatchBody, { items, failed: false }));
    expect(html).toContain("Collection: amount, IP Amount");
    expect(html).toContain("Visits: purpose, Activity");
    expect(html).toContain("Totals of Collection now use IP Amount.");
    expect(applyLabel(2)).toBe("Apply 2 answers");
    expect(renderToStaticMarkup(createElement(BatchBody, { items: null, failed: true }))).toContain("Try again");
  });

  it("dry runs through the read cache, applies the same ids, and undoes each module once", () => {
    const comp = read("./OpenQuestions.tsx");
    expect(comp).toContain("api.acceptBest(ids, true)");
    expect(comp).toContain("api.acceptBest(ids, false, plan)");
    expect(comp).toMatch(/<Modal open onClose=\{onClose\} title=\{BATCH_TITLE\}/);
    expect(
      batchUndoCalls([
        { module: "Payment_Received", part: "amount", value: AUTO },
        { module: "Payment_Received", part: "date", value: AUTO },
        { module: "Visits", part: "purpose", value: AUTO },
      ]),
    ).toEqual([
      { module: "Payment_Received", parts: { amount: AUTO, date: AUTO } },
      { module: "Visits", parts: { purpose: AUTO } },
    ]);
  });
});

// ── Item 4: the toast and Undo ─────────────────────────────────────────────

describe("an answer, its toast and its Undo", () => {
  const done = (over: Partial<AnswerDone>): AnswerDone => ({
    id: KEY.id,
    kind: "key_field",
    version: 42,
    line: null,
    undo: null,
    moved: null,
    slot: null,
    module: null,
    ...over,
  });

  it("says what changed", () => {
    expect(savedLine(KEY, done({ line: "Totals of Collection now use IP Amount." }), "IP_Amount")).toBe(
      "Saved. Totals of Collection now use IP Amount.",
    );
    expect(savedLine(STAGE, done({ kind: "stage_value", moved: 12 }), "won")).toBe(
      "Saved. 12 records leave Unsorted.",
    );
    expect(savedLine(REVENUE, done({ kind: "revenue" }), "payment_in")).toBe("Saved. Revenue is now Collected.");
    expect(savedLine(KEY, done({}), null)).toBe("Saved. No field holds the amount of Collection.");
  });

  it("puts back the choice of Metorite for an open question", () => {
    const u = undoOf(KEY, done({ undo: { module: "Payment_Received", part: "amount", value: AUTO } }));
    expect(u).toEqual({ kind: "put", module: "Payment_Received", parts: { amount: AUTO } });
    const r = undoOf(REVENUE, done({ undo: { module: "@org", part: "revenue", value: AUTO } }));
    expect(r).toEqual({ kind: "put", module: "@org", parts: { revenue: AUTO } });
  });

  it("puts back the earlier choice of the admin", () => {
    const corrected = { ...KEY, reason: "answered" as const, by: "admin", current: "Amount_Due" };
    expect(undoOf(corrected, done({ undo: { module: "Payment_Received", part: "amount", value: AUTO } }))).toEqual({
      kind: "put",
      module: "Payment_Received",
      parts: { amount: "Amount_Due" },
    });
  });

  it("sends @auto to the same id for a stage, and has none for a slot", () => {
    expect(undoOf(STAGE, done({ undo: { question: STAGE.id, value: AUTO, module: "Potentials" } }))).toEqual({
      kind: "answer",
      id: STAGE.id,
      value: AUTO,
    });
    expect(undoOf(SLOT, done({ kind: "slot" }))).toBeNull();
  });

  it("toasts each answer with Undo, and sends the version only for a stage", () => {
    const comp = read("./OpenQuestions.tsx");
    expect(comp).toContain('api.answerQuestion(q.id, value, q.kind === "stage_value" ? plan : null)');
    expect(comp).toMatch(/action: undo \? \{ label: UNDO_LABEL, onClick: \(\) => void runUndo\(key, undo\) \}/);
    expect(comp).toContain("useToast");
  });

  it("says what moved after a 409", () => {
    expect(questionError(new CrmError("x", 409, "question_moved"), "f")).toBe(QUESTION_MOVED);
    expect(questionError(new CrmError("x", 409, "plan_moved"), "f")).toBe(PLAN_MOVED);
    expect(questionError(new CrmError("That is not an answer.", 422, "bad_answer"), "f")).toBe(
      "That is not an answer.",
    );
    expect(questionError(new Error("boom"), "f")).toBe("f");
  });
});

// ── Item 3: the client, and invalidate("crm:") on each write ───────────────

describe("the client of the questions (item 3)", () => {
  type Sent = { url: string; method: string; body: unknown };
  const original = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = original;
    clearAll();
  });

  function stub(status = 200, answer: unknown = {}): Sent[] {
    const sent: Sent[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(status >= 400 ? { detail: { code: "question_moved", message: "x" } } : answer), {
        status,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    return sent;
  }

  it("reads the questions, with all and the limit", async () => {
    const sent = stub(200, body([]));
    await api.listQuestions({ limit: 200 });
    await api.listQuestions({ all: true, limit: 200 });
    expect(sent.map((s) => s.url)).toEqual([
      "/api/crm/questions?limit=200",
      "/api/crm/questions?all=1&limit=200",
    ]);
  });

  it("sends each write, and wakes every CRM read after it, also after a refusal", async () => {
    const sent = stub();
    const writes: Array<() => Promise<unknown>> = [
      () => api.answerQuestion("stage:deal:3f9a", "won", 41),
      () => api.answerQuestion(KEY.id, null),
      () => api.dismissQuestion(PURPOSE.id),
      () => api.acceptBest([KEY.id, PURPOSE.id], false, 41),
      () => api.putCatalogue("@org", { revenue: AUTO }),
    ];
    for (const write of writes) {
      put("crm:mode", { mode: "mirror" });
      await write();
      expect(peek("crm:mode")).toBeUndefined();
    }
    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([
      "POST /api/crm/questions/stage%3Adeal%3A3f9a/answer",
      "POST /api/crm/questions/key%3APayment_Received%3Aamount/answer",
      "POST /api/crm/questions/purpose%3AVisits/dismiss",
      "POST /api/crm/questions/accept-best",
      "PUT /api/crm/catalogue/%40org",
    ]);
    expect(sent[0].body).toEqual({ value: "won", version: 41 });
    expect(sent[1].body).toEqual({ value: null });
    expect(sent[3].body).toEqual({ ids: [KEY.id, PURPOSE.id], dry_run: false, version: 41 });
    expect(sent[4].body).toEqual({ revenue: AUTO });

    stub(409);
    put("crm:mode", { mode: "mirror" });
    await expect(api.answerQuestion(KEY.id, "IP_Amount")).rejects.toMatchObject({ code: "question_moved" });
    expect(peek("crm:mode")).toBeUndefined();
  });

  it("wakes nothing on a dry run, which writes nothing", async () => {
    const sent = stub(200, { dry_run: true, items: [], version: null, undo: [] });
    put("crm:mode", { mode: "mirror" });
    await api.acceptBest([KEY.id], true);
    expect(peek("crm:mode")).toBeDefined();
    expect(sent[0].body).toEqual({ ids: [KEY.id], dry_run: true });
  });
});

// ── Your answers (the corrections of CRM-Q31-4) ────────────────────────────

describe("Your answers", () => {
  const PURPOSE_AI = { ...PURPOSE, id: "purpose:Visits", reason: "answered" as const, by: "ai", current: "activity" };
  const AMOUNT_ADMIN = { ...KEY, reason: "answered" as const, by: "admin", current: "IP_Amount" };
  const DATE_NONE = { ...DATE, reason: "answered" as const, by: "admin", current: null };
  const REVENUE_ADMIN = { ...REVENUE, reason: "answered" as const, by: "admin", current: "payment_in" };

  it("groups the parts in use by module, purpose first, and revenue apart", () => {
    const groups = answerGroups([DATE_NONE, AMOUNT_ADMIN, PURPOSE_AI, REVENUE_ADMIN]);
    expect(groups.map((g) => g.label)).toEqual(["Collection", "Visits", "Revenue"]);
    expect(groups[0].parts.map((q) => q.part)).toEqual(["amount", "date"]);
  });

  it("says who chose each part, and names the answer in use", () => {
    expect(byLine(AMOUNT_ADMIN)).toBe("You chose");
    expect(byLine(PURPOSE_AI)).toBe("Metorite chose");
    expect(byLine({ ...KEY, by: "code", current: null })).toBe("Not set");
    expect(currentLabel(AMOUNT_ADMIN)).toBe("IP Amount");
    expect(currentLabel(DATE_NONE)).toBe("None");
    expect(currentLabel(REVENUE_ADMIN)).toBe("Collected");
  });

  it("offers Let Metorite choose, each option, and None for a key part", () => {
    const picks = answerPicks(AMOUNT_ADMIN);
    expect(picks[0]).toEqual({ value: AUTO, label: LET_METORITE });
    expect(picks.map((p) => p.label)).toContain("Grand Total");
    expect(picks.at(-1)).toEqual({ value: NONE_PICK, label: "None" });
    expect(answerPicks(PURPOSE_AI).some((p) => p.value === NONE_PICK)).toBe(false);
    expect(answerPicks(REVENUE_ADMIN).map((p) => p.label)).toEqual([LET_METORITE, "Billed", "Collected"]);
    expect(pickValue(AMOUNT_ADMIN)).toBe("IP_Amount");
    expect(pickValue(PURPOSE_AI)).toBe(AUTO);
    expect(pickValue(DATE_NONE)).toBe(NONE_PICK);
    expect(pickToValue(NONE_PICK)).toBeNull();
  });

  it("writes through PUT /crm/catalogue, and its Undo puts back the part before", () => {
    expect(correctionTarget(AMOUNT_ADMIN)).toEqual({ module: "Payment_Received", part: "amount" });
    expect(correctionTarget(REVENUE_ADMIN)).toEqual({ module: "@org", part: "revenue" });
    expect(correctionUndo(AMOUNT_ADMIN)).toBe("IP_Amount");
    expect(correctionUndo(PURPOSE_AI)).toBe(AUTO);
    expect(read("./OpenQuestions.tsx")).toContain(
      "await api.putCatalogue(target.module, { [target.part]: pickToValue(pick) });",
    );
  });

  it("draws one row for each module, under the Zoho labels, with no number of Jev", () => {
    const html = renderToStaticMarkup(
      createElement(YourAnswersView, {
        body: body([], { answered: [AMOUNT_ADMIN, PURPOSE_AI, REVENUE_ADMIN] }),
        failed: false,
        handlers: answersHandlers,
      }),
    );
    expect(html).toContain(">Collection<");
    expect(html).toContain(">Visits<");
    expect(html).toContain("You chose");
    expect(html).toContain("Metorite chose");
    expect(html).toContain('aria-label="Change the amount of Collection');
    expect(textOf(html)).not.toMatch(ENGINE_WORDS);
  });

  it("draws the answers tab in the section, and its error state", () => {
    const html = view({
      tab: "answers",
      answers: { body: null, failed: true, handlers: answersHandlers },
    });
    expect(html).toContain("Could not load your answers. Try again.");
    expect(html).not.toContain('data-question="key_field"');
  });
});

// ── Item 5: the count on the rail ──────────────────────────────────────────

describe("the count on the rail (item 5)", () => {
  const sections = visibleSections({ isAdmin: true, mode: "mirror", questions: "on" });

  it("shows the open count on the CRM settings row, in the attention tone", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "deals",
        onSelect: noop,
        settings: { sections, active: null, onSelect: noop, questions: 3 },
      }),
    );
    expect(html).toMatch(/CRM settings<\/span><span class="rail-row-count[^"]*" data-meta-tone="attention">3</);
  });

  it("shows it on the Open questions row while settings is open, and on no other row", () => {
    const html = renderToStaticMarkup(
      createElement(SettingsSectionRows, { sections, active: "setup", onSelect: noop, questions: 3 }),
    );
    expect(html).toMatch(/Open questions<\/span><span class="rail-row-count[^"]*" data-meta-tone="attention">3</);
    expect(html.match(/data-meta-tone=/g)).toHaveLength(1);
  });

  it("hides the count at 0 and for a member", () => {
    expect(railCount(0)).toBeNull();
    expect(railCount(4)).toBe(4);
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "settings",
        onSelect: noop,
        settings: { sections, active: "setup", onSelect: noop, questions: null },
      }),
    );
    expect(html).not.toContain("data-meta-tone");
  });
});
