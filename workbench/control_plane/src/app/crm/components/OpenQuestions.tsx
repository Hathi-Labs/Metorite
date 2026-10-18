"use client";

/**
 * Settings → Open questions (WS-53 CRM-Q32-3, spec crm_platform.md §18.14.9).
 *
 * The CRM asks the admin each question that the AI (internally, Jev) could
 * not answer with confidence about the data of the org. Each answer becomes a fixed fact for
 * totals and chat, and the AI never asks it again.
 *
 * - **Open (<n>).** One card for each question, by impact, 5 at a time. A
 *   click applies at once, and the toast carries Undo. A `slot` card opens
 *   the panel of the suggestion (`SuggestionPanel`), which shows real
 *   records before it approves. "Accept the AI's suggestions" opens a dry run
 *   in a `Modal`, then applies it as one change.
 * - **Your answers.** The corrections of CRM-Q31-4: each part in use, with a
 *   `SelectButton` to change it, and "Let Metorite choose".
 * - **Proposed by your team** (CRM-Q32-8). A member who is not an admin can
 *   say in the chat what unclear data means. The card shows each proposal,
 *   "Proposed by <name> · <when>", with a one-click Confirm. Confirm on an
 *   open question is the answer route. Confirm on a part in use is the
 *   correction of "Your answers". The open tab sorts "Proposed first".
 *
 * `lib/questions.ts` holds every word and rule, and this file only draws
 * them. The views take props only, so a test reads their markup. No
 * confidence number and no engine word reaches the screen.
 */

import { EmptyState } from "@/components/EmptyState";
import RecommendedBadge from "@/components/RecommendedBadge";
import Tabs from "@/components/Tabs";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { SelectButton } from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";

import * as api from "../lib/api";
import {
  ANSWERS_KEY,
  ANSWERS_TAB,
  ANSWERS_UNREAD,
  ASKED_AGAIN_LINE,
  NONE_PICK,
  SORT_LABEL,
  SORT_OPTIONS,
  BATCH_NOTE,
  BATCH_TITLE,
  BATCH_UNREAD,
  CANCEL_LABEL,
  CHANGES_HEADING,
  DISMISSED_LINE,
  NOT_RELEVANT,
  NOT_RELEVANT_TIP,
  NO_ANSWERS,
  NOT_DISMISSED_LINE,
  NO_QUESTIONS,
  QUESTIONS_KEY,
  QUESTIONS_NOTE,
  QUESTIONS_TITLE,
  QUESTIONS_UNREAD,
  READ_LIMIT,
  TRY_AGAIN,
  UNDONE_LINE,
  UNDO_LABEL,
  WHY_HEADING,
  anyProposed,
  answerGroups,
  answerPicks,
  applyLabel,
  batchDoneLine,
  batchIds,
  batchLabel,
  batchLine,
  batchShown,
  batchUndoCalls,
  byLine,
  choiceView,
  correctionTarget,
  correctionUndo,
  currentLabel,
  moreLabel,
  noneLabel,
  notDismissed,
  openTabLabel,
  partWord,
  pickLabel,
  pickOptions,
  pickToValue,
  pickValue,
  proposalsOf,
  questionCopy,
  questionError,
  questionsGate,
  readQuestions,
  reasonBadge,
  recordsInLabel,
  savedLine,
  undoOf,
  visibleQuestions,
  type AcceptBestDone,
  type BestGuess,
  type ChoiceView,
  type Question,
  type QuestionChoice,
  type QuestionSort,
  type QuestionsBody,
  type QuestionsGate,
  type QuestionsRead,
  type UndoCall,
} from "../lib/questions";
import {
  AUTO,
  LET_METORITE,
  SUM_CHANGE,
  SUM_CHOICE_LABEL,
  SUM_CHOICE_LINE,
  SUM_PICK,
  isSum,
  sumOffered,
  type AnswerValue,
} from "../lib/questions";
import { SLOT_SLUG, type SlotWord, type Suggestion } from "../lib/suggestions";
import type { EntitySlug } from "../lib/types";
import { useSourceLabels } from "../lib/useCrmMode";
import { viewLabel } from "../lib/views";
import { Card } from "./SettingsCards";
import { SuggestionPanel, useSuggestionActions, useSuggestions } from "./Suggestions";
import { ProposalList } from "./ProposalList";
import { SumDialog } from "./SumPicker";

