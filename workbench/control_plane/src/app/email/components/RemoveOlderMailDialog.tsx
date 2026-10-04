"use client";

/**
 * "Remove older mail from Metorite" (WS-17 EM-T6e, scope item 6, D1 to D5).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e". The
 * routes are EM-T6c: the preview `GET …/storage/older` and the removal
 * `POST …/storage/remove-older`.
 *
 * ⚠️ **Metorite's copy ONLY (D-EM-14).** The dialog says so before any
 * choice, and no word of it says that Metorite deletes mail at the provider.
 *
 * Built on `Modal` with `Button`s, as `MailboxEditDialog.tsx` is, because
 * `ConfirmDialog` cannot disable its confirm (D5). Two parts, because vitest
 * here runs in the node environment and a portal draws nothing there:
 * - `RemoveOlderMailView` is pure. A test draws it with `createElement`.
 * - `RemoveOlderMailDialog` holds the state in `removalReducer` of
 *   `lib/storage.ts` and makes the calls. Every decision lives there.
 *
 * The flow:
 * 1. The member keeps the newest 1, 2, 3 or 6 months, or picks a date. Each
 *    pick runs the preview of its `before`.
 * 2. The confirm stays disabled until the preview of the CURRENT choice
 *    answers with one message or more (A8). A late answer for an earlier
 *    choice changes nothing.
 * 3. The confirm sends the `before` of that preview (A9), for the id of the
 *    mailbox that the dialog names, never the selected mailbox (D3).
 * 4. A 409 shows the detail of the gateway, and the choice stays (A11).
 * 5. A 502, a 504 or a network error starts the follow-up of D1: the dialog
 *    reads the preview every 5 seconds, confirms at 0 messages, and gives up
 *    after 3 minutes with words that claim no failure (A13).
 * 6. On success the page writes the new meter into the store and reads the
 *    accounts again (A14).
 *
 * ⚠️ The calls run from the click, never from an effect. React runs an effect
 * twice in development, and a removal is not a call to send twice. Only the
 * follow-up uses an effect, for its timer, and a preview is safe to repeat.
 */

