"use client";

/**
 * The rules step of the guided setup (WS-17 EM-T6d items 8 to 11, D-EM-15).
 *
 * It draws at the top of the mail pane, under the import panel's place, once
 * `onboardingStage(account)` is `rules`. It is not a modal, so the member can
 * read mail while it is open.
 *
 * Two parts, because vitest here runs in the node environment:
 * - `RulesStepView` is pure. A test draws it with `createElement`.
 * - `OnboardingRulesStep` makes the calls through `RulesStepApi`. The
 *   decisions it calls live in `lib/onboarding.ts`.
 *
 * What it does:
 * - "Use the recommended rules" calls `installPresetRules` once.
 * - "Choose my own" opens AI Settings, which opens on its Rules tab.
 * - Once an enabled rule exists, it offers "Sort my imported mail". The
 *   automatic run touches only mail that arrived after the first enabled rule
 *   (owner decision (d), #576), so the imported mail needs one run of
 *   "Process past emails". The action opens that dialog on the import range.
 *   The dialog counts the mail before it spends a model call.
 * - "Draft replies for me" opens OFF (D-EM-6). Moving it reads the assistant
 *   settings and saves them with `draft_replies` changed and nothing else.
 * - "Done", "Skip for now" and the "Skip setup" button send
 *   `onboarding_done: true`. The setup then never shows again.
 * - It names no model and offers no model choice (EM-T5b).
 */

import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  getAssistantSettings,
  installPresetRules,
  listRules,
  saveAssistantSettings,
  updateEmailAccount,
} from "../lib/api";
import {
  RULES_STEP_COPY as COPY,
  installRecommendedRules,
  installedLine,
  processPastFrom,
  rulesStepPhase,
  setDraftReplies,
  type RulesStepApi,
  type RulesStepPhase,
} from "../lib/onboarding";
import type { AutomationFeature, AutomationRule, EmailAccount } from "../lib/types";
import { Toggle } from "./automation/ui";

/** The live calls. `finishOnboarding` is the PATCH of item 11. */
const RULES_API: RulesStepApi = {
  listRules,
  installPresetRules,
  getAssistantSettings,
  saveAssistantSettings,
  finishOnboarding: (accountId) => updateEmailAccount(accountId, { onboardingDone: true }),
};

export interface RulesStepViewProps {
  phase: RulesStepPhase;
  /** How many presets "Use the recommended rules" added. */
  installed: number;
  busy: "install" | "finish" | null;
  error: string | null;
  draftOn: boolean;
  draftBusy: boolean;
  /** The start of the import range, or null when nothing was imported. */
  pastFrom: string | null;
  onRecommended: () => void;
  onChooseOwn: () => void;
  onSkip: () => void;
  onProcessPast: () => void;
  onDraftChange: (on: boolean) => void;
  onInsights: () => void;
  onDone: () => void;
}

export function RulesStepView(p: RulesStepViewProps) {
  const ready = p.phase === "ready";
  const locked = p.busy !== null;
  const readyBody = [installedLine(p.installed), p.pastFrom ? COPY.readyBody : COPY.readyBodyNoImport]
    .filter(Boolean)
    .join(" ");
  return (
    <section
      aria-label="Mailbox setup"
      aria-busy={p.phase === "checking" || undefined}
      className="flex items-start gap-3 border-b border-border bg-card px-3 py-2.5 flex-shrink-0"
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon name={ready ? "CheckCircle2" : "Sparkles"} size={14} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-foreground">{ready ? COPY.readyTitle : COPY.title}</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{ready ? readyBody : COPY.body}</p>

        {p.phase === "choose" && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              icon="Sparkles"
              loading={p.busy === "install"}
              disabled={locked}
              onClick={p.onRecommended}
            >
              {COPY.recommended}
            </Button>
            <Button variant="secondary" size="sm" disabled={locked} onClick={p.onChooseOwn}>
              {COPY.chooseOwn}
            </Button>
            <Button variant="ghost" size="sm" disabled={locked} onClick={p.onSkip}>
              {COPY.skipForNow}
            </Button>
          </div>
        )}

        {ready && (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
              {p.pastFrom && (
                <Button variant="primary" size="sm" icon="History" disabled={locked} onClick={p.onProcessPast}>
                  {COPY.processPast}
                </Button>
              )}
              <label className="flex items-center gap-2 text-xs text-foreground">
                <Toggle enabled={p.draftOn} disabled={p.draftBusy || locked} onChange={p.onDraftChange} />
                {COPY.draftLabel}
              </label>
              <Button variant="text" size="sm" disabled={locked} onClick={p.onInsights}>
                {COPY.insights}
              </Button>
              <Button
                variant={p.pastFrom ? "secondary" : "primary"}
                size="sm"
                loading={p.busy === "finish"}
                disabled={locked}
                onClick={p.onDone}
              >
                {COPY.done}
              </Button>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">{COPY.draftNote}</p>
          </>
        )}

        {p.error && (
          <p role="alert" className="mt-1.5 text-[11px] text-destructive">
            {p.error}
          </p>
        )}
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="X"
        aria-label={COPY.skipSetup}
        title={COPY.skipSetup}
        disabled={locked}
        onClick={p.onSkip}
      />
    </section>
  );
}

