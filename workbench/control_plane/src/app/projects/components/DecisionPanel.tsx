"use client";

/**
 * The decision block of the task panel (WS-ALN ALN-2b,
 * `project_alignment.md` §3.2).
 *
 * Who does what, and what they see next:
 * - The DECIDER opens the task from the My Day card (or the bell). The block
 *   shows the question, its two to four options and when it closes. The
 *   decider picks an option, may add a reason, and presses "Send answer".
 *   The block then shows the answer, and the card leaves My Day.
 * - Another member who can open the task sees that a decision waits, on whom,
 *   and until when. Never the question, the options or the answer.
 * - An expired decision reads as expired, with the next step.
 *
 * The answer goes through the ONE answer route (`decisionsApi.answer`), the
 * route the chat tool `answer_decision` calls. While `DECISIONS_ORGS` does
 * not hold the org, every route answers 404 and this block draws nothing.
 *
 * It lives in `TaskBody`, so both hosts (Projects and My Tasks) show it.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";

import { ProjectsApiError, type TaskRow, decisionsApi } from "../lib/api";
import {
  DECISION_COPY,
  REASON_MAX,
  type DecisionRow,
  type TaskDecisions,
  answerFailure,
  closesText,
  expiredLine,
  hasDecisionBlock,
  labelOf,
  rowsToShow,
  stateNow,
  waitingLine,
} from "../lib/decisions";
import { labelWith } from "../lib/grouping";

interface Props {
  task: TaskRow;
  personLabels?: ReadonlyMap<string, string>;
  /** A write added a comment row. The host reads its discussion again. */
  onActivity?: () => void;
}

export function DecisionPanel({ task, personLabels, onActivity }: Props) {
  /** `null` until the first read, and when the routes are off. */
  const [data, setData] = useState<TaskDecisions | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = useCallback(async () => {
    try {
      setData(await decisionsApi.forTask(task.id));
    } catch {
      // A failed read must not blank the task. The block stays away.
      setData(null);
    }
  }, [task.id]);

  useEffect(() => {
    // Reset FIRST, so the next task never shows the last task's decision.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setData(null);
    setError(null);
    void read();
  }, [read]);

  // The read is a fresh clock for each render of a new `data`.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNowMs(Date.now());
  }, [data]);

  if (!data || !hasDecisionBlock(data, nowMs)) return null;
  const nameOf = labelWith(personLabels);
  const shown = rowsToShow(data.decisions, nowMs);

  return (
    <section
      aria-label={DECISION_COPY.heading}
      data-decision-panel=""
      className="@container mb-3 space-y-3 rounded-lg border border-border bg-card p-3"
    >
      {shown.map((row) => (
        <DecisionRowView
          key={row.id}
          row={row}
          state={stateNow(row, nowMs)}
          nameOf={nameOf}
          onAnswered={() => {
            setError(null);
            onActivity?.();
            void read();
          }}
          onFailed={(text, reload) => {
            setError(text);
            if (reload) void read();
          }}
        />
      ))}
      {data.waiting_on.map((w, i) => (
        <p
          key={`${w.decider}:${i}`}
          className="flex items-start gap-2 text-sm text-muted-foreground"
          data-decision-waiting=""
        >
          <Icon name="Hourglass" size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0">{waitingLine(w, nameOf)}</span>
        </p>
      ))}
      {error ? (
        <p role="alert" className="text-xs text-destructive" data-decision-error="">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function DecisionRowView({
  row,
  state,
  nameOf,
  onAnswered,
  onFailed,
}: {
  row: DecisionRow;
  state: "open" | "answered" | "expired";
  nameOf: (email: string) => string;
  onAnswered: () => void;
  onFailed: (text: string, reload: boolean) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    if (!picked) return;
    setBusy(true);
    try {
      await decisionsApi.answer(row.id, picked, reason);
      onAnswered();
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      const message = err instanceof Error ? err.message : undefined;
      const failed = answerFailure(status, message);
      onFailed(failed.text, failed.reload);
    } finally {
      setBusy(false);
    }
  }

  const heading = (
    <p className="flex min-w-0 items-start gap-2 text-sm font-medium text-foreground">
      <Icon name="Scale" size={14} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 break-words" data-decision-question="">
        {row.question}
      </span>
    </p>
  );

  if (state === "answered") {
    return (
      <div className="space-y-1" data-decision-state="answered">
        {heading}
        <p className="pl-6 text-xs text-muted-foreground">
          {`You answered: ${labelOf(row, row.answer_key)}.`}
          {row.answer_reason ? ` ${row.answer_reason}` : ""}
        </p>
      </div>
    );
  }

  if (state === "expired") {
    return (
      <div className="space-y-1" data-decision-state="expired">
        {heading}
        <p className="pl-6 text-xs text-muted-foreground">
          {closesText(row.expires_at, "Expired")}. {expiredLine(row, nameOf)}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2" data-decision-state="open">
      {heading}
      <p className="pl-6 text-xs text-muted-foreground">
        {`${row.asked_by ? `${nameOf(row.asked_by)} asks you to decide. ` : ""}${closesText(row.expires_at)}.`}
      </p>
      <div
        role="radiogroup"
        aria-label="Options"
        className="flex flex-wrap gap-2 pl-6"
        data-decision-options=""
      >
        {row.options.map((o) => (
          <Button
            key={o.key}
            role="radio"
            aria-checked={picked === o.key}
            variant="secondary"
            size="sm"
            selected={picked === o.key}
            disabled={busy}
            data-decision-option={o.key}
            onClick={() => setPicked(o.key)}
          >
            {o.label}
          </Button>
        ))}
      </div>
      {picked ? (
        <div className="space-y-2 pl-6">
          <Textarea
            rows={2}
            maxLength={REASON_MAX}
            value={reason}
            disabled={busy}
            aria-label={DECISION_COPY.reasonLabel}
            placeholder={DECISION_COPY.reasonPlaceholder}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button size="sm" loading={busy} disabled={busy} data-decision-send="" onClick={() => void send()}>
              {DECISION_COPY.send}
            </Button>
            <span className="min-w-0 text-xs text-muted-foreground">{DECISION_COPY.finalHint}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
