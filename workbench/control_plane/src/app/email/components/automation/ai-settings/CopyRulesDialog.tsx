"use client";

// "Copy rules from..." on the Rules tab (multi-inbox UI, item 2). The member
// picks another mailbox, reads the rules that will copy and the ones that stay
// behind, and then agrees. The copy is an act the member starts (D-EM-24,
// §11.0 rule 5). It uses the guard of the onboarding step, so a double click
// starts one copy and a pair copies once. Fence: `copyRulesPreview.test.ts`.

import { useEffect, useMemo, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";
import { copyRules, listRules } from "../../../lib/api";
import {
  COPY_STEP,
  copyReport,
  copyRulesPreview,
  ruleCopier,
  rulesStepCopyChoices,
  type CopyReport,
} from "../../../lib/mailboxSettings";
import type { AutomationRule, EmailAccount } from "../../../lib/types";

type Source = Pick<EmailAccount, "id" | "emailAddress" | "displayLabel" | "provider">;

export function CopyRulesDialog({
  targetId,
  mailboxes,
  onClose,
  onCopied,
}: {
  /** The mailbox of the Rules tab. The rules copy into it. */
  targetId: string;
  /** Each mailbox of the member. The target is not a source. */
  mailboxes: ReadonlyArray<Source>;
  onClose: () => void;
  /** Called once a copy landed, so the tab reads its rules again. */
  onCopied: () => void;
}) {
  const choices = useMemo(() => rulesStepCopyChoices(mailboxes, targetId), [mailboxes, targetId]);
  const [sourceId, setSourceId] = useState<string>(choices[0]?.id ?? "");
  const [sourceRules, setSourceRules] = useState<AutomationRule[] | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  // The rules of the target. The preview leaves out a rule that it holds already.
  const [targetRules, setTargetRules] = useState<AutomationRule[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<CopyReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The names the target holds. The copy sends them as `skip_names`.
  const skipRef = useRef<string[]>([]);
  const [copier] = useState(() =>
    ruleCopier((from, to) => copyRules(from, to, skipRef.current)),
  );

  const source = choices.find((c) => c.id === sourceId);

  // The preview reads the rules of the source. It writes nothing.
  useEffect(() => {
    if (!sourceId) return;
    let live = true;
    listRules(sourceId)
      .then((r) => {
        if (live) {
          setSourceRules(r);
          setReadFailed(false);
        }
      })
      .catch(() => {
        if (live) setReadFailed(true);
      });
    return () => {
      live = false;
    };
  }, [sourceId]);

  // The same read for the target. A copy ran in another dialog is in it too.
  useEffect(() => {
    let live = true;
    listRules(targetId)
      .then((r) => {
        if (live) setTargetRules(r);
      })
      .catch(() => {
        if (live) setReadFailed(true);
      });
    return () => {
      live = false;
    };
  }, [targetId]);

  const targetAccount = mailboxes.find((m) => m.id === targetId);
  const preview =
    source && sourceRules && targetRules
      ? copyRulesPreview(sourceRules, source.label, {
          rules: targetRules,
          ownAddresses: mailboxes.map((m) => m.emailAddress),
          provider: targetAccount?.provider ?? "",
        })
      : null;
  skipRef.current = preview?.alreadyHere ?? [];
  const done = copier.copied(sourceId, targetId);

  const pick = (id: string) => {
    setSourceId(id);
    setSourceRules(null);
    setReadFailed(false);
    setReport(null);
    setError(null);
  };

  const run = () => {
    if (!source) return;
    const started = copier.start(sourceId, targetId);
    if (!started) return;
    setBusy(true);
    setError(null);
    started
      .then((result) => {
        setReport(copyReport(result, source.label));
        onCopied();
      })
      .catch(() => setError(COPY_STEP.failed))
      .finally(() => setBusy(false));
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Copy rules from another mailbox"
      description="Rules stay in one mailbox. This adds a copy here and changes nothing in the other mailbox."
      size="lg"
    >
      <div className="space-y-4 p-4">
        {choices.length === 0 ? (
          <p className="text-sm text-muted-foreground">Connect a second mailbox to copy its rules.</p>
        ) : (
          <>
            <SelectButton
              label="Copy rules from"
              value={sourceId}
              options={choices.map((c) => ({ value: c.id, label: c.label }))}
              onChange={pick}
              widthClass="w-full"
            />
            {readFailed && (
              <p role="alert" className="text-xs text-destructive">
                Metorite could not read the rules of {source?.label}. Try again.
              </p>
            )}
            {!preview && !readFailed && <p className="text-xs text-muted-foreground">Reading the rules…</p>}
            {preview && (
              <div className="space-y-3 text-sm">
                <p className="font-medium text-foreground">{preview.question}</p>
                {preview.willCopy.length > 0 && (
                  <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
                    {preview.willCopy.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                )}
                {preview.leftOut.length > 0 && (
                  <div className="space-y-0.5 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Metorite will not copy</p>
                    {preview.leftOut.map((r) => (
                      <p key={r.name}>
                        {r.name}. {r.why}
                      </p>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">
                  A reply rule keeps its name, and a mailbox keeps one reply rule. A copy of a pair
                  runs once in this window.
                </p>
              </div>
            )}
            {error && (
              <p role="alert" className="text-xs text-destructive">
                {error}
              </p>
            )}
            {report && (
              <div role="status" className="space-y-1 text-xs text-muted-foreground">
                <p className="text-foreground">{report.summary}</p>
                {report.lines.map((line, i) => (
                  <p key={i}>{line}</p>
                ))}
              </div>
            )}
          </>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {report ? "Close" : "Cancel"}
          </Button>
          {choices.length > 0 && (
            <Button onClick={run} disabled={busy || done || !preview || preview.willCopy.length === 0}>
              {busy ? "Copying…" : COPY_STEP.copyFrom(source?.label ?? "")}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
