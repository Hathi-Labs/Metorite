"use client";

// The "Fix / Improve Rules" flow — a per-email correction dialog shared by the
// Test and History tabs. For an existing rule / "None" it persists a learned
// classification pattern (and re-runs the email); for "New rule" it hands the
// correction to the AI chat. Extracted from AISettingsView.tsx.

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { listRules, runRuleOnMessage, submitRuleFeedback } from "../../../lib/api";
import type { AutomationRule, RunMessageResult } from "../../../lib/types";
import { useEmailStore } from "../../../lib/emailStore";
import { Modal } from "../ui";
import { currentMatchChip } from "./actionFormat";
import { INPUT_CLS } from "./common";

type Expected = "none" | "new" | { id: string; name: string };

function buildFixPrompt(
  expected: Expected,
  explanation: string,
  email: { subject: string; from: string },
): string {
  const ctx = `For the email "${email.subject || "(no subject)"}" from ${
    email.from || "unknown sender"
  }, `;
  const why = explanation.trim() ? ` because ${explanation.trim()}` : ".";
  if (expected === "new")
    return `${ctx}create a new rule for emails like this${why}`;
  if (expected === "none")
    return `${ctx}this email shouldn't have matched any rule${why}`;
  return `${ctx}this email should have matched the "${expected.name}" rule${why}`;
}

/**
 * The "Improve Rules" dialog. For an existing rule / "None" it PERSISTS a
 * learned classification pattern (so the same sender matches/skips that rule
 * next time — inbox-zero parity). For "New rule" it hands off to the AI chat.
 *
 * Exported so ANY surface that shows a categorization (mailbox row context
 * menu, the Cleaner's sender rows) can open the same correction flow — one
 * dialog, one learning path, reachable where the mistake is seen.
 */