import { useEffect, useId, useReducer, useRef, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { previewOlderMail, removeOlderMail } from "../lib/api";
import { mailboxLabel } from "../lib/mailbox";
import {
  FOLLOW_UP_POLL_MS,
  KEEP_NEWEST_MONTHS,
  STORAGE_COPY,
  beforeOfDate,
  confirmBefore,
  dateInputValue,
  initialRemovalState,
  keepNewestBefore,
  meterSentence,
  monthsLabel,
  previewLine,
  providerUnchanged,
  removalFailure,
  removalReducer,
  removedLine,
  type RemovalChoice,
  type RemovalState,
} from "../lib/storage";
import type { EmailAccount, RemoveOlderResult } from "../lib/types";
import { MailboxChip } from "./MailboxChip";

export type RemovalAccount = Pick<
  EmailAccount,
  "id" | "emailAddress" | "displayLabel" | "colorSlot" | "provider"
>;

/** What the preview line says for the state of the dialog. */
export function previewText(state: RemovalState, locale?: string): string {
  const c = state.choice;
  if (!c) return STORAGE_COPY.chooseFirst;
  if (c.kind === "date" && !c.before) return c.date ? STORAGE_COPY.dateInvalid : STORAGE_COPY.chooseDay;
  const p = state.preview;
  if (p.state === "ready") return previewLine(p.answer, locale);
  if (p.state === "failed") return STORAGE_COPY.previewFailed;
  return STORAGE_COPY.counting;
}

export interface RemoveOlderMailViewProps {
  account: RemovalAccount;
  /** True with two or more mailboxes: the chip, the address and the label. */
  named: boolean;
  state: RemovalState;
  /** Today as `YYYY-MM-DD`, the `max` of the date field. */
  dateMax: string;
  locale?: string;
  onPickMonths: (months: number) => void;
  onPickDate: () => void;
  onDateChange: (value: string) => void;
  onConfirm: () => void;
  onClose: () => void;
}

export function RemoveOlderMailView(p: RemoveOlderMailViewProps) {
  const groupId = useId();
  const dateId = useId();
  const { state } = p;
  const name = p.named ? mailboxLabel(p.account) : null;
  const choosing = state.phase === "choose" || state.phase === "removing";
  const locked = state.phase !== "choose";
  const sendable = confirmBefore(state.preview, state.choice?.before ?? null) !== null;
  return (
    <div className="space-y-3 p-4">
      {p.named && (
        <div className="flex min-w-0 items-center gap-2 rounded-md bg-muted/50 px-2 py-1.5">
          <MailboxChip account={p.account} />
          <span className="truncate text-[11px] text-muted-foreground">{p.account.emailAddress}</span>
        </div>
      )}

      {choosing ? (
        <>
          <div className="space-y-1">
            <p className="text-xs text-foreground">
              {STORAGE_COPY.metoriteOnly} {providerUnchanged(p.account.provider)}
            </p>
            <p className="text-[11px] text-muted-foreground">{STORAGE_COPY.kept}</p>
          </div>

          <div className="space-y-1.5">
            <p id={groupId} className="text-xs font-medium text-muted-foreground">
              {STORAGE_COPY.keepNewest}
            </p>
            {/* A radio group of `Button`s, the pattern of ImportRangeStep (D5). */}
            <div role="radiogroup" aria-labelledby={groupId} className="flex flex-wrap gap-1.5">
              {KEEP_NEWEST_MONTHS.map((m) => {
                const on = state.choice?.kind === "months" && state.choice.months === m;
                return (
                  <Button
                    key={m}
                    variant="secondary"
                    size="sm"
                    role="radio"
                    aria-checked={on}
                    selected={on}
                    disabled={locked}
                    onClick={() => p.onPickMonths(m)}
                  >
                    {monthsLabel(m)}
                  </Button>
                );
              })}
              <Button
                variant="secondary"
                size="sm"
                role="radio"
                aria-checked={state.choice?.kind === "date"}
                selected={state.choice?.kind === "date"}
                disabled={locked}
                onClick={p.onPickDate}
              >
                {STORAGE_COPY.pickDate}
              </Button>
            </div>
          </div>

          {state.choice?.kind === "date" && (
            <div className="space-y-1">
              <label htmlFor={dateId} className="text-xs font-medium text-muted-foreground">
                {STORAGE_COPY.dateLabel}
              </label>
              <Input
                id={dateId}
                type="date"
                max={p.dateMax}
                value={state.choice.date}
                disabled={locked}
                onChange={(e) => p.onDateChange(e.target.value)}
              />
            </div>
          )}

          <p role="status" aria-live="polite" className="text-xs font-medium text-foreground">
            {state.phase === "removing" ? STORAGE_COPY.removing : previewText(state, p.locale)}
          </p>
          <p className="text-[11px] text-muted-foreground">{STORAGE_COPY.loadOlderNote}</p>

          {state.message && (
            <p role="alert" className="text-xs text-destructive">
              {state.message}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" disabled={locked} onClick={p.onClose}>
              {STORAGE_COPY.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              loading={state.phase === "removing"}
              disabled={!sendable || locked}
              onClick={p.onConfirm}
            >
              {STORAGE_COPY.confirm}
            </Button>
          </div>
        </>
      ) : (
        <>
          <div role="status" aria-live="polite" className="flex items-start gap-2">
            <Icon
              name={state.phase === "following" ? "Loader2" : state.phase === "done" ? "CheckCircle2" : "Info"}
              size={14}
              className={`mt-0.5 flex-shrink-0 ${
                state.phase === "following"
                  ? "animate-spin text-muted-foreground"
                  : state.phase === "done"
                    ? "text-success"
                    : "text-muted-foreground"
              }`}
              aria-hidden
            />
            <div className="min-w-0 space-y-1">
              {state.phase === "following" && <p className="text-xs text-foreground">{STORAGE_COPY.following}</p>}
              {state.phase === "unconfirmed" && <p className="text-xs text-foreground">{STORAGE_COPY.unconfirmed}</p>}
              {state.phase === "done" && (
                <>
                  <p className="text-xs text-foreground">
                    {state.removed !== null ? removedLine(state.removed, p.locale) : STORAGE_COPY.confirmedBefore}
                  </p>
                  {state.meter && (
                    <p className="text-[11px] text-muted-foreground">
                      {meterSentence(state.meter.stored, state.meter.limit, name, { now: true, locale: p.locale })}
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
          <div className="flex justify-end pt-1">
            <Button type="button" variant="secondary" onClick={p.onClose}>
              {STORAGE_COPY.close}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

type MeterRead = Pick<EmailAccount, "storedBytes" | "storageLimitBytes"> | null;

interface DialogProps {
  /** The mailbox that the notice or the step names. `null` closes the dialog. */
  account: RemovalAccount | null;
  named: boolean;
  onClose: () => void;
  /** After a removal answered: the page writes the meter of the answer into
   *  the store, then reads the accounts again (A14). */
  onRemoved: (accountId: string, result: RemoveOlderResult) => void;
  /** Reads the accounts again, and gives back this mailbox, or null. After a
   *  failure, after the follow-up confirms, and on a close after D1. */
  onRefresh: (accountId: string) => Promise<MeterRead>;
}

export function RemoveOlderMailDialog({ account, ...rest }: DialogProps) {
  if (!account) return null;
  // Keyed on the id: a DIFFERENT mailbox mounts a fresh dialog with no choice.
  return <RemoveOlderMailForm key={account.id} account={account} {...rest} />;
}

function RemoveOlderMailForm({ account, named, onClose, onRemoved, onRefresh }: DialogProps & { account: RemovalAccount }) {
  const [state, dispatch] = useReducer(removalReducer, undefined, initialRemovalState);
  const [dateMax] = useState(() => dateInputValue(new Date()));
  // One removal at a time, also for a double click before the next render.
  const sending = useRef(false);
  // The page passes a new function at each render. The follow-up timer must
  // not restart for that, so it reads the latest one through a ref.
  const refresh = useRef(onRefresh);
  useEffect(() => {
    refresh.current = onRefresh;
  }, [onRefresh]);
  const id = account.id;

  const runPreview = (before: string) => {
    previewOlderMail(id, before).then(
      (answer) => dispatch({ type: "previewAnswered", key: before, answer }),
      () => dispatch({ type: "previewAnswered", key: before, answer: null }),
    );
  };

  const pick = (choice: RemovalChoice) => {
    if (state.phase !== "choose") return;
    dispatch({ type: "pick", choice });
    if (choice.before) runPreview(choice.before);
  };

  const confirm = () => {
    const before = confirmBefore(state.preview, state.choice?.before ?? null);
    if (!before || state.phase !== "choose" || sending.current) return;
    sending.current = true;
    const choiceBefore = state.choice?.before ?? null;
    dispatch({ type: "confirm", before });
    removeOlderMail(id, before).then(
      (result) => {
        sending.current = false;
        onRemoved(id, result);
        dispatch({ type: "removed", result });
      },
      (error: unknown) => {
        sending.current = false;
        const failure = removalFailure(error);
        dispatch({ type: "removeFailed", failure, now: Date.now() });
        if (failure.kind === "failed") {
          // A failure surfaces, and the list is read again: some mail can be
          // gone, so the meter and the count can have changed.
          void refresh.current(id);
          if (choiceBefore) runPreview(choiceBefore);
        }
      },
    );
  };

  // D1: follow a removal that outlived the proxy, through the preview of the
  // same `before`. Each answer bumps `polls`, which arms the next read.
  const followBefore = state.phase === "following" ? state.before : null;
  useEffect(() => {
    if (!followBefore) return;
    const timer = setTimeout(() => {
      previewOlderMail(id, followBefore).then(
        (answer) => {
          dispatch({ type: "followAnswered", answer, now: Date.now() });
          if (!(answer.messages > 0)) {
            void refresh.current(id).then((acct) => dispatch({ type: "meterRead", account: acct }));
          }
        },
        () => dispatch({ type: "followAnswered", answer: null, now: Date.now() }),
      );
    }, FOLLOW_UP_POLL_MS);
    return () => clearTimeout(timer);
  }, [id, followBefore, state.polls]);

  const close = () => {
    if (state.phase === "removing") return;
    // A removal that the dialog could not confirm can still end later, so the
    // list is read again on the way out.
    if (state.phase === "following" || state.phase === "unconfirmed") void refresh.current(id);
    onClose();
  };

  return (
    <Modal
      open
      onClose={close}
      title={STORAGE_COPY.dialogTitle}
      description={named ? undefined : account.emailAddress}
      icon="HardDrive"
      size="md"
    >
      <RemoveOlderMailView
        account={account}
        named={named}
        state={state}
        dateMax={dateMax}
        onPickMonths={(months) => pick({ kind: "months", months, before: keepNewestBefore(months, new Date()) })}
        onPickDate={() => {
          // A second click keeps the date that the member typed.
          if (state.choice?.kind !== "date") pick({ kind: "date", date: "", before: null });
        }}
        onDateChange={(value) => pick({ kind: "date", date: value, before: beforeOfDate(value, new Date()) })}
        onConfirm={confirm}
        onClose={close}
      />
    </Modal>
  );
}