// One copy, shared with the picker of a sum (CRM-Q32-8).
export { ProposalList };

// ── The reads ──────────────────────────────────────────────────────────────

/**
 * The open questions, for an admin in Mirror mode. The page, the rail
 * count and the section share this ONE read (`QUESTIONS_KEY`). A 404
 * `questions_off` reads as `off`, and hides each of them.
 */
export function useQuestions(enabled: boolean): {
  read: QuestionsRead | undefined;
  gate: QuestionsGate;
  failed: boolean;
  refresh: () => void;
} {
  const res = useCachedResource<QuestionsRead>(enabled ? QUESTIONS_KEY : null, () =>
    readQuestions(() => api.listQuestions({ limit: READ_LIMIT })),
  );
  const failed = enabled && res.data === undefined && res.error !== null;
  return {
    read: enabled ? res.data : undefined,
    gate: enabled ? questionsGate(res.data, failed) : "off",
    failed,
    refresh: res.refresh,
  };
}

/** Each part in use (`all=1`), for "Your answers". Read only while that tab is open. */
function useAnswers(enabled: boolean): { body: QuestionsBody | null; failed: boolean; refresh: () => void } {
  const res = useCachedResource<QuestionsBody>(enabled ? ANSWERS_KEY : null, () =>
    api.listQuestions({ all: true, limit: READ_LIMIT }),
  );
  return {
    body: res.data ?? null,
    failed: enabled && res.data === undefined && res.error !== null,
    refresh: res.refresh,
  };
}

// ── One card ───────────────────────────────────────────────────────────────

/** What a card can do. The caller owns the requests. */
export type QuestionHandlers = {
  /** The id of the question whose request runs, `batch`, or `null`. */
  busy: string | null;
  /** One answer. `null` is "None of these". A list is a sum (CRM-Q32-9). */
  onAnswer: (q: Question, value: AnswerValue) => void;
  onDismiss: (q: Question) => void;
  /** A `slot` choice: answer it, then open the panel of the suggestion. */
  onSlot: (q: Question, choice: QuestionChoice) => void;
  /** "These fields add up to the amount": open the picker of a sum (CRM-Q32-9b). */
  onSum: (q: Question) => void;
};

/** The text of one choice: its label, the AI's mark, its lines and its examples. */
function ChoiceText({ choice, view }: { choice: QuestionChoice; view: ChoiceView }) {
  return (
    <>
      <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs font-medium text-foreground">
        <span className="min-w-0 break-words">{view.label}</span>
        {choice.best ? <RecommendedBadge /> : null}
      </span>
      {view.lines.map((line) => (
        <span key={line} className="text-[11px] text-muted-foreground">
          {line}
        </span>
      ))}
      {view.examples ? (
        // The examples wrap under the label, so a phone never scrolls sideways.
        <span className="min-w-0 break-words text-[11px] text-muted-foreground">{view.examples}</span>
      ) : null}
    </>
  );
}

/** The choices of a card: one under the other on a phone, three across from `sm`. */
function Choices({
  q,
  handlers,
  disabled,
}: {
  q: Question;
  handlers: QuestionHandlers;
  disabled: boolean;
}) {
  const sum = sumOffered(q);
  if (q.choices.length === 0 && !sum) return null;
  const compact = q.kind === "stage_value";
  return (
    <ul
      className={compact ? "flex flex-col gap-1.5 sm:flex-row sm:flex-wrap" : "grid gap-1.5 sm:grid-cols-3"}
      aria-label="Choices"
    >
      {q.choices.map((choice) => (
        <li
          key={choice.value}
          className={`min-w-0 rounded-md border ${choice.best ? "border-primary/40" : "border-border"}`}
        >
          <Button
            variant="ghost"
            size="none"
            layout="flex w-full min-w-0 flex-col items-start gap-0.5"
            className="px-3 py-2 text-left"
            disabled={disabled}
            loading={handlers.busy === q.id}
            onClick={() =>
              q.kind === "slot" ? handlers.onSlot(q, choice) : handlers.onAnswer(q, choice.value)
            }
          >
            <ChoiceText choice={choice} view={choiceView(q, choice)} />
          </Button>
        </li>
      ))}
      {sum ? (
        // CRM-Q32-9b: a choice of its own, a full row, that opens the picker of a sum.
        <li className="min-w-0 rounded-md border border-dashed border-border sm:col-span-3">
          <Button
            variant="ghost"
            size="none"
            layout="flex w-full min-w-0 flex-col items-start gap-0.5"
            className="px-3 py-2 text-left"
            disabled={disabled}
            onClick={() => handlers.onSum(q)}
          >
            <span className="text-xs font-medium text-foreground">{SUM_CHOICE_LABEL}</span>
            <span className="text-[11px] text-muted-foreground">{SUM_CHOICE_LINE}</span>
          </Button>
        </li>
      ) : null}
    </ul>
  );
}