export function FixDialog({
  accountId,
  email,
  current,
  messageId,
  onReran,
  onClose,
}: {
  accountId: string;
  email: { subject: string; from: string };
  current: {
    matched: boolean;
    ruleName: string | null;
    ruleId?: string | null;
    mailGone?: boolean;
  };
  messageId?: string | null;
  onReran?: () => void;
  onClose: () => void;
}) {
  const chip = currentMatchChip(
    current.matched,
    current.ruleName,
    current.mailGone === true,
  );
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [expected, setExpected] = useState<Expected | null>(null);
  const [explanation, setExplanation] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The sender is taught by default so an existing-rule / "None" correction is
  // applied DIRECTLY (no chat round-trip): for cleanup rules it learns a
  // sender→rule pattern; for conversation rules (Reply/FYI/…) the backend
  // sets the thread status instead (a sender pin there is wrong + overridden).
  // Uncheck "from <sender>" to fix just this email without learning the sender.
  const [useSender, setUseSender] = useState(true);
  // Pinning is opt-in. A pin skips the classifier for one sender, so making it
  // the default meant a correction never improved the AI — it removed the
  // sender from the AI's reach and left the same misunderstanding in place for
  // everyone else.
  const [pinSender, setPinSender] = useState(false);
  const [useSubject, setUseSubject] = useState(false);
  const [subjectKw, setSubjectKw] = useState("");
  const setPendingChatPrompt = useEmailStore((s) => s.setPendingChatPrompt);

  useEffect(() => {
    listRules(accountId).then(setRules).catch(() => {});
  }, [accountId]);

  // Seed the subject-keyword box with the email subject the first time the
  // user opts into subject matching, so they can trim it down to a keyword.
  const enableSubject = () => {
    setUseSubject((on) => {
      if (!on && !subjectKw) setSubjectKw(email.subject || "");
      return !on;
    });
  };

  const senderVal = expected && expected !== "new" && useSender ? email.from : "";
  const subjectVal =
    expected && expected !== "new" && useSubject ? subjectKw.trim() : "";
  // Only a brand-new rule needs the assistant; existing-rule / "None"
  // corrections are applied directly and re-classified immediately.
  const willChat = expected === "new";

  const submit = async () => {
    if (!expected) return;
    if (willChat) {
      setPendingChatPrompt(buildFixPrompt(expected, explanation, email));
      onClose();
      return;
    }
    // Apply the correction directly (persist a pattern for cleanup rules, or set
    // the thread status for conversation rules) and re-classify this email.
    setBusy(true);
    setError(null);
    try {
      const matchedIds = current.ruleId ? [current.ruleId] : [];
      const res = await submitRuleFeedback({
        accountId,
        sender: senderVal,
        expected: expected === "none" ? "none" : expected.id,
        matchedRuleIds: matchedIds,
        explanation,
        // The explanation IS the lesson. It goes into the classifier's prompt
        // for this rule, so the same judgement improves for every sender rather
        // than only for the one being corrected.
        guidance: explanation.trim() || undefined,
        pinSender,
        messageId: messageId || undefined,
        subjectKeyword: subjectVal || undefined,
      });
      // Conversation-status fix: the thread's status + label were set directly
      // (a learned sender pattern would be overridden for these rules) — no
      // re-run needed.
      if (expected !== "none" && res?.status_correction?.ok) {
        setDone(
          `Done — moved this thread to "${
            res.status_correction.label || expected.name
          }".`,
        );
        onReran?.();
        return;
      }
      // Nothing was persisted. The backend refuses some corrections on purpose
      // (a conversation rule can't be sender-pinned, and with no resolvable
      // thread there is nowhere to put the status), and it used to report those
      // as "Learned" anyway — so the user got a success message, nothing
      // changed, and they repeated the same correction forever. Say what
      // actually happened instead.
      if (!res?.created) {
        setError(
          messageId
            ? "Nothing was saved — this correction can't be learned for this rule."
            : "Couldn't apply this correction here — open the email and fix it from there.",
        );
        return;
      }
      // Cleanup-rule / "None" correction → re-run THIS email so the new
      // label/action applies immediately now that the pattern is learned.
      let reran: RunMessageResult | null = null;
      if (messageId) {
        try {
          reran = await runRuleOnMessage({
            accountId,
            messageId,
            isTest: false,
          });
        } catch {
          /* the learning is saved; a re-run failure shouldn't block the fix */
        }
      }
      const target = [
        senderVal && `from ${senderVal}`,
        subjectVal && `about "${subjectVal}"`,
      ]
        .filter(Boolean)
        .join(" / ") || "like this";
      const rerunNote = !reran
        ? ""
        : reran.applied && reran.rule
          ? ` Re-ran on this email → applied "${reran.rule.name}".`
          : reran.matched
            ? " Re-ran on this email."
            : " Re-ran on this email → no rule matched now.";
      // H6: the correction now strips the wrong label off THIS email, not just
      // teaches future mail. Say so, so the user sees the chip actually changed.
      const removed = res?.label_correction?.removed ?? [];
      const stripNote = removed.length
        ? ` Removed "${removed.join('", "')}" from this email.`
        : "";
      setDone(
        (expected === "none"
          ? `Got it — emails ${target} won't match ${
              current.ruleName || "that rule"
            } anymore.`
          : `Learned — emails ${target} will now match "${expected.name}".`) +
          stripNote +
          rerunNote,
      );
      onReran?.();
    } catch (e) {
      setError((e as Error).message || "Couldn't save the correction.");
    } finally {
      setBusy(false);
    }
  };

  const expectedLabel =
    expected === "new"
      ? "✨ New rule"
      : expected === "none"
        ? "❌ None"
        : expected
          ? expected.name
          : "";

  return (
    <Modal
      title="Improve Rules"
      onClose={onClose}
      footer={
        done ? (
          <Button size="none" layout="" onClick={onClose} className="ml-auto px-3 py-1.5 text-xs">
            Done
          </Button>
        ) : expected ? (
          <>
            <button
              onClick={() => setExpected(null)}
              disabled={busy}
              className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors disabled:opacity-50"
            >
              Back
            </button>
            <Button layout="flex items-center" onClick={submit} disabled={busy}>
              {busy ? (
                <Icon name="Loader2" className="animate-spin" size={13} />
              ) : willChat ? (
                <Icon name="MessageCircle" size={13} />
              ) : (
                <Icon name="Check" size={13} />
              )}
              {willChat ? "Send to assistant" : "Apply correction"}
            </Button>
          </>
        ) : undefined
      }
    >
      <div>
        <div className="text-[11px] text-muted-foreground mb-1">Matched:</div>
        <span
          className={`inline-block text-[10px] px-1.5 py-0.5 rounded-md ${chip.cls}`}
        >
          {chip.label}
        </span>
      </div>

      {done ? (
        <div className="text-xs text-emerald-400 bg-emerald-500/10 rounded-md px-2.5 py-2">
          {done}
        </div>
      ) : !expected ? (
        <div>
          <div className="text-xs font-medium text-foreground mb-2">
            Which rule did you expect it to match?
          </div>
          <div className="space-y-1.5">
            <FixOption onClick={() => setExpected("none")}>❌ None</FixOption>
            <FixOption onClick={() => setExpected("new")}>✨ New rule</FixOption>
            {rules.map((r) => (
              <FixOption
                key={r.id}
                onClick={() => setExpected({ id: r.id!, name: r.name })}
              >
                {r.name}
              </FixOption>
            ))}
            {rules.length === 0 && (
              <div className="text-[11px] text-muted-foreground">
                You haven&apos;t created any rules yet.
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-xs text-foreground">
            Selected rule:{" "}
            <span className="text-primary">{expectedLabel}</span>
          </div>
          {/* Free-text explanation is the primary input (inbox-zero style): just
              describe the fix and the assistant adjusts your rules. */}
          <textarea
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
            rows={3}
            placeholder={
              expected === "new"
                ? "Describe the rule you want for emails like this…"
                : expected === "none"
                  ? "What should the assistant learn? e.g. \u201cMail from our own domain is never a cold email.\u201d"
                  : `What should the assistant learn? e.g. \u201cVendor product digests belong in ${expectedLabel}.\u201d`
            }
            className={`${INPUT_CLS} resize-none`}
          />
          {expected !== "new" && expected !== "none" && (
            <label className="flex items-start gap-2 text-xs text-foreground cursor-pointer rounded-lg border border-border p-2.5">
              <input
                type="checkbox"
                checked={pinSender}
                onChange={() => setPinSender((v) => !v)}
                className="accent-primary mt-0.5"
              />
              <span className="min-w-0">
                <span className="block">
                  Always file mail from this sender here
                </span>
                <span className="block text-[10px] text-muted-foreground leading-snug">
                  Skips the AI entirely for this one sender — instant and free,
                  but it stops adapting, so it suits newsletters and bulk mail
                  rather than people.
                </span>
              </span>
            </label>
          )}
          {expected !== "new" && (
            <details className="rounded-lg border border-border p-2.5">
              <summary className="text-[11px] font-medium text-foreground cursor-pointer select-none">
                Fine-tune what&apos;s pinned (sender / subject)
              </summary>
              <div className="space-y-2 mt-2">
                <label className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useSender}
                    onChange={() => setUseSender((v) => !v)}
                    className="accent-primary"
                  />
                  from{" "}
                  <span className="text-muted-foreground truncate">
                    {email.from || "this sender"}
                  </span>
                </label>
                <label className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useSubject}
                    onChange={enableSubject}
                    className="accent-primary"
                  />
                  with subject containing…
                </label>
                {useSubject && (
                  <input
                    value={subjectKw}
                    onChange={(e) => setSubjectKw(e.target.value)}
                    placeholder="keyword in the subject (e.g. invoice)"
                    className={`${INPUT_CLS} ml-6 w-[calc(100%-1.5rem)]`}
                  />
                )}
              </div>
            </details>
          )}
          <p className="text-[10px] text-muted-foreground">
            {willChat
              ? "The assistant will refine your rules from your explanation."
              : pinSender
                ? "Teaches the AI, and pins this sender so their mail skips it."
                : "Teaches the AI — it will apply this to every sender, not just this one."}
          </p>
          {error && (
            <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2 py-1.5">
              {error}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function FixOption({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left text-xs px-3 py-2 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
    >
      {children}
    </button>
  );
}

/** The "Fix" button that opens the Improve Rules dialog for one email. */
export function FixButton({
  accountId,
  email,
  current,
  messageId,
  onReran,
}: {
  accountId: string;
  email: { subject: string; from: string };
  current: {
    matched: boolean;
    ruleName: string | null;
    ruleId?: string | null;
    mailGone?: boolean;
  };
  messageId?: string | null;
  onReran?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Tell the assistant this was wrong"
        className="flex items-center gap-1 px-1.5 py-1 rounded-md text-[11px] text-muted-foreground border border-border hover:text-foreground hover:bg-secondary transition-colors"
      >
        <Icon name="MessageCircle" size={12} /> Fix
      </button>
      {open && (
        <FixDialog
          accountId={accountId}
          email={email}
          current={current}
          messageId={messageId}
          onReran={onReran}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
