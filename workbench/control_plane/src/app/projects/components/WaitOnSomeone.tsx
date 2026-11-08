"use client";

/**
 * The form "Wait on someone" of the task card (WS-AP AP-9a,
 * `waiting_on_me.md` §6.2).
 *
 * Who does what, and what they see next: a member who waits on a person picks
 * the person, writes the need in 140 characters, may add 2 or 3 choices and a
 * day, and presses "Send". The card then shows "Waiting for Priya", and the
 * person gets a card in their own list. A refusal shows its text under the
 * form and keeps what the member wrote.
 *
 * The send goes through the ONE ask route (`decisionsApi.askPerson`). The
 * asker is the session. The form is offered only while both flags hold the
 * org: `DecisionPanel` draws it from the read, and draws nothing otherwise.
 * The pure rules and the refusal texts are in `lib/askPerson.ts`.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import { ProjectsApiError, decisionsApi } from "../lib/api";
import {
  ASK_COPY,
  CHOICES_LIMIT,
  CHOICE_MAX,
  EMPTY_DRAFT,
  QUESTION_MAX,
  askBody,
  askFailure,
  draftProblem,
} from "../lib/askPerson";
import { AssigneePicker } from "./AssigneePicker";

interface Props {
  taskId: string;
  /** The ask went out. The host reads the task again. */
  onSent: () => void;
  onCancel: () => void;
}

export function WaitOnSomeone({ taskId, onSent, onCancel }: Props) {
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  /** The text in the person box. It holds the address once a row is picked. */
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problem = draftProblem(draft);

  async function send() {
    if (problem || busy) return;
    setBusy(true);
    setError(null);
    try {
      await decisionsApi.askPerson(taskId, askBody(draft));
      onSent();
    } catch (err) {
      setError(
        err instanceof ProjectsApiError
          ? askFailure(err.status, err.detail)
          : askFailure(undefined, null),
      );
    } finally {
      setBusy(false);
    }
  }

  const today = new Date();
  const min = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  return (
    <form
      aria-label={ASK_COPY.heading}
      data-wait-form=""
      className="mt-3 space-y-3 border-t border-border pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <p className="text-sm font-medium text-foreground">{ASK_COPY.heading}</p>

      <div className="block text-xs text-muted-foreground">
        {ASK_COPY.who}
        <AssigneePicker
          peopleOnly
          value={typed}
          disabled={busy}
          ariaLabel="Who to ask"
          placeholder="name or email"
          onChange={(value) => {
            setTyped(value);
            setDraft((d) => ({ ...d, addressee: value }));
          }}
          onPick={(address) => {
            setTyped(address);
            setDraft((d) => ({ ...d, addressee: address }));
          }}
          onCommitText={() => {}}
          commitOnBlur={false}
        />
      </div>

      <label className="block text-xs text-muted-foreground">
        {ASK_COPY.need}
        <Input
          className="mt-1"
          value={draft.question}
          maxLength={QUESTION_MAX}
          disabled={busy}
          placeholder="What do you need from them?"
          onChange={(e) => setDraft((d) => ({ ...d, question: e.target.value }))}
        />
        <span className="mt-0.5 block text-[11px]">
          {`${draft.question.length} of ${QUESTION_MAX}. ${ASK_COPY.needHint}`}
        </span>
      </label>

      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="text-xs text-muted-foreground">{ASK_COPY.choices}</legend>
        {draft.choices.map((choice, i) => (
          <Input
            key={i}
            value={choice}
            maxLength={CHOICE_MAX}
            aria-label={`Choice ${i + 1}`}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                choices: d.choices.map((c, at) => (at === i ? e.target.value : c)),
              }))
            }
          />
        ))}
        {draft.choices.length < CHOICES_LIMIT ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            icon="Plus"
            onClick={() => setDraft((d) => ({ ...d, choices: [...d.choices, ""] }))}
          >
            {ASK_COPY.addChoice}
          </Button>
        ) : null}
      </fieldset>

      <label className="block text-xs text-muted-foreground">
        {ASK_COPY.due}
        <Input
          type="date"
          className="mt-1"
          value={draft.due}
          min={min}
          disabled={busy}
          onChange={(e) => setDraft((d) => ({ ...d, due: e.target.value }))}
        />
      </label>

      {error ? (
        <p role="alert" className="text-xs text-destructive" data-wait-error="">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
          {ASK_COPY.cancel}
        </Button>
        <Button
          type="submit"
          size="sm"
          loading={busy}
          disabled={busy || problem !== null}
          title={problem ?? undefined}
          data-wait-send=""
        >
          {ASK_COPY.send}
        </Button>
      </div>
    </form>
  );
}
