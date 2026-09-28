"use client";

/**
 * AddAgentWizard
 *
 * Multi-step onboarding flow shown when a user selects a new agent.
 * Steps:
 *   0. Overview       — Agent card: name, description, what integrations it needs
 *   1. GitHub         — Device flow (always mandatory — used to clone the repo)
 *   2+. Mandatory     — One card per mandatory integration
 *   3+. Optional      — One card per optional integration (each skippable)
 *   Final. Done       — Success state; "Start chatting" button
 *
 * Props:
 *   agent      — the selected AgentEntry (from /api/agent/list)
 *   onComplete — called when setup is done; parent creates the session
 *   onCancel   — called if user dismisses before completing
 */

import Button from "@/components/ui/Button";
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEntry } from "@/app/api/agent/list/route";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import GitHubDeviceConnect from "@/components/GitHubDeviceConnect";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CardState {
  values: Record<string, string>;
  saving: boolean;
  testing: boolean;
  testResult: { ok: boolean; detail: string } | null;
  saved: boolean;
  skipped: boolean;
  error: string | null;
}

type WizardStep =
  | { kind: "overview" }
  | { kind: "github"; integration: IntegrationStatus }
  | { kind: "mandatory"; integration: IntegrationStatus; index: number; total: number }
  | { kind: "optional"; integration: IntegrationStatus; index: number; total: number }
  | { kind: "done" };

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function ProgressBar({ current, total }: { current: number; total: number }) {
  const pct = total <= 1 ? 100 : Math.round(((current - 1) / (total - 1)) * 100);
  return (
    <div className="w-full bg-secondary rounded-full h-1">
      <div
        className="bg-primary h-1 rounded-full transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Integration card (plain credentials form)
// ---------------------------------------------------------------------------

function IntegrationFormCard({
  integration,
  state,
  optional,
  onValueChange,
  onSave,
  onTest,
  onSkip,
}: {
  integration: IntegrationStatus;
  state: CardState;
  optional: boolean;
  onValueChange: (key: string, value: string) => void;
  onSave: () => void;
  onTest: () => void;
  onSkip?: () => void;
}) {
  const allFilled = integration.env_vars.every((v) => state.values[v.key]?.trim());

  return (
    <div
      className={`rounded-xl border p-6 space-y-4 ${
        state.saved
          ? "border-emerald-500 bg-emerald-500/5"
          : state.skipped
          ? "border-border bg-card/40 opacity-60"
          : "border-border bg-card"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-foreground">{integration.label}</span>
            {optional && !state.saved && !state.skipped && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">
                Optional
              </span>
            )}
            {state.saved && (
              <span className="text-xs text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">
                ✓ Configured
              </span>
            )}
            {state.skipped && (
              <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
                Skipped
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{integration.description}</p>
        </div>
        {integration.setup_url && !state.saved && !state.skipped && (
          <a
            href={integration.setup_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs px-3 py-1.5 rounded-lg border border-primary/40 text-primary hover:opacity-90/10 transition-colors shrink-0"
          >
            Get credentials →
          </a>
        )}
      </div>

      {/* Instructions */}
      {!state.saved && !state.skipped && integration.instructions && (
        <pre className="text-xs text-muted-foreground bg-secondary/60 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">
          {integration.instructions}
        </pre>
      )}

      {/* Inputs */}
      {!state.saved && !state.skipped && (
        <div className="space-y-3">
          {integration.env_vars.map((v) => (
            <div key={v.key}>
              <label className="block text-xs text-muted-foreground mb-1">
                {v.label}
                <span className="ml-2 font-mono text-muted-foreground">({v.key})</span>
              </label>
              <input
                type={v.sensitive ? "password" : "text"}
                autoComplete="off"
                spellCheck={false}
                value={state.values[v.key] ?? ""}
                onChange={(e) => onValueChange(v.key, e.target.value)}
                placeholder={`Enter ${v.label}…`}
                className="w-full px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-ring transition-colors"
              />
            </div>
          ))}
        </div>
      )}

      {/* Error / test result */}
      {state.error && (
        <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
          {state.error}
        </div>
      )}
      {state.testResult && (
        <div
          className={`text-sm rounded-lg px-3 py-2 ${
            state.testResult.ok
              ? "text-emerald-400 bg-emerald-500/10"
              : "text-warning bg-warning/10"
          }`}
        >
          {state.testResult.ok ? "✓" : "✗"} {state.testResult.detail}
        </div>
      )}

      {/* Actions */}
      {!state.saved && !state.skipped && (
        <div className="flex gap-3 pt-1">
          <Button size="none" layout="" onClick={onSave} disabled={!allFilled || state.saving} className="flex-1 py-2 text-sm">
            {state.saving ? "Saving…" : "Save & continue"}
          </Button>
          <button
            onClick={onTest}
            disabled={!allFilled || state.testing}
            className="px-4 py-2 rounded-lg border border-border hover:bg-secondary disabled:opacity-40 text-sm text-foreground transition-colors"
          >
            {state.testing ? "Testing…" : "Test"}
          </button>
          {optional && onSkip && (
            <button
              onClick={onSkip}
              className="px-4 py-2 rounded-lg border border-border hover:bg-secondary text-sm text-muted-foreground transition-colors"
            >
              Skip
            </button>
          )}
        </div>
      )}

      {/* Re-test after saved */}
      {state.saved && (
        <button
          onClick={onTest}
          disabled={state.testing}
          className="px-4 py-2 rounded-lg border border-border hover:bg-secondary text-sm text-muted-foreground transition-colors"
        >
          {state.testing ? "Testing…" : "Re-test connection"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard
// ---------------------------------------------------------------------------

interface AddAgentWizardProps {
  agent: AgentEntry;
  onComplete: () => void;
  onCancel: () => void;
}

export default function AddAgentWizard({
  agent,
  onComplete,
  onCancel,
}: AddAgentWizardProps) {
  const [integrations, setIntegrations] = useState<IntegrationStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [cardStates, setCardStates] = useState<Record<string, CardState>>({});
  const [stepIndex, setStepIndex] = useState(0);
  const hasFetched = useRef(false);

  // Build step list from integrations
  const steps = React.useMemo<WizardStep[]>(() => {
    if (integrations.length === 0) return [{ kind: "overview" }];
    const githubIntg = integrations.find((i) => i.service === "github");
    const mandatory = integrations.filter(
      (i) => i.service !== "github" && i.mandatory && !i.configured
    );
    const optional = integrations.filter(
      (i) => i.service !== "github" && !i.mandatory && !i.configured
    );
    const list: WizardStep[] = [{ kind: "overview" }];
    if (githubIntg && !githubIntg.configured) {
      list.push({ kind: "github", integration: githubIntg });
    }
    mandatory.forEach((intg, idx) =>
      list.push({ kind: "mandatory", integration: intg, index: idx + 1, total: mandatory.length })
    );
    optional.forEach((intg, idx) =>
      list.push({ kind: "optional", integration: intg, index: idx + 1, total: optional.length })
    );
    list.push({ kind: "done" });
    return list;
  }, [integrations]);

  const currentStep = steps[stepIndex] ?? { kind: "overview" };
  const totalSteps = steps.length;

  // Fetch integration status on mount
  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await fetch(
        `/api/integrations/status?agent=${encodeURIComponent(agent.name)}`
      );
      const data: IntegrationStatus[] = await res.json();
      if (!res.ok) {
        setFetchError(String((data as any).error ?? `HTTP ${res.status}`));
        return;
      }
      setIntegrations(data);
      // Init card states for all unconfigured integrations
      setCardStates((prev) => {
        const next = { ...prev };
        for (const intg of data) {
          if (!next[intg.service]) {
            next[intg.service] = {
              values: Object.fromEntries(intg.env_vars.map((v) => [v.key, ""])),
              saving: false,
              testing: false,
              testResult: null,
              saved: intg.configured,
              skipped: false,
              error: null,
            };
          }
        }
        return next;
      });
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [agent.name]);

  useEffect(() => {
    if (!hasFetched.current) {
      hasFetched.current = true;
      void fetchStatus();
    }
  }, [fetchStatus]);

  // Card state helpers
  const setCardField = (service: string, patch: Partial<CardState>) => {
    setCardStates((prev) => ({
      ...prev,
      [service]: { ...prev[service], ...patch },
    }));
  };

  // Save credentials for one service
  const handleSave = async (service: string) => {
    const state = cardStates[service];
    if (!state) return;
    setCardField(service, { saving: true, error: null });
    try {
      const payload = {
        vars: Object.entries(state.values)
          .filter(([, v]) => v.trim())
          .map(([key, value]) => ({ key, value })),
      };
      const res = await fetch("/api/integrations/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setCardField(service, { saving: false, saved: true });
        // Auto-advance to next step
        setStepIndex((i) => i + 1);
      } else {
        const data = await res.json().catch(() => ({}));
        setCardField(service, {
          saving: false,
          error: String(data.error ?? `Save failed (${res.status})`),
        });
      }
    } catch (err) {
      setCardField(service, {
        saving: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  };

  // Test connection
  const handleTest = async (service: string) => {
    setCardField(service, { testing: true, testResult: null });
    try {
      const res = await fetch(
        `/api/integrations/test?service=${encodeURIComponent(service)}`
      );
      const data: { ok: boolean; detail: string } = await res.json();
      setCardField(service, { testing: false, testResult: { ok: data.ok, detail: data.detail } });
    } catch (err) {
      setCardField(service, {
        testing: false,
        testResult: {
          ok: false,
          detail: err instanceof Error ? err.message : String(err),
        },
      });
    }
  };

  // Skip optional integration
  const handleSkip = (service: string) => {
    setCardField(service, { skipped: true });
    setStepIndex((i) => i + 1);
  };

  // GitHub device flow completed
  const handleGitHubConfigured = () => {
    setCardField("github", { saved: true });
    setStepIndex((i) => i + 1);
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70">
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <div className="w-6 h-6 border-2 border-border border-t-blue-500 rounded-full animate-spin" />
          <span className="text-sm">Checking integrations…</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-xl max-h-[90vh] flex flex-col rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-border shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-base font-semibold text-foreground">Add agent</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Step {Math.min(stepIndex + 1, totalSteps)} of {totalSteps}
              </p>
            </div>
            <Button variant="text" size="none" layout="" onClick={onCancel} className="text-sm">
              ✕
            </Button>
          </div>
          <ProgressBar current={stepIndex + 1} total={totalSteps} />
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">

          {/* ── Overview step ─────────────────────────────────────────────── */}
          {currentStep.kind === "overview" && (
            <div className="space-y-5">
              {/* Agent card */}
              <div className="rounded-xl border border-border bg-card p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="text-lg font-bold text-foreground">{agent.name}</h3>
                    <p className="text-sm text-muted-foreground mt-1">{agent.description}</p>
                    <div className="flex gap-1.5 mt-3 flex-wrap">
                      {agent.tags.map((t) => (
                        <span
                          key={t}
                          className="text-[10px] px-2 py-0.5 rounded bg-secondary text-muted-foreground"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* What this wizard will do */}
              <div className="space-y-2">
                <p className="text-sm font-medium text-foreground">
                  What we'll set up:
                </p>
                <ul className="space-y-1.5">
                  {/* GitHub is always first */}
                  <li className="flex items-center gap-2 text-sm text-muted-foreground">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                    <span>
                      <strong className="text-foreground">GitHub</strong> — connect account to clone this agent's private repo
                    </span>
                  </li>
                  {integrations
                    .filter((i) => i.service !== "github" && i.mandatory && !i.configured)
                    .map((i) => (
                      <li key={i.service} className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                        <span>
                          <strong className="text-foreground">{i.label}</strong> — {i.description}
                        </span>
                      </li>
                    ))}
                  {integrations
                    .filter((i) => i.service !== "github" && i.mandatory && i.configured)
                    .map((i) => (
                      <li key={i.service} className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                        <span>
                          <strong className="text-foreground">{i.label}</strong>{" "}
                          <span className="text-emerald-600 text-xs">(already configured)</span>
                        </span>
                      </li>
                    ))}
                  {integrations
                    .filter((i) => i.service !== "github" && !i.mandatory && !i.configured)
                    .map((i) => (
                      <li key={i.service} className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span className="w-1.5 h-1.5 rounded-full bg-secondary shrink-0" />
                        <span>
                          <strong className="text-muted-foreground">{i.label}</strong> — {i.description}{" "}
                          <span className="text-muted-foreground">(optional)</span>
                        </span>
                      </li>
                    ))}
                  {integrations.length === 0 && (
                    <li className="text-sm text-muted-foreground">No integrations required — this agent is self-contained.</li>
                  )}
                </ul>
              </div>

              {fetchError && (
                <p className="text-sm text-warning bg-warning/10 rounded-lg px-3 py-2">
                  Could not fetch integration status: {fetchError}. You can continue anyway.
                </p>
              )}
            </div>
          )}

          {/* ── GitHub step ───────────────────────────────────────────────── */}
          {currentStep.kind === "github" && (
            <GitHubDeviceConnect
              integration={currentStep.integration}
              onConfigured={handleGitHubConfigured}
            />
          )}

          {/* ── Mandatory integration step ────────────────────────────────── */}
          {currentStep.kind === "mandatory" && (
            <div className="space-y-3">
              <div className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
                Required — {currentStep.index} of {currentStep.total}
              </div>
              <IntegrationFormCard
                integration={currentStep.integration}
                state={
                  cardStates[currentStep.integration.service] ?? {
                    values: {},
                    saving: false,
                    testing: false,
                    testResult: null,
                    saved: false,
                    skipped: false,
                    error: null,
                  }
                }
                optional={false}
                onValueChange={(key, value) =>
                  setCardField(currentStep.integration.service, {
                    values: {
                      ...(cardStates[currentStep.integration.service]?.values ?? {}),
                      [key]: value,
                    },
                  })
                }
                onSave={() => void handleSave(currentStep.integration.service)}
                onTest={() => void handleTest(currentStep.integration.service)}
              />
            </div>
          )}

          {/* ── Optional integration step ─────────────────────────────────── */}
          {currentStep.kind === "optional" && (
            <div className="space-y-3">
              <div className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
                Optional — {currentStep.index} of {currentStep.total}
              </div>
              <IntegrationFormCard
                integration={currentStep.integration}
                state={
                  cardStates[currentStep.integration.service] ?? {
                    values: {},
                    saving: false,
                    testing: false,
                    testResult: null,
                    saved: false,
                    skipped: false,
                    error: null,
                  }
                }
                optional={true}
                onValueChange={(key, value) =>
                  setCardField(currentStep.integration.service, {
                    values: {
                      ...(cardStates[currentStep.integration.service]?.values ?? {}),
                      [key]: value,
                    },
                  })
                }
                onSave={() => void handleSave(currentStep.integration.service)}
                onTest={() => void handleTest(currentStep.integration.service)}
                onSkip={() => handleSkip(currentStep.integration.service)}
              />
            </div>
          )}

          {/* ── Done step ─────────────────────────────────────────────────── */}
          {currentStep.kind === "done" && (
            <div className="flex flex-col items-center text-center gap-4 py-6">
              <div className="w-14 h-14 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                <svg className="w-7 h-7 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <h3 className="text-lg font-semibold text-foreground">
                  {agent.name} is ready
                </h3>
                <p className="text-sm text-muted-foreground mt-1">
                  All integrations are configured. You can start a conversation now.
                </p>
              </div>
              {/* Summary of what was configured */}
              <div className="w-full text-left space-y-1">
                {Object.entries(cardStates)
                  .filter(([, s]) => s.saved || s.skipped)
                  .map(([svc, s]) => {
                    const intg = integrations.find((i) => i.service === svc);
                    return (
                      <div key={svc} className="flex items-center gap-2 text-sm">
                        <span
                          className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${
                            s.saved
                              ? "bg-emerald-500/20 text-emerald-400"
                              : "bg-secondary text-muted-foreground"
                          }`}
                        >
                          {s.saved ? "✓" : "–"}
                        </span>
                        <span className={s.saved ? "text-foreground" : "text-muted-foreground"}>
                          {intg?.label ?? svc}
                        </span>
                        {s.skipped && (
                          <span className="text-[10px] text-muted-foreground">(skipped)</span>
                        )}
                      </div>
                    );
                  })}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-border shrink-0 flex items-center justify-between gap-3">
          {/* Back button */}
          <button
            onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
            disabled={stepIndex === 0 || currentStep.kind === "done"}
            className="px-4 py-2 rounded-lg border border-border hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed text-sm text-muted-foreground transition-colors"
          >
            ← Back
          </button>

          <div className="flex gap-2">
            {currentStep.kind === "overview" && (
              <>
                {/* If all integrations already configured, skip straight to done */}
                {integrations.every((i) => i.configured) ? (
                  <button
                    onClick={onComplete}
                    className="px-5 py-2 rounded-lg bg-success hover:opacity-90 text-sm font-medium text-success-foreground transition-colors"
                  >
                    Start chatting →
                  </button>
                ) : (
                  <Button size="none" layout="" onClick={() => setStepIndex(1)} className="px-5 py-2 text-sm">
                    Set up integrations →
                  </Button>
                )}
              </>
            )}

            {/* GitHub step — let users skip if they don't have OAuth App ready */}
            {currentStep.kind === "github" && (
              <button
                onClick={() => {
                  setCardField("github", { skipped: true });
                  setStepIndex((i) => i + 1);
                }}
                className="px-4 py-2 rounded-lg border border-border hover:bg-secondary text-sm text-muted-foreground transition-colors"
              >
                Skip for now
              </button>
            )}

            {currentStep.kind === "done" && (
              <button
                onClick={onComplete}
                className="px-5 py-2 rounded-lg bg-success hover:opacity-90 text-sm font-medium text-success-foreground transition-colors"
              >
                Start chatting →
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
