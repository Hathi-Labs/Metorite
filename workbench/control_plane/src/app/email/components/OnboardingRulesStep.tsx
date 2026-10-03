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
 * - "Use the recommended rules" calls `installPresetRules` once. When it adds
 *   none and no rule is on, a line says to turn one on in AI Settings.
 * - "Choose my own" opens AI Settings, which opens on its Rules tab.
 * - Once an enabled rule exists, it offers "Sort my imported mail". The
 *   automatic run touches only mail that arrived after the first enabled rule
 *   (owner decision (d), #576), so the imported mail needs one run of
 *   "Process past emails". The action opens that dialog on the import range.
 *   The dialog counts the mail before it spends a model call.
 * - "Draft replies for me" shows only with an enabled reply rule, because a
 *   draft is an action on that rule. It shows the STORED value, read when the
 *   step is ready, and stays disabled until the read returns or when it fails
 *   (fix round 1, P1). Moving it saves `draft_replies` and nothing else.
 * - "Done", "Skip for now" and the "Skip setup" button send
 *   `onboarding_done: true`. The page writes the returned account into the
 *   store, so the setup never shows again.
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
  hasEnabledReplyRule,
  installRecommendedRules,
  installedLine,
  processPastFrom,
  readDraftSwitch,
  rulesStepPhase,
  setDraftReplies,
  type DraftSwitch,
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
  /** True after "Use the recommended rules" added none. */
  installedNone: boolean;
  busy: "install" | "finish" | null;
  error: string | null;
  /** True when an enabled reply rule exists, so drafting can act. */
  replyRule: boolean;
  draft: DraftSwitch;
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
  const draftOn = p.draft.state === "ready" && p.draft.on;
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
          <>
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
            {p.installedNone && <p className="mt-1.5 text-[11px] text-muted-foreground">{COPY.noneAdded}</p>}
          </>
        )}

        {ready && (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
              {p.pastFrom && (
                <Button variant="primary" size="sm" icon="History" disabled={locked} onClick={p.onProcessPast}>
                  {COPY.processPast}
                </Button>
              )}
              {p.replyRule && (
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <Toggle
                    enabled={draftOn}
                    disabled={p.draft.state !== "ready" || p.draftBusy || locked}
                    onChange={p.onDraftChange}
                  />
                  {COPY.draftLabel}
                </label>
              )}
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
            <p className="mt-1 text-[11px] text-muted-foreground">
              {!p.replyRule
                ? COPY.draftNeedsReplyRule
                : p.draft.state === "failed"
                  ? COPY.draftReadFailed
                  : COPY.draftNote}
            </p>
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
  account: Pick<EmailAccount, "id" | "importSince" | "importPhase" | "importCount">;
  /** Opens an automation view. `pastFrom` opens Process past emails on that date. */
  onOpenAutomation: (feature: AutomationFeature, pastFrom?: string | null) => void;
  /** Called with the account the PATCH returned. The page writes it to the store. */
  onFinished: (updated: EmailAccount) => void;
}) {
  // null: the read of the rules is in flight.
  const [rules, setRules] = useState<ReadonlyArray<Pick<AutomationRule, "enabled" | "name" | "system_type">> | null>(
    null,
  );
  const [installed, setInstalled] = useState(0);
  const [installTried, setInstallTried] = useState(false);
  const [busy, setBusy] = useState<"install" | "finish" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The stored value, read once the step is ready. Disabled until then.
  const [draft, setDraft] = useState<DraftSwitch>({ state: "loading" });
  const [draftBusy, setDraftBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  const phase = rulesStepPhase(rules);

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

  // The switch shows what is stored, on every mount: drafting can be ON from
  // AI Settings, or from an earlier visit to this step.
  useEffect(() => {
    if (phase !== "ready") return;
    let live = true;
    void readDraftSwitch(RULES_API, account.id).then((d) => live && setDraft(d));
    return () => {
      live = false;
    };
  }, [phase, account.id]);

  const recommended = async () => {
    setBusy("install");
    setError(null);
    try {
      const added = await installRecommendedRules(RULES_API, account.id);
      setInstalled(added.length);
      setInstallTried(true);
      // Re-read, so the step moves on only when an enabled rule exists. If
      // the re-read fails, the rules the install added stand in for it.
      setRules(
        await RULES_API.listRules(account.id).catch(() =>
          added.map((name) => ({ enabled: true, name, system_type: null })),
        ),
      );
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
      const updated = await RULES_API.finishOnboarding(account.id);
      setFinished(true);
      onFinished(updated);
    } catch {
      setError(COPY.failed);
      setBusy(null);
    }
  };

  const changeDraft = async (on: boolean) => {
    setDraftBusy(true);
    setError(null);
    try {
      await setDraftReplies(RULES_API, account.id, on);
      setDraft({ state: "ready", on });
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
      phase={phase}
      installed={installed}
      installedNone={installTried && installed === 0}
      busy={busy}
      error={error}
      replyRule={hasEnabledReplyRule(rules)}
      draft={draft}
      draftBusy={draftBusy}
      pastFrom={pastFrom}
      onRecommended={() => void recommended()}
      onChooseOwn={() => onOpenAutomation("ai-settings")}
      onSkip={() => void finish()}
      onProcessPast={() => onOpenAutomation("ai-settings", pastFrom)}
      onDraftChange={(on) => void changeDraft(on)}
      onInsights={() => onOpenAutomation("analytics")}
      onDone={() => void finish()}
    />
  );
}