export function OnboardingRulesStep({
  account,
  onOpenAutomation,
  onFinished,
}: {
  account: Pick<EmailAccount, "id" | "importSince">;
  /** Opens an automation view. `pastFrom` opens Process past emails on that date. */
  onOpenAutomation: (feature: AutomationFeature, pastFrom?: string | null) => void;
  /** Called after the PATCH, so the page re-reads the accounts. */
  onFinished: () => void;
}) {
  // null: the read of the rules is in flight.
  const [rules, setRules] = useState<ReadonlyArray<Pick<AutomationRule, "enabled">> | null>(null);
  const [installed, setInstalled] = useState(0);
  const [busy, setBusy] = useState<"install" | "finish" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // OFF when the step opens (D-EM-6). Nothing is written until it moves.
  const [draftOn, setDraftOn] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [finished, setFinished] = useState(false);

  // A rule may exist already: the member can make one during the import, or
  // come back from "Choose my own". A failed read shows the choices.
  useEffect(() => {
    let live = true;
    RULES_API.listRules(account.id)
      .then((r) => live && setRules(r))
      .catch(() => live && setRules([]));
    return () => {
      live = false;
    };
  }, [account.id]);

  const recommended = async () => {
    setBusy("install");
    setError(null);
    try {
      setInstalled(await installRecommendedRules(RULES_API, account.id));
      // Re-read, so the step moves on only when an enabled rule exists.
      setRules(await RULES_API.listRules(account.id).catch(() => [{ enabled: true }]));
    } catch {
      setError(COPY.failed);
    } finally {
      setBusy(null);
    }
  };

  const finish = async () => {
    setBusy("finish");
    setError(null);
    try {
      await RULES_API.finishOnboarding(account.id);
      setFinished(true);
      onFinished();
    } catch {
      setError(COPY.failed);
      setBusy(null);
    }
  };

  const draft = async (on: boolean) => {
    setDraftBusy(true);
    setError(null);
    try {
      await setDraftReplies(RULES_API, account.id, on);
      setDraftOn(on);
    } catch {
      setError(COPY.failed);
    } finally {
      setDraftBusy(false);
    }
  };

  if (finished) return null;

  const pastFrom = processPastFrom(account, new Date());
  return (
    <RulesStepView
      phase={rulesStepPhase(rules)}
      installed={installed}
      busy={busy}
      error={error}
      draftOn={draftOn}
      draftBusy={draftBusy}
      pastFrom={pastFrom}
      onRecommended={() => void recommended()}
      onChooseOwn={() => onOpenAutomation("ai-settings")}
      onSkip={() => void finish()}
      onProcessPast={() => onOpenAutomation("ai-settings", pastFrom)}
      onDraftChange={(on) => void draft(on)}
      onInsights={() => onOpenAutomation("analytics")}
      onDone={() => void finish()}
    />
  );
}