/**
 * One question, as a person reads it (owner feedback, 2026-10-18): the
 * question as a full sentence, a badge, why we ask, what the answer
 * changes, the choices, and plain actions under them. No hook, so a test
 * reads its markup.
 */
export function QuestionCard({ q, handlers }: { q: Question; handlers: QuestionHandlers }) {
  const badge = reasonBadge(q.reason, q.kind);
  const disabled = handlers.busy !== null;
  const copy = questionCopy(q);
  const records = recordsInLabel(q);
  const options = q.other ? pickOptions(q) : [];
  return (
    <li className="space-y-2.5 rounded-lg border border-border p-3" data-question={q.kind}>
      <div className="space-y-1">
        <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
          <h3 className="min-w-0 flex-1 break-words text-sm font-medium text-foreground">{copy.question}</h3>
          {badge ? <Badge tone={badge.tone}>{badge.label}</Badge> : null}
        </div>
        {records ? <p className="text-[11px] text-muted-foreground">{records}</p> : null}
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <div className="min-w-0 space-y-0.5">
          <dt className="text-[11px] font-medium text-muted-foreground">{WHY_HEADING}</dt>
          <dd className="break-words text-xs text-foreground">{copy.why}</dd>
        </div>
        <div className="min-w-0 space-y-0.5">
          <dt className="text-[11px] font-medium text-muted-foreground">{CHANGES_HEADING}</dt>
          <dd className="break-words text-xs text-foreground">{copy.changes}</dd>
        </div>
      </dl>
      <Choices q={q} handlers={handlers} disabled={disabled} />
      <ProposalList q={q} disabled={disabled} onConfirm={(p) => handlers.onAnswer(q, p.value)} />
      {q.none || options.length > 0 || q.dismissible ? (
        <div className="flex flex-wrap items-center gap-2">
          {q.none ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => handlers.onAnswer(q, null)}
            >
              {noneLabel(q)}
            </Button>
          ) : null}
          {options.length > 0 ? (
            <SelectButton
              label={`${pickLabel(q)}. ${copy.question}`}
              prompt={pickLabel(q)}
              value=""
              options={options}
              // The prompt is a full phrase, so it keeps its width (visual review, 2026-10-18).
              widthClass="w-full sm:w-56"
              filterAbove={8}
              disabled={disabled}
              onChange={(value) => handlers.onAnswer(q, value)}
            />
          ) : null}
          {q.dismissible ? (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto"
              title={NOT_RELEVANT_TIP}
              disabled={disabled}
              onClick={() => handlers.onDismiss(q)}
            >
              {NOT_RELEVANT}
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

// ── Your answers ───────────────────────────────────────────────────────────

export type AnswersHandlers = {
  /** The id of the part whose change runs, or `null`. */
  busy: string | null;
  /** A pick of the `SelectButton` of a part. */
  onChange: (q: Question, pick: string) => void;
  onRetry: () => void;
  /** Open the picker of a sum for this part (CRM-Q32-9b). */
  onSum: (q: Question) => void;
};

/**
 * "Your answers": one row for each module, with its purpose and its key
 * parts under the Zoho labels. "You chose", "Metorite chose" or "Not set",
 * and a `SelectButton` to change each part. No hook.
 */
export function YourAnswersView({
  body,
  failed,
  handlers,
}: {
  body: QuestionsBody | null;
  failed: boolean;
  handlers: AnswersHandlers;
}) {
  if (body === null) {
    return failed ? (
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <p className="text-xs text-muted-foreground">{ANSWERS_UNREAD}</p>
        <Button variant="secondary" size="sm" onClick={handlers.onRetry}>
          {TRY_AGAIN}
        </Button>
      </div>
    ) : (
      <div className="space-y-2 px-4 py-3">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-4 w-40" />
      </div>
    );
  }
  const groups = answerGroups(body.answered);
  if (groups.length === 0) {
    return <p className="px-4 py-3 text-xs text-muted-foreground">{NO_ANSWERS}</p>;
  }
  return (
    <ul className="divide-y divide-border/60" aria-label={ANSWERS_TAB}>
      {groups.map((group) => (
        <li key={group.module} className="space-y-1.5 px-4 py-3">
          <h3 className="text-sm font-medium text-foreground">{group.label}</h3>
          <ul className="space-y-1.5">
            {group.parts.map((q) => {
              const part = q.kind === "revenue" ? "meaning" : partWord(q.part);
              return (
                <li key={q.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="w-20 shrink-0 text-xs capitalize text-muted-foreground">{part}</span>
                  <span className="min-w-0 flex-1 text-xs text-foreground">
                    {currentLabel(q)}
                    <span className="ml-2 text-[11px] text-muted-foreground">{byLine(q)}</span>
                  </span>
                  {isSum(q.current) ? (
                    // A sum: Change opens the same picker, and Metorite can choose again.
                    <span className="flex w-full flex-wrap gap-2 sm:w-auto">
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-label={`${SUM_CHANGE} the ${part} of ${group.label}`}
                        disabled={handlers.busy !== null}
                        onClick={() => handlers.onSum(q)}
                      >
                        {SUM_CHANGE}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={handlers.busy !== null}
                        onClick={() => handlers.onChange(q, AUTO)}
                      >
                        {LET_METORITE}
                      </Button>
                    </span>
                  ) : (
                    <SelectButton
                      label={`Change the ${part} of ${group.label}`}
                      value={pickValue(q)}
                      options={answerPicks(q)}
                      // A line of its own on a phone, so the answer in use keeps its room.
                      widthClass="w-full sm:w-48"
                      filterAbove={8}
                      disabled={handlers.busy !== null}
                      onChange={(pick) => (pick === SUM_PICK ? handlers.onSum(q) : handlers.onChange(q, pick))}
                    />
                  )}
                  {proposalsOf(q).length > 0 ? (
                    // A proposed change of a part in use: Confirm is the correction.
                    <div className="w-full">
                      <ProposalList
                        q={q}
                        disabled={handlers.busy !== null}
                        onConfirm={(p) => handlers.onChange(q, p.value ?? NONE_PICK)}
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ul>
  );
}

// ── The section ────────────────────────────────────────────────────────────

export type SectionTab = "open" | "answers";

/**
 * The section, drawn from the reads the caller holds: the tabs, the batch
 * button, the cards with their cap of 5, the empty state and the error
 * state. No hook, so a test reads its markup.
 */
export function OpenQuestionsView({
  read,
  failed,
  onRetry,
  tab,
  onTab,
  expanded,
  onExpand,
  handlers,
  onBatch,
  answers,
  sort = "impact",
  onSort,
}: {
  /** The read of `GET /crm/questions`, or `undefined` while it loads. */
  read: QuestionsRead | undefined;
  failed: boolean;
  onRetry: () => void;
  tab: SectionTab;
  onTab: (tab: SectionTab) => void;
  expanded: boolean;
  onExpand: () => void;
  handlers: QuestionHandlers;
  /** Open the batch Modal with these ids. */
  onBatch: (ids: string[]) => void;
  answers: { body: QuestionsBody | null; failed: boolean; handlers: AnswersHandlers };
  /** CRM-Q32-8: the order of the open tab. */
  sort?: QuestionSort;
  onSort?: (sort: QuestionSort) => void;
}) {
  if (read?.off) return null;
  const body = read?.body ?? null;
  const open = body?.open ?? 0;
  const ids = body ? batchIds(body.shown) : [];
  const { cards, more } = body ? visibleQuestions(body, expanded, sort) : { cards: [], more: 0 };
  // The sort shows only when a question holds a proposal: it orders nothing else.
  const sortable = body !== null && onSort !== undefined && anyProposed(body.shown);
  return (
    <Card title={QUESTIONS_TITLE} note={QUESTIONS_NOTE}>
      <div className="space-y-3 px-4 py-3">
        <Tabs
          tabs={[
            { id: "open", label: openTabLabel(open) },
            { id: "answers", label: ANSWERS_TAB },
          ]}
          activeTab={tab}
          onTabChange={(id) => onTab(id === "answers" ? "answers" : "open")}
          variant="segmented"
          chrome="inline"
        />
        {tab === "answers" ? null : body === null ? (
          failed ? (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs text-muted-foreground">{QUESTIONS_UNREAD}</p>
              <Button variant="secondary" size="sm" onClick={onRetry}>
                {TRY_AGAIN}
              </Button>
            </div>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          )
        ) : cards.length === 0 ? (
          <EmptyState icon="CircleCheck" tone="success" message={NO_QUESTIONS} />
        ) : (
          <>
            {batchShown(ids) || sortable ? (
              <div className="flex flex-wrap items-center gap-2">
                {batchShown(ids) ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    icon="ListChecks"
                    disabled={handlers.busy !== null}
                    onClick={() => onBatch(ids)}
                  >
                    {batchLabel(ids.length)}
                  </Button>
                ) : null}
                {sortable && onSort ? (
                  <SelectButton
                    label={SORT_LABEL}
                    value={sort}
                    options={SORT_OPTIONS}
                    widthClass="w-full sm:w-40"
                    onChange={(value) => onSort(value === "proposed" ? "proposed" : "impact")}
                  />
                ) : null}
              </div>
            ) : null}
            <ul className="space-y-2" aria-label={QUESTIONS_TITLE}>
              {cards.map((q) => (
                <QuestionCard key={q.id} q={q} handlers={handlers} />
              ))}
            </ul>
            {more > 0 ? (
              <Button variant="ghost" size="sm" icon="ChevronDown" onClick={onExpand}>
                {moreLabel(more)}
              </Button>
            ) : null}
          </>
        )}
      </div>
      {tab === "answers" ? (
        <div className="border-t border-border">
          <YourAnswersView body={answers.body} failed={answers.failed} handlers={answers.handlers} />
        </div>
      ) : null}
    </Card>
  );
}

// ── The batch ──────────────────────────────────────────────────────────────

/** The body of the batch Modal: the dry run, one line for each question. No hook. */
export function BatchBody({ items, failed }: { items: BestGuess[] | null; failed: boolean }) {
  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <p className="text-xs text-muted-foreground">{BATCH_NOTE}</p>
      {items ? (
        <ul className="space-y-1.5" aria-label="The AI's suggestions">
          {items.map((item) => (
            <li key={item.id} className="space-y-0.5">
              <p className="text-xs font-medium text-foreground">{batchLine(item)}</p>
              <p className="text-[11px] text-muted-foreground">{item.line}</p>
            </li>
          ))}
        </ul>
      ) : failed ? (
        <p className="text-xs text-muted-foreground">{BATCH_UNREAD}</p>
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
    </div>
  );
}

/**
 * The batch Modal. The dry run is a read, so it goes through the one read
 * cache (`useCachedResource`), keyed by the ids. "Apply <k> answers" sends
 * the same ids without the dry run.
 */
function BatchDialog({
  ids,
  busy,
  onApply,
  onClose,
}: {
  ids: string[];
  busy: boolean;
  onApply: () => void;
  onClose: () => void;
}) {
  const dry = useCachedResource<AcceptBestDone>(`crm:questions:best:${ids.join(",")}`, () =>
    api.acceptBest(ids, true),
  );
  const items = dry.data?.items ?? null;
  return (
    <Modal open onClose={onClose} title={BATCH_TITLE} icon="ListChecks" size="lg" closeLabel="Close">
      <BatchBody items={items} failed={items === null && dry.error !== null} />
      <div className="flex flex-wrap justify-end gap-2 border-t border-border p-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          {CANCEL_LABEL}
        </Button>
        <Button
          variant="primary"
          size="sm"
          icon="Check"
          loading={busy}
          disabled={busy || items === null || items.length === 0}
          onClick={onApply}
        >
          {applyLabel(items?.length ?? ids.length)}
        </Button>
      </div>
    </Modal>
  );
}

// ── The section, with its data ─────────────────────────────────────────────

/** Settings → Open questions. The page draws it for an admin in Mirror mode, while the gate is on. */
export default function OpenQuestionsCard() {
  const toast = useToast();
  const { read, failed, refresh } = useQuestions(true);
  const [tab, setTab] = useState<SectionTab>("open");
  const [expanded, setExpanded] = useState(false);
  const [sort, setSort] = useState<QuestionSort>("impact");
  const [busy, setBusy] = useState<string | null>(null);
  const [batch, setBatch] = useState<string[] | null>(null);
  // CRM-Q32-9b: the question whose picker of a sum is open, and where it came from.
  const [sum, setSum] = useState<{ q: Question; from: "card" | "answers" } | null>(null);
  const answers = useAnswers(tab === "answers");
  // The slot whose panel is open. The panel draws the suggestion of the
  // newest read, with the read of the click as a fallback (CRM-Q20-2).
  const [preview, setPreview] = useState<{ slot: SlotWord; found: Suggestion } | null>(null);
  const labels = useSourceLabels(true);
  const labelOf = (slug: EntitySlug) => viewLabel(slug, labels);
  const suggestions = useSuggestions(preview !== null);
  const suggestionActions = useSuggestionActions(labelOf);
  const previewing = preview
    ? (suggestions.list?.suggestions.find((s) => s.slot === preview.slot) ?? preview.found)
    : null;
  const plan = read && !read.off ? read.body.plan : null;

  function fail(key: string, err: unknown, fallback: string) {
    toast.show({ key, variant: "error", title: questionError(err, fallback) });
  }

  async function runUndo(key: string, call: UndoCall) {
    setBusy(key);
    try {
      if (call.kind === "put") await api.putCatalogue(call.module, call.parts);
      else if (call.kind === "undismiss") await api.undismissQuestion(call.id);
      else await api.answerQuestion(call.id, call.value);
      toast.show({ key, variant: "success", title: call.kind === "undismiss" ? ASKED_AGAIN_LINE : UNDONE_LINE });
    } catch (err) {
      // A dismissal that is gone already: nothing to undo, and no error.
      if (notDismissed(err)) toast.show({ key, variant: "success", title: NOT_DISMISSED_LINE });
      else fail(key, err, "Undo did not save.");
    } finally {
      setBusy(null);
    }
  }

  async function answer(q: Question, value: AnswerValue): Promise<boolean> {
    const key = `crm-question:${q.id}`;
    setBusy(q.id);
    try {
      // A stage answer commits the plan, so it names the version it read.
      const done = await api.answerQuestion(q.id, value, q.kind === "stage_value" ? plan : null);
      const undo = undoOf(q, done, value);
      toast.show({
        key,
        variant: "success",
        title: savedLine(q, done, value),
        action: undo ? { label: UNDO_LABEL, onClick: () => void runUndo(key, undo) } : undefined,
      });
      return true;
    } catch (err) {
      fail(key, err, "That answer did not save.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function dismiss(q: Question) {
    const key = `crm-question:${q.id}`;
    setBusy(q.id);
    try {
      await api.dismissQuestion(q.id);
      const undo: UndoCall = { kind: "undismiss", id: q.id };
      toast.show({
        key,
        variant: "success",
        title: DISMISSED_LINE,
        action: { label: UNDO_LABEL, onClick: () => void runUndo(key, undo) },
      });
    } catch (err) {
      fail(key, err, "That did not save.");
    } finally {
      setBusy(null);
    }
  }

  /**
   * A `slot` choice puts the module in the open draft, then opens the panel
   * of the suggestion. The panel shows real records first, and its one
   * action approves it, with the Undo of CRM-Q20-2.
   */
  async function slot(q: Question, choice: QuestionChoice) {
    const key = `crm-question:${q.id}`;
    const word = q.slot && q.slot in SLOT_SLUG ? (q.slot as SlotWord) : null;
    if (!word) return;
    setBusy(q.id);
    try {
      await api.answerQuestion(q.id, choice.value);
      const list = await api.listSuggestions();
      const found = list.suggestions.find((s) => s.slot === word);
      if (found) setPreview({ slot: word, found });
      else toast.show({ key, variant: "error", title: "The preview did not load. Try again." });
    } catch (err) {
      fail(key, err, "The preview did not load.");
    } finally {
      setBusy(null);
    }
  }

  async function applyBatch(ids: string[]) {
    const key = "crm-question:batch";
    setBusy("batch");
    try {
      const done = await api.acceptBest(ids, false, plan);
      const calls = batchUndoCalls(done.undo);
      toast.show({
        key,
        variant: "success",
        title: batchDoneLine(done.items.length),
        action:
          calls.length > 0
            ? {
                label: UNDO_LABEL,
                onClick: () =>
                  void (async () => {
                    setBusy("batch");
                    try {
                      for (const c of calls) await api.putCatalogue(c.module, c.parts);
                      toast.show({ key, variant: "success", title: UNDONE_LINE });
                    } catch (err) {
                      fail(key, err, "Undo did not save.");
                    } finally {
                      setBusy(null);
                    }
                  })(),
              }
            : undefined,
      });
      setBatch(null);
    } catch (err) {
      fail(key, err, "The answers did not save.");
    } finally {
      setBusy(null);
    }
  }

  async function correct(q: Question, pick: string) {
    if (pick === pickValue(q)) return;
    await correctTo(q, pickToValue(pick));
  }

  /** A correction to a value: a pick, or the list of a sum. Undo puts back the part before. */
  async function correctTo(q: Question, value: AnswerValue): Promise<boolean> {
    const target = correctionTarget(q);
    if (!target) return false;
    const key = `crm-answer:${q.id}`;
    const before = correctionUndo(q);
    setBusy(q.id);
    try {
      await api.putCatalogue(target.module, { [target.part]: value });
      toast.show({
        key,
        variant: "success",
        title: "Saved.",
        action: {
          label: UNDO_LABEL,
          onClick: () =>
            void runUndo(key, { kind: "put", module: target.module, parts: { [target.part]: before } }),
        },
      });
      return true;
    } catch (err) {
      fail(key, err, "That change did not save.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  /** Save the picker of a sum: an answer from a card, a correction from "Your answers". */
  /** Confirm of a proposal in the picker of a sum: one field, or none. */
  async function confirmInSum(value: string | null) {
    if (!sum) return;
    const done = sum.from === "card" ? await answer(sum.q, value) : await correctTo(sum.q, value);
    if (done) setSum(null);
  }

  async function saveSum(keys: string[]) {
    if (!sum) return;
    const done = sum.from === "card" ? await answer(sum.q, keys) : await correctTo(sum.q, keys);
    if (done) setSum(null);
  }

  return (
    <>
      <OpenQuestionsView
        read={read}
        failed={failed}
        onRetry={refresh}
        tab={tab}
        onTab={setTab}
        expanded={expanded}
        onExpand={() => setExpanded(true)}
        handlers={{
          busy,
          onAnswer: (q, value) => void answer(q, value),
          onDismiss: (q) => void dismiss(q),
          onSlot: (q, choice) => void slot(q, choice),
          onSum: (q) => setSum({ q, from: "card" }),
        }}
        onBatch={setBatch}
        sort={sort}
        onSort={setSort}
        answers={{
          body: answers.body,
          failed: answers.failed,
          handlers: {
            busy,
            onChange: (q, pick) => void correct(q, pick),
            onRetry: answers.refresh,
            onSum: (q) => setSum({ q, from: "answers" }),
          },
        }}
      />
      {batch ? (
        <BatchDialog
          ids={batch}
          busy={busy === "batch"}
          onClose={() => setBatch(null)}
          onApply={() => void applyBatch(batch)}
        />
      ) : null}
      {sum ? (
        <SumDialog
          q={sum.q}
          busy={busy === sum.q.id}
          onClose={() => setSum(null)}
          onSave={(keys) => void saveSum(keys)}
          onConfirm={(p) => void confirmInSum(p.value)}
        />
      ) : null}
      {previewing && preview ? (
        <SuggestionPanel
          suggestion={previewing}
          listLabel={labelOf(SLOT_SLUG[preview.slot])}
          busy={suggestionActions.busy === preview.slot}
          onClose={() => setPreview(null)}
          onUse={() => {
            void suggestionActions.apply(previewing).then((done) => done && setPreview(null));
          }}
        />
      ) : null}
    </>
  );
}
