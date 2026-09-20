"use client";

// The Assistant "Rules" tab: the rule list, the full rule editor (conditions +
// actions), the action-config sub-editors, and the Rules-tab dialogs (Add rule,
// Process past emails, artifact picker). Extracted from AISettingsView.tsx.

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createEmailFolder, createRule, deleteRule, deleteRulePattern, generateRules,
  getRulePolicies, installPresetRules, listEmailArtifacts, listRulePatterns,
  listRules, processPastEmails, processPastEstimate, reviewRulePatterns,
  updateRule, uploadEmailArtifacts,
} from "../../../lib/api";
import type {
  EmailArtifact, ProcessPastEstimate, RulePolicies,
} from "../../../lib/api";
import {
  AutomationRule, LearnedRulePattern, RuleAction, RuleActionAttachment,
  RuleActionType,
} from "../../../lib/types";
import { useEmailStore } from "../../../lib/emailStore";
import { isPendingReview, PatternRow } from "./SettingsTab";
import { LabeledToggle, Modal } from "../ui";
import {
  ACTION_META, ACTION_TYPES, ActionChip, ActionIcon, actionIconColor,
  exampleActionType,
} from "./actionFormat";
import { Empty, Field, IconAction, INPUT_BASE, INPUT_CLS, Spinner } from "./common";

/**
 * Default rule set, mirroring inbox-zero's presets. Installed on demand into the
 * selected account; each rule is then fully editable.
 */
type PresetRule = Omit<AutomationRule, "account_id">;

const PRESET_RULES: PresetRule[] = [
  {
    name: "Needs Reply",
    instructions: "Emails I need to respond to.",
    enabled: true,
    automated: true,
    run_on_threads: true,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Needs Reply" }, { type: "DRAFT_EMAIL" }],
  },
  {
    name: "Awaiting Reply",
    instructions:
      "Threads where I've already replied and am now waiting to hear back " +
      "from the other person.",
    enabled: true,
    automated: true,
    run_on_threads: true,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Awaiting Reply" }],
  },
  {
    name: "Done",
    instructions:
      "Emails I've already handled or replied to that need no further action " +
      "from me.",
    enabled: true,
    automated: true,
    run_on_threads: true,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Done" }],
  },
  {
    name: "FYI",
    instructions:
      "Important emails I should know about, but don't need to reply to.",
    enabled: true,
    automated: true,
    run_on_threads: true,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "FYI" }],
  },
  {
    name: "Newsletter",
    instructions:
      "Newsletters: regular content from publications, blogs, or services " +
      "I've subscribed to.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Newsletter" }],
  },
  {
    name: "Marketing",
    instructions:
      "Marketing: promotional emails about products, services, sales, or offers.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Marketing" }, { type: "ARCHIVE" }],
  },
  {
    name: "Calendar",
    instructions:
      "Calendar: any email related to scheduling, meeting invites, or " +
      "calendar notifications.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Calendar" }],
  },
  {
    name: "Receipt",
    instructions:
      "Receipts: purchase confirmations, payment receipts, transaction " +
      "records or invoices.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Receipt" }],
  },
  {
    name: "Notification",
    instructions: "Notifications: alerts, status updates, or system messages.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Notification" }],
  },
  {
    name: "Cold Email",
    instructions:
      "Cold emails: unsolicited sales pitches and outreach from people or " +
      "companies I have no prior relationship with.",
    enabled: true,
    automated: true,
    run_on_threads: false,
    conditional_operator: "AND",
    actions: [{ type: "LABEL", label: "Cold Email" }, { type: "ARCHIVE" }],
  },
];


/** Loads a rule by id and shows its editor (a Modal) — used by History's
 *  "View matching rule" so the editor opens over the current tab. */
export function RuleEditorModalLoader({
  accountId,
  ruleId,
  onClose,
}: {
  accountId: string;
  ruleId: string;
  onClose: () => void;
}) {
  const [rule, setRule] = useState<AutomationRule | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    listRules(accountId)
      .then((rs) => {
        if (!cancelled) {
          const found = rs.find((r) => r.id === ruleId) ?? null;
          setRule(found);
          if (!found) setErr("That rule no longer exists.");
        }
      })
      .catch((e) => {
        if (!cancelled) setErr((e as Error).message || "Couldn't load the rule.");
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, ruleId]);

  const save = async (r: AutomationRule) => {
    // Close ONLY on success. `finally` closed the modal either way, so a failed
    // save looked exactly like a successful one and threw the user's edits away
    // with no way to recover them. Save failures use their own state so the
    // editor stays mounted with the user's edits intact — a load failure has
    // nothing to preserve and still replaces the editor outright.
    try {
      if (r.id) await updateRule(r.id, r);
      onClose();
    } catch (e) {
      setSaveErr((e as Error).message || "Couldn't save the rule.");
    }
  };

  if (err) {
    return (
      <Modal title="Rule" onClose={onClose}>
        <div className="text-xs text-destructive">{err}</div>
      </Modal>
    );
  }
  if (!rule) return null; // brief load; the editor Modal appears once resolved
  return (
    <RuleEditor
      rule={rule}
      onSave={save}
      onCancel={onClose}
      saveError={saveErr}
    />
  );
}

// ── Rules tab ───────────────────────────────────────────────────────────────

function RuleMenuItem({
  icon,
  label,
  onClick,
  destructive = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-secondary transition-colors ${
        destructive ? "text-destructive" : "text-foreground"
      }`}
    >
      <span className="shrink-0">{icon}</span>
      {label}
    </button>
  );
}

const emptyRule = (accountId: string): AutomationRule => ({
  account_id: accountId,
  name: "",
  instructions: "",
  enabled: true,
  automated: true,
  run_on_threads: false,
  conditional_operator: "AND",
  from_pattern: "",
  subject_pattern: "",
  actions: [{ type: "ARCHIVE" }],
});

export function RulesTab({
  accountId,
  onSeeHistory,
  onPastJobStarted,
  openRuleId,
  onRuleOpened,
}: {
  accountId: string | null;
  onSeeHistory?: (ruleName: string) => void;
  onPastJobStarted?: () => void;
  openRuleId?: string | null;
  onRuleOpened?: () => void;
}) {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<AutomationRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [showPast, setShowPast] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  // Learned patterns nested under their rule — the deterministic pre-AI stage
  // shown WHERE the rules live, so "why does mail from X get filed as Y?" has
  // one answer screen. Storage stays separate (approval gate + provenance);
  // only the view is unified. Same rows as Settings → Learned patterns.
  const [patterns, setPatterns] = useState<LearnedRulePattern[]>([]);
  const [patternBusy, setPatternBusy] = useState(false);
  // Everything ELSE acting on the mailbox (cold blocker, sender dispositions,
  // provider-native inbox rules) — displayed read-only below the rule list so
  // one screen answers "why did this email move?".
  const [policies, setPolicies] = useState<RulePolicies | null>(null);
  const setPendingChatPrompt = useEmailStore((s) => s.setPendingChatPrompt);

  const loadPatterns = useCallback(() => {
    if (!accountId) return;
    // Best-effort: the rule list must render even if the pattern fetch fails.
    listRulePatterns(accountId).then(setPatterns).catch(() => {});
    getRulePolicies(accountId).then(setPolicies).catch(() => {});
  }, [accountId]);

  const reviewPattern = async (id: string, approve: boolean) => {
    if (!accountId) return;
    setPatternBusy(true);
    try {
      await reviewRulePatterns({ accountId, patternIds: [id], approve });
    } finally {
      setPatternBusy(false);
      loadPatterns();
    }
  };

  const forgetPattern = async (id: string) => {
    setPatterns((prev) => prev.filter((p) => p.id !== id));
    try {
      await deleteRulePattern(id);
    } catch {
      loadPatterns();
    }
  };

  const duplicate = async (rule: AutomationRule) => {
    if (!accountId) return;
    setMenuFor(null);
    const { id: _id, ...rest } = rule;
    void _id;
    try {
      await createRule({
        ...rest,
        account_id: accountId,
        name: `${rule.name} (copy)`,
      });
      load();
    } catch (e) {
      setError((e as Error).message || "Failed to duplicate rule");
    }
  };

  const editWithAI = (rule: AutomationRule) => {
    setMenuFor(null);
    setPendingChatPrompt(
      `Help me improve my "${rule.name}" email rule. ` +
        `Its current instructions are: "${rule.instructions || "(none)"}". ` +
        `I want to `,
    );
  };

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    listRules(accountId)
      .then(setRules)
      .catch((e) => setError(e.message || "Failed to load rules"))
      .finally(() => setLoading(false));
    loadPatterns();
  }, [accountId, loadPatterns]);

  const missingDefaults = PRESET_RULES.some(
    (p) => !rules.some((r) => r.name.toLowerCase() === p.name.toLowerCase())
  );

  const installDefaults = async () => {
    if (!accountId) return;
    setInstalling(true);
    setError(null);
    try {
      // Backend owns the canonical preset set (also used by the AI assistant).
      await installPresetRules(accountId);
      load();
    } catch (e) {
      setError((e as Error).message || "Failed to install default rules");
    } finally {
      setInstalling(false);
    }
  };

  useEffect(load, [load]);

  // Opened from History "View rule": when rules are loaded, open that rule's
  // editor and clear the request so it doesn't re-open on the next render.
  useEffect(() => {
    if (!openRuleId || rules.length === 0) return;
    const r = rules.find((x) => x.id === openRuleId);
    if (r) setEditing(r);
    onRuleOpened?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRuleId, rules]);

  const save = async (rule: AutomationRule) => {
    try {
      if (rule.id) await updateRule(rule.id, rule);
      else await createRule(rule);
      setEditing(null);
      load();
    } catch (e) {
      setError((e as Error).message || "Failed to save rule");
    }
  };

  const toggle = async (rule: AutomationRule) => {
    if (!rule.id) return;
    const next = { ...rule, enabled: !rule.enabled };
    setRules((prev) => prev.map((r) => (r.id === rule.id ? next : r)));
    try {
      await updateRule(rule.id, next);
    } catch {
      load();
    }
  };

  const remove = async (rule: AutomationRule) => {
    if (!rule.id || !confirm(`Delete rule "${rule.name}"?`)) return;
    setRules((prev) => prev.filter((r) => r.id !== rule.id));
    try {
      await deleteRule(rule.id);
    } catch {
      load();
    }
  };

  if (!accountId) return <Empty>Select an account first.</Empty>;
  if (loading) return <Spinner label="Loading rules…" />;

  return (
    <div className="h-full flex flex-col relative">
      {editing && (
        <RuleEditor
          rule={editing}
          onSave={save}
          onCancel={() => setEditing(null)}
        />
      )}
      {/* The intro copy shrinks; the action buttons never do (shrink-0 +
          nowrap), so "Past emails" / "Add rule" stay on one line. */}
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border flex-shrink-0">
        <p className="text-xs text-muted-foreground min-w-0">
          Learned patterns file pinned senders first (no AI), then the AI
          matches your plain-English conditions. Mail nothing matches shows
          as Uncategorized — click its pill, or right-click → Fix, to teach
          a pattern or add a rule.
        </p>
        <div className="flex items-center gap-2 flex-shrink-0">
          {missingDefaults && rules.length > 0 && (
            <button
              onClick={installDefaults}
              disabled={installing}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:opacity-50 whitespace-nowrap"
            >
              {installing ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="Sparkles" size={13} />}
              Add defaults
            </button>
          )}
          {rules.length > 0 && (
            <button
              onClick={() => setShowPast(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors whitespace-nowrap"
            >
              <Icon name="History" size={13} /> Past emails
            </button>
          )}
          <Button layout="flex items-center" onClick={() => setShowAdd(true)} className="whitespace-nowrap">
            <Icon name="Plus" size={13} /> Add rule
          </Button>
        </div>
      </div>
      {showAdd && accountId && (
        <AddRuleDialog
          accountId={accountId}
          onManual={() => {
            setShowAdd(false);
            setEditing(emptyRule(accountId));
          }}
          onCreated={() => {
            setShowAdd(false);
            load();
          }}
          onClose={() => setShowAdd(false)}
        />
      )}
      {showPast && accountId && (
        <ProcessPastEmailsDialog
          accountId={accountId}
          onClose={() => setShowPast(false)}
          onStarted={onPastJobStarted}
        />
      )}

      {error && (
        <div className="px-5 py-2 text-xs text-destructive bg-destructive/10">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
        {rules.length === 0 && (
          <div className="flex flex-col items-center text-center py-10 gap-3">
            <Icon name="Sparkles" size={22} className="text-primary/60" />
            <div className="text-sm text-muted-foreground max-w-xs">
              No rules yet. Install the recommended set (Needs Reply, FYI,
              Newsletter, Marketing, Calendar, Receipt, Notification, Cold Email)
              or create your own.
            </div>
            <Button size="none" layout="flex items-center" onClick={installDefaults} disabled={installing} className="gap-1.5 px-3 py-2 text-xs">
              {installing ? (
                <Icon name="Loader2" className="animate-spin" size={13} />
              ) : (
                <Icon name="Sparkles" size={13} />
              )}
              Install default rules
            </Button>
          </div>
        )}
        {rules.map((rule) => (
          <div
            key={rule.id}
            className="flex items-start gap-3 bg-card border border-border rounded-xl px-4 py-3"
          >
            <button
              onClick={() => toggle(rule)}
              title={rule.enabled ? "Disable" : "Enable"}
              className={`mt-0.5 w-8 h-4.5 rounded-full transition-colors flex-shrink-0 relative ${
                rule.enabled ? "bg-primary" : "bg-secondary"
              }`}
              style={{ height: 18, width: 32 }}
            >
              <span
                className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all ${
                  rule.enabled ? "left-[15px]" : "left-0.5"
                }`}
              />
            </button>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-foreground">
                  {rule.name}
                </span>
              </div>
              {rule.instructions && (
                <div className="text-xs text-muted-foreground mt-0.5">
                  {rule.instructions}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-1 mt-1.5">
                {rule.actions.map((a, i) => (
                  <ActionChip key={i} action={a} />
                ))}
              </div>
              {(() => {
                // This rule's learned patterns — the deterministic stage that
                // files a pinned sender before the AI ever sees the email.
                // Rejected ones are records, not behaviour; they stay in
                // Settings → Learned patterns only.
                const rp = patterns.filter(
                  (p) => p.rule_id === rule.id && !p.rejected_at,
                );
                if (rp.length === 0) return null;
                const pending = rp.filter(isPendingReview).length;
                return (
                  <details className="mt-2">
                    <summary
                      className={`cursor-pointer select-none text-[11px] flex items-center gap-1 transition-colors ${
                        pending > 0
                          ? "text-amber-500 hover:text-amber-400"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <Icon name="Zap" size={11} />
                      {rp.length === 1
                        ? "1 learned pattern"
                        : `${rp.length} learned patterns`}
                      {pending > 0 && ` · ${pending} pending review`}
                      <span className="text-muted-foreground/70 font-normal">
                        — filed without the AI
                      </span>
                    </summary>
                    <div className="mt-1.5 space-y-1.5">
                      {rp.map((p) => (
                        <PatternRow
                          key={p.id}
                          p={p}
                          tone={isPendingReview(p) ? "review" : "active"}
                          busy={patternBusy}
                          onApprove={
                            isPendingReview(p)
                              ? () => reviewPattern(p.id, true)
                              : undefined
                          }
                          onReject={() => reviewPattern(p.id, false)}
                          onForget={
                            isPendingReview(p)
                              ? undefined
                              : () => forgetPattern(p.id)
                          }
                        />
                      ))}
                    </div>
                  </details>
                );
              })()}
            </div>
            <div className="relative flex items-center gap-1 flex-shrink-0">
              <IconAction
                title="More"
                onClick={() => setMenuFor(menuFor === rule.id ? null : rule.id ?? null)}
              >
                <Icon name="MoreVertical" size={14} />
              </IconAction>
              {menuFor === rule.id && (
                <>
                  {/* Click-away backdrop */}
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setMenuFor(null)}
                  />
                  <div className="absolute right-0 top-7 z-50 w-44 rounded-lg border border-border bg-card shadow-lg py-1 text-xs">
                    <RuleMenuItem
                      icon={<Icon name="Pencil" size={13} />}
                      label="Edit"
                      onClick={() => {
                        setMenuFor(null);
                        setEditing(rule);
                      }}
                    />
                    <RuleMenuItem
                      icon={<Icon name="Wand2" size={13} />}
                      label="Edit with AI"
                      onClick={() => editWithAI(rule)}
                    />
                    <RuleMenuItem
                      icon={<Icon name="Copy" size={13} />}
                      label="Duplicate"
                      onClick={() => duplicate(rule)}
                    />
                    <RuleMenuItem
                      icon={<Icon name="History" size={13} />}
                      label="See history"
                      onClick={() => {
                        setMenuFor(null);
                        onSeeHistory?.(rule.name);
                      }}
                    />
                    <div className="my-1 h-px bg-border" />
                    <RuleMenuItem
                      icon={<Icon name="Trash2" size={13} />}
                      label="Delete"
                      destructive
                      onClick={() => {
                        setMenuFor(null);
                        remove(rule);
                      }}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        ))}

        {/* ── Also acting on your mail ─────────────────────────────────────
            The standing policies OUTSIDE the AI rules: the cold-email blocker
            (a Settings toggle that screens unmatched mail), the Cleaner's
            per-sender dispositions, and the provider-native inbox rules —
            including the block filters the Cleaner created upstream. Read-only
            here; each is managed on its own screen. Shown so this tab answers
            "why did this email move?" for every mechanism that exists. */}
        {policies && rules.length > 0 && (
          <div className="pt-3 mt-2 border-t border-border space-y-2">
            <div>
              <h5 className="text-[11px] font-semibold text-foreground">
                Also acting on your mail
              </h5>
              <p className="text-[10px] text-muted-foreground leading-snug">
                Standing policies outside the rules above — managed in
                Settings and the Email Cleaner.
              </p>
            </div>
            <div className="flex items-center gap-2.5 bg-card border border-border rounded-xl px-4 py-2.5">
              <Icon name="Shield"
                size={14}
                className={
                  policies.cold_email_blocker === "OFF"
                    ? "text-muted-foreground"
                    : "text-primary"
                }
              />
              <div className="flex-1 min-w-0 text-xs">
                <span className="font-medium text-foreground">
                  Cold Email Blocker
                </span>
                <span className="text-[11px] text-muted-foreground ml-2">
                  {policies.cold_email_blocker === "OFF"
                    ? "Off"
                    : policies.cold_email_blocker === "ARCHIVE"
                      ? "Screens mail no rule matched · archives cold email"
                      : "Screens mail no rule matched · labels cold email"}
                </span>
              </div>
              <span className="text-[10px] text-muted-foreground flex-shrink-0">
                Settings tab
              </span>
            </div>
            {Object.values(policies.dispositions).some((n) => n > 0) && (
              <div className="flex items-center gap-2.5 bg-card border border-border rounded-xl px-4 py-2.5">
                <Icon name="Inbox" size={14} className="text-muted-foreground" />
                <div className="flex-1 min-w-0 text-xs">
                  <span className="font-medium text-foreground">
                    Sender dispositions
                  </span>
                  <span className="text-[11px] text-muted-foreground ml-2">
                    {[
                      policies.dispositions.AUTO_ARCHIVED &&
                        `${policies.dispositions.AUTO_ARCHIVED} auto-archived`,
                      policies.dispositions.UNSUBSCRIBED &&
                        `${policies.dispositions.UNSUBSCRIBED} unsubscribed`,
                      policies.dispositions.APPROVED &&
                        `${policies.dispositions.APPROVED} approved`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    {policies.filters_active > 0 &&
                      ` · ${policies.filters_active} enforced upstream`}
                  </span>
                </div>
                <span className="text-[10px] text-muted-foreground flex-shrink-0">
                  Email Cleaner
                </span>
              </div>
            )}
            {policies.provider_rules_supported &&
              policies.provider_rules.length > 0 && (
                <details className="bg-card border border-border rounded-xl px-4 py-2.5">
                  <summary className="cursor-pointer select-none text-xs flex items-center gap-2.5 text-foreground hover:text-primary transition-colors">
                    <Icon name="Cloud" size={14} className="text-muted-foreground" />
                    <span className="font-medium">
                      {policies.provider_rules.length === 1
                        ? "1 inbox rule at your mail provider"
                        : `${policies.provider_rules.length} inbox rules at your mail provider`}
                    </span>
                    <span className="text-[11px] text-muted-foreground font-normal">
                      — run upstream, before mail ever syncs here
                    </span>
                  </summary>
                  <div className="mt-2 space-y-1.5">
                    {policies.provider_rules.map((r) => (
                      <div
                        key={r.id}
                        className="rounded-lg border border-border/60 px-3 py-2 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className={
                              r.enabled
                                ? "text-foreground font-medium"
                                : "text-muted-foreground line-through"
                            }
                          >
                            {r.name || "(unnamed rule)"}
                          </span>
                          {r.managed && (
                            <span
                              title="Created by the Email Cleaner when this sender was blocked"
                              className="text-[9px] px-1.5 py-0.5 rounded-md bg-sky-500/15 text-sky-400"
                            >
                              Email Cleaner
                            </span>
                          )}
                          {!r.enabled && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground">
                              disabled
                            </span>
                          )}
                        </div>
                        {(r.from_addresses.length > 0 ||
                          r.summary.length > 0) && (
                          <div className="mt-0.5 text-[11px] text-muted-foreground break-words">
                            {r.from_addresses.length > 0 && (
                              <>From {r.from_addresses.join(", ")}</>
                            )}
                            {r.from_addresses.length > 0 &&
                              r.summary.length > 0 &&
                              " → "}
                            {r.summary.join(" · ")}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Condition types exposed in the rule editor, mapped to the flat rule fields. */
type CondType = "prompt" | "from" | "to" | "subject" | "body";

const COND_META: Record<
  CondType,
  {
    label: string;
    field: "instructions" | "from_pattern" | "to_pattern" | "subject_pattern" | "body_pattern";
    placeholder: string;
    textarea?: boolean;
  }
> = {
  prompt: {
    label: "AI Prompt",
    field: "instructions",
    placeholder: "Describe the emails this matches, in plain English…",
    textarea: true,
  },
  from: { label: "From", field: "from_pattern", placeholder: "newsletter@ or @vendor.com" },
  to: { label: "To", field: "to_pattern", placeholder: "me@company.com" },
  subject: { label: "Subject", field: "subject_pattern", placeholder: "invoice" },
  body: { label: "Body", field: "body_pattern", placeholder: "unsubscribe" },
};

const COND_ORDER: CondType[] = ["prompt", "from", "to", "subject", "body"];

function RuleEditor({
  rule,
  onSave,
  onCancel,
  saveError,
}: {
  rule: AutomationRule;
  onSave: (r: AutomationRule) => void;
  onCancel: () => void;
  /** Surfaced in-place so a failed save keeps the editor (and the user's
   *  unsaved edits) on screen instead of silently discarding them. */
  saveError?: string | null;
}) {
  const [draft, setDraft] = useState<AutomationRule>(rule);
  const set = (patch: Partial<AutomationRule>) =>
    setDraft((d) => ({ ...d, ...patch }));
  const setField = (field: CondType, value: string) =>
    setDraft((d) => ({ ...d, [COND_META[field].field]: value }));

  const setAction = (i: number, patch: Partial<RuleAction>) =>
    setDraft((d) => ({
      ...d,
      actions: d.actions.map((a, idx) => (idx === i ? { ...a, ...patch } : a)),
    }));

  // Real provider labels so the "Categorize" action offers a pick-list (with
  // free-type for new labels), mirroring inbox-zero's LabelCombobox.
  const availableLabels = useEmailStore((s) => s.availableLabels);

  // Conditions visible in the builder: any pre-filled field, plus ones the user
  // explicitly adds. A new rule starts with the AI Prompt row.
  const prefilled = COND_ORDER.filter(
    (t) => ((draft[COND_META[t].field] ?? "") as string).trim() !== ""
  );
  const [shown, setShown] = useState<CondType[]>(
    prefilled.length ? prefilled : ["prompt"]
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const removeCondition = (index: number) => {
    const t = shown[index];
    setShown((s) => s.filter((_, i) => i !== index));
    setField(t, "");
  };
  // Re-type a condition row: carry its value over to the new field type so the
  // user doesn't lose what they typed when switching From → Subject etc.
  const changeCondType = (index: number, next: CondType) => {
    const old = shown[index];
    if (old === next) return;
    const val = (draft[COND_META[old].field] ?? "") as string;
    setField(old, "");
    setField(next, val);
    setShown((s) => s.map((x, i) => (i === index ? next : x)));
  };
  const addCondition = () => {
    const next = COND_ORDER.find((t) => !shown.includes(t));
    if (next) setShown((s) => [...s, next]);
  };

  const valid = draft.name.trim().length > 0 && draft.actions.length > 0;

  return (
    <Modal
      title={rule.id ? "Edit Rule" : "Create Rule"}
      onClose={onCancel}
      maxWidth="max-w-2xl"
      footer={
        <>
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors"
          >
            Cancel
          </button>
          <Button layout="flex items-center" onClick={() => onSave(draft)} disabled={!valid}>
            <Icon name="Check" size={13} /> {rule.id ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      {saveError && (
        <div className="mb-3 px-3 py-2 rounded-lg bg-destructive/10 text-xs text-destructive">
          {saveError}
        </div>
      )}
      {/* Rule name */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-foreground">Rule name</label>
        <input
          value={draft.name}
          onChange={(e) => set({ name: e.target.value })}
          placeholder="e.g. Label receipts"
          className={INPUT_CLS}
        />
      </div>

      {/* When — conditions */}
      <div className="bg-card border border-border rounded-xl p-3.5 space-y-3">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Icon name="Inbox" size={16} className="text-blue-500" />
            When I get an email
          </span>
          {shown.length > 1 && (
            <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <span>match</span>
              <select
                value={draft.conditional_operator}
                onChange={(e) =>
                  set({ conditional_operator: e.target.value as "AND" | "OR" })
                }
                className="bg-secondary border border-border rounded px-1.5 py-0.5 text-foreground outline-none"
              >
                <option value="AND">ALL</option>
                <option value="OR">ANY</option>
              </select>
              <span>of these</span>
            </div>
          )}
        </div>

        <p className="text-xs text-muted-foreground">That matches:</p>

        {shown.map((t, i) => {
          const meta = COND_META[t];
          const value = (draft[meta.field] ?? "") as string;
          const opts = COND_ORDER.filter((o) => o === t || !shown.includes(o));
          return (
            <div key={t} className="flex items-start gap-2">
              <select
                value={t}
                onChange={(e) => changeCondType(i, e.target.value as CondType)}
                className={`${INPUT_BASE} w-28 flex-shrink-0`}
              >
                {opts.map((o) => (
                  <option key={o} value={o}>
                    {COND_META[o].label}
                  </option>
                ))}
              </select>
              {meta.textarea ? (
                <textarea
                  value={value}
                  onChange={(e) => setField(t, e.target.value)}
                  rows={2}
                  placeholder={meta.placeholder}
                  className={`${INPUT_CLS} resize-none flex-1 min-w-0`}
                />
              ) : (
                <input
                  value={value}
                  onChange={(e) => setField(t, e.target.value)}
                  placeholder={meta.placeholder}
                  className={`${INPUT_CLS} flex-1 min-w-0`}
                />
              )}
              {shown.length > 1 && (
                <button
                  onClick={() => removeCondition(i)}
                  title="Remove condition"
                  className="p-1.5 text-muted-foreground hover:text-destructive flex-shrink-0 mt-0.5"
                >
                  <Icon name="X" size={13} />
                </button>
              )}
            </div>
          );
        })}

        {shown.length < COND_ORDER.length && (
          <button
            onClick={addCondition}
            className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-dashed border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            <Icon name="Plus" size={13} /> Add Condition
          </button>
        )}
      </div>

      {/* Then — actions */}
      <div className="bg-card border border-border rounded-xl p-3.5 space-y-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Icon name="Zap" size={16} className="text-emerald-500" />
          Then
        </span>
        <div className="space-y-2.5">
          {draft.actions.map((a, i) => (
            <div key={i} className="flex items-start gap-2">
              <select
                value={a.type}
                onChange={(e) =>
                  setAction(i, { type: e.target.value as RuleActionType })
                }
                title={ACTION_META[a.type].description}
                className={`${INPUT_BASE} w-40 flex-shrink-0`}
              >
                {ACTION_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {ACTION_META[t].label}
                  </option>
                ))}
              </select>
              <div className="flex-1 min-w-0">
                <ActionConfig
                  action={a}
                  idx={i}
                  set={(patch) => setAction(i, patch)}
                  onRemove={() =>
                    set({
                      actions: draft.actions.filter((_, idx) => idx !== i),
                    })
                  }
                  availableLabels={availableLabels}
                />
              </div>
            </div>
          ))}
        </div>
        <button
          onClick={() => set({ actions: [...draft.actions, { type: "LABEL" }] })}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-dashed border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
        >
          <Icon name="Plus" size={13} /> Add Action
        </button>
      </div>

      {/* Advanced options */}
      <div className="border border-border rounded-xl bg-card overflow-hidden">
        <button
          onClick={() => setAdvancedOpen((o) => !o)}
          className="w-full flex items-center gap-1.5 px-3.5 py-2.5 text-sm font-medium text-foreground hover:bg-secondary/40 transition-colors"
        >
          <Icon name="ChevronRight"
            size={14}
            className={`transition-transform ${advancedOpen ? "rotate-90" : ""}`}
          />
          Advanced options
        </button>
        {advancedOpen && (
          <div className="px-3.5 pb-3.5 pt-1 space-y-3 border-t border-border/60">
            {/* Apply to threads */}
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={draft.run_on_threads}
                onChange={(e) => set({ run_on_threads: e.target.checked })}
                className="accent-primary mt-0.5"
              />
              <span>
                <span className="text-xs text-foreground">Apply to threads</span>
                <span className="block text-[11px] text-muted-foreground">
                  Run on every reply in a conversation, not just the first
                  message (recommended for “Reply” / “FYI”).
                </span>
              </span>
            </label>

          </div>
        )}
      </div>
    </Modal>
  );
}

/**
 * MOVE_FOLDER destination picker: a combobox of the account's real folders
 * (user folders + Archive/Junk) with an inline "Create folder" affordance when
 * the typed name doesn't exist yet. The stored value is the folder's display
 * name, which the backend resolves (system folders by key, user folders via
 * get-or-create) — inbox-zero parity for filing mail into folders.
 */
function MoveFolderField({
  value,
  onChange,
  idx,
}: {
  value: string;
  onChange: (v: string) => void;
  idx: number;
}) {
  const folders = useEmailStore((s) => s.folders);
  const accountId = useEmailStore((s) => s.selectedAccountId);
  const fetchFolders = useEmailStore((s) => s.fetchFolders);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Folders a message can be moved into: user folders + the Archive/Junk system
  // targets (inbox/sent/drafts/trash aren't sensible rule destinations).
  const targets = folders.filter(
    (f) => f.type === "user" || f.key === "archive" || f.key === "junk",
  );
  const typed = value.trim();
  const exists = targets.some(
    (t) => t.label.toLowerCase() === typed.toLowerCase(),
  );
  const dlId = `folder-options-${idx}`;

  const handleCreate = async () => {
    if (!accountId || !typed || creating) return;
    setCreating(true);
    setErr(null);
    try {
      const folder = await createEmailFolder(accountId, typed);
      await fetchFolders(accountId);
      onChange(folder.name);
    } catch {
      setErr("Couldn't create that folder. Try again.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <input
        list={dlId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Choose or type a folder name"
        className={INPUT_CLS}
      />
      <datalist id={dlId}>
        {targets.map((f) => (
          <option key={f.key} value={f.label} />
        ))}
      </datalist>
      {typed && !exists && (
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating || !accountId}
          className="flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-dashed border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:opacity-50"
        >
          {creating ? <Icon name="Loader2" size={12} className="animate-spin" /> : <Icon name="FolderPlus" size={12} />}
          Create folder “{typed}”
        </button>
      )}
      {err && <p className="text-[11px] text-destructive">{err}</p>}
    </div>
  );
}

/**
 * The right-hand configuration card for a single rule action — mirrors
 * inbox-zero's per-action card with a "…" overflow menu and the per-field
 * AI-vs-manual model (label combobox ↔ AI prompt; AI draft ↔ manual content
 * with {{variables}}). Attachments + delay live behind the overflow menu.
 */
function ActionConfig({
  action,
  idx,
  set,
  onRemove,
  availableLabels,
}: {
  action: RuleAction;
  idx: number;
  set: (patch: Partial<RuleAction>) => void;
  onRemove: () => void;
  availableLabels: string[];
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [showAttach, setShowAttach] = useState(
    (action.attachments?.length ?? 0) > 0,
  );

  const isDraftReply = action.type === "DRAFT_EMAIL" || action.type === "REPLY";
  const isForward = action.type === "FORWARD";
  const draftLike = isDraftReply || isForward;
  const delayable = action.type !== "CALL_WEBHOOK";
  // Forward is inherently manual (a note above the forwarded body); reply/draft
  // follow the explicit content_manual toggle (default = AI writes it).
  const manual = isForward ? true : !!action.content_manual;

  type MenuItem = { icon: React.ReactNode; label: string; onClick: () => void };
  const menuItems: MenuItem[] = [];
  if (action.type === "LABEL") {
    menuItems.push(
      action.label_ai
        ? { icon: <Icon name="Tag" size={13} />, label: "Use a fixed label", onClick: () => set({ label_ai: false }) }
        : { icon: <Icon name="Sparkles" size={13} />, label: "Use an AI prompt", onClick: () => set({ label_ai: true }) },
    );
  }
  if (isDraftReply) {
    menuItems.push(
      manual
        ? { icon: <Icon name="Sparkles" size={13} />, label: "Use AI draft", onClick: () => set({ content_manual: false }) }
        : { icon: <Icon name="Pencil" size={13} />, label: "Set content manually", onClick: () => set({ content_manual: true }) },
    );
  }
  if (draftLike) {
    menuItems.push({
      icon: <Icon name="Paperclip" size={13} />,
      label: showAttach ? "Hide attachments" : "Configure attachments",
      onClick: () => setShowAttach((v) => !v),
    });
  }
  if (delayable) {
    menuItems.push(
      action.delay_minutes != null
        ? { icon: <Icon name="X" size={13} />, label: "Remove delay", onClick: () => set({ delay_minutes: null }) }
        : { icon: <Icon name="ChevronDown" size={13} />, label: "Add delay", onClick: () => set({ delay_minutes: 60 }) },
    );
  }

  const dlId = `label-options-${idx}`;

  return (
    <div className="border border-border rounded-lg p-3 bg-secondary/30 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <span className={`mt-px ${actionIconColor(action.type)}`}>
            <ActionIcon type={action.type} size={12} />
          </span>
          {ACTION_META[action.type].description}
        </p>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {menuItems.length > 0 && (
            <div className="relative">
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={() => setMenuOpen((o) => !o)} title="More options" className="rounded">
                <Icon name="MoreHorizontal" size={14} />
              </Button>
              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                  <div className="absolute right-0 top-7 z-20 w-52 rounded-lg border border-border bg-popover shadow-lg p-1">
                    {menuItems.map((m, j) => (
                      <button
                        key={j}
                        onClick={() => {
                          m.onClick();
                          setMenuOpen(false);
                        }}
                        className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs text-foreground hover:bg-secondary text-left"
                      >
                        {m.icon}
                        {m.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
          <button
            onClick={onRemove}
            title="Delete action"
            className="p-1 rounded text-muted-foreground hover:text-destructive"
          >
            <Icon name="Trash2" size={14} />
          </button>
        </div>
      </div>

      {/* Categorize (LABEL): label combobox or AI prompt */}
      {action.type === "LABEL" && (
        action.label_ai ? (
          <>
            <input
              value={action.label ?? ""}
              onChange={(e) => set({ label: e.target.value })}
              placeholder={'e.g. {{choose "urgent", "normal", or "low"}}'}
              className={INPUT_CLS}
            />
            <VariablesHint />
          </>
        ) : (
          <>
            <input
              list={dlId}
              value={action.label ?? ""}
              onChange={(e) => set({ label: e.target.value })}
              placeholder="Select a label"
              className={INPUT_CLS}
            />
            <datalist id={dlId}>
              {availableLabels.map((l) => (
                <option key={l} value={l} />
              ))}
            </datalist>
          </>
        )
      )}

      {action.type === "MOVE_FOLDER" && (
        <MoveFolderField
          value={action.label ?? ""}
          onChange={(v) => set({ label: v })}
          idx={idx}
        />
      )}

      {action.type === "CALL_WEBHOOK" && (
        <input
          value={action.url ?? ""}
          onChange={(e) => set({ url: e.target.value })}
          placeholder="https://…"
          className={INPUT_CLS}
        />
      )}

      {/* Draft reply — AI mode (default) */}
      {draftLike && !manual && (
        <>
          <p className="text-xs text-muted-foreground">
            Our AI generates a draft reply from your email history and knowledge
            base.
          </p>
          <DraftToSection />
        </>
      )}

      {/* Draft reply / forward — manual content */}
      {draftLike && manual && (
        <div className="space-y-2">
          <FieldLabel>Content</FieldLabel>
          <textarea
            value={action.content ?? ""}
            onChange={(e) => set({ content: e.target.value })}
            rows={3}
            placeholder={
              isForward
                ? "Note to add above the forwarded message (optional)"
                : "Reply text — supports {{variables}}"
            }
            className={`${INPUT_CLS} resize-none`}
          />
          <FieldLabel>Subject</FieldLabel>
          <input
            value={action.subject ?? ""}
            onChange={(e) => set({ subject: e.target.value })}
            placeholder="Subject (optional)"
            className={INPUT_CLS}
          />
          <FieldLabel>To</FieldLabel>
          <input
            value={action.to_address ?? ""}
            onChange={(e) => set({ to_address: e.target.value })}
            placeholder={
              isForward ? "Forward to (email)" : "To (defaults to the sender)"
            }
            className={INPUT_CLS}
          />
          <div className="grid grid-cols-2 gap-2">
            <div>
              <FieldLabel>CC</FieldLabel>
              <input
                value={action.cc_address ?? ""}
                onChange={(e) => set({ cc_address: e.target.value })}
                placeholder="Cc (optional)"
                className={`${INPUT_CLS} mt-1`}
              />
            </div>
            <div>
              <FieldLabel>BCC</FieldLabel>
              <input
                value={action.bcc_address ?? ""}
                onChange={(e) => set({ bcc_address: e.target.value })}
                placeholder="Bcc (optional)"
                className={`${INPUT_CLS} mt-1`}
              />
            </div>
          </div>
          <VariablesHint />
          <p className="text-[10px] text-muted-foreground">
            Creates a draft for review — never auto-sends.
          </p>
          <DraftToSection />
        </div>
      )}

      {/* Attachments (draft/reply/forward) — behind "Configure attachments" */}
      {draftLike && showAttach && (
        <div className="pt-2 border-t border-border/60 space-y-1.5">
          <FieldLabel>Attachments</FieldLabel>
          <ActionAttachments
            attachments={action.attachments ?? []}
            onChange={(att) => set({ attachments: att })}
          />
        </div>
      )}

      {/* Delay — behind "Add delay" */}
      {delayable && action.delay_minutes != null && (
        <div className="flex items-center gap-2 pt-1">
          <span className="text-[10px] text-muted-foreground">Run after</span>
          <input
            type="number"
            min={0}
            value={action.delay_minutes ?? ""}
            onChange={(e) =>
              set({
                delay_minutes: e.target.value
                  ? Math.max(0, parseInt(e.target.value, 10) || 0)
                  : 0,
              })
            }
            placeholder="0"
            className={`${INPUT_BASE} w-20`}
          />
          <span className="text-[10px] text-muted-foreground">
            minutes (0 = immediately)
          </span>
        </div>
      )}
    </div>
  );
}

/** Small field label used inside the action config card. */
function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs font-medium text-foreground">{children}</span>
  );
}

/**
 * "Draft to" delivery row — Email is always on (drafts land in the inbox).
 * Messaging-channel delivery (Slack/Telegram) is shown for parity but not yet
 * wired, so the connect button is disabled.
 */
function DraftToSection() {
  return (
    <div className="pt-2 border-t border-border/60">
      <span className="text-xs font-medium text-foreground">Draft to</span>
      <label className="flex items-center gap-2 mt-1.5">
        <input
          type="checkbox"
          checked
          disabled
          readOnly
          className="accent-primary"
          title="Drafts always appear in your inbox"
        />
        <span className="text-xs text-foreground">
          Email{" "}
          <span className="text-muted-foreground">
            — Draft appears in your inbox
          </span>
        </span>
      </label>
      <button
        type="button"
        disabled
        title="Coming soon"
        className="mt-2 text-[11px] px-2.5 py-1.5 rounded-md border border-border text-muted-foreground opacity-60 cursor-not-allowed"
      >
        Connect Slack or Telegram
      </button>
    </div>
  );
}

/** Blue {{variables}} hint banner with an inline examples disclosure. */
function VariablesHint() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg bg-blue-500/10 border border-blue-500/20 px-2.5 py-2 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-blue-600 dark:text-blue-400">
          ✨ Use {"{{variables}}"} for personalized content
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="px-1.5 py-0.5 rounded border border-blue-500/30 text-blue-600 dark:text-blue-400 hover:bg-blue-500/10"
        >
          See examples
        </button>
      </div>
      {open && (
        <ul className="mt-2 space-y-1 text-muted-foreground">
          <li>
            <code className="text-foreground">{"{{name}}"}</code> — the sender’s
            name
          </li>
          <li>
            <code className="text-foreground">{"{{summarize the email}}"}</code>{" "}
            — a short summary of their message
          </li>
          <li>
            <code className="text-foreground">{'{{choose "urgent", "normal"}}'}</code>{" "}
            — let the AI pick a value
          </li>
        </ul>
      )}
    </div>
  );
}

// ── Process past emails (inbox-zero parity) ─────────────────────────────────

const PAST_PRESETS: { label: string; days: number }[] = [
  { label: "Last 24 hours", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
  { label: "Last 6 months", days: 182 },
  { label: "Last year", days: 365 },
];

/** Mirrors _PROCESS_PAST_MAX_SPAN_DAYS in the gateway, which is the real bound —
 *  this only stops the user reaching a range the API will refuse. */
const PAST_MAX_SPAN_DAYS = 366;

/** Above this, the run is worth pausing over rather than just counting. Set at
 *  the point where a mistake costs real money instead of pennies. */
const PAST_COSTLY_THRESHOLD = 500;

/** ISO YYYY-MM-DD for `daysAgo` days before today (0 = today). */
function isoDaysAgo(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

/** Days covered by [start, end] inclusive; 0 when either is unset. */
function spanDays(start: string, end: string): number {
  if (!start || !end) return 0;
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.floor((b - a) / 86_400_000) + 1;
}

function ProcessPastEmailsDialog({
  accountId,
  onClose,
  onStarted,
}: {
  accountId: string;
  onClose: () => void;
  onStarted?: () => void;
}) {
  const [start, setStart] = useState(isoDaysAgo(7));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [includeRead, setIncludeRead] = useState(true); // true = all mail, false = unread only
  // OFF by default: a backfill is for filing old mail, not answering it. Every
  // draft action spends a call on the drafting model, on threads that usually
  // ended months ago — so this is opt-in, per run, and never remembered.
  const [draftReplies, setDraftReplies] = useState(false);
  // ON by default: the picker is a RANGE, not a cursor, so re-running it — or
  // nudging "last 7 days" out to 30 — otherwise re-classifies everything it
  // already did, at one AI call each, to rewrite labels those emails already
  // have. Turn it off deliberately to re-apply after changing a rule.
  const [skipProcessed, setSkipProcessed] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<ProcessPastEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);

  const span = spanDays(start, end);
  const tooWide = span > PAST_MAX_SPAN_DAYS;
  // Keep the picker itself inside the cap, so an over-wide range is normally
  // unreachable rather than merely rejected after the fact.
  const minStart = end
    ? new Date(Date.parse(end) - (PAST_MAX_SPAN_DAYS - 1) * 86_400_000)
        .toISOString()
        .slice(0, 10)
    : undefined;

  // Fetch the count for the current range. This dialog used to offer a free date
  // picker and a Process button with nothing between them, so the only way to
  // find out a range covered 4,000 emails was to spend 4,000 AI calls learning
  // it. Skipped entirely when the range is already over the cap — the API would
  // refuse it, and the refusal is the more useful message.
  useEffect(() => {
    if (!accountId || !start || tooWide) {
      setEstimate(null);
      return;
    }
    let live = true;
    setEstimating(true);
    const t = setTimeout(() => {
      processPastEstimate({
        accountId,
        startDate: start,
        endDate: end || undefined,
        includeRead,
        skipProcessed,
      })
        .then((e) => live && setEstimate(e))
        .catch(() => live && setEstimate(null))
        .finally(() => live && setEstimating(false));
    }, 300);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [accountId, start, end, includeRead, skipProcessed, tooWide]);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      // Processing past emails always APPLIES the matched rules (there's nothing
      // to "test" against history) — progress shows in the banner above the tabs
      // and results stream into the History tab as they're applied.
      await processPastEmails({
        accountId,
        startDate: start || undefined,
        endDate: end || undefined,
        isTest: false,
        includeRead,
        draftReplies,
        skipProcessed,
      });
      // Always hand off to the live progress banner: the job downloads the range
      // from the provider first, so there's no meaningful up-front count to gate
      // on (the banner shows "Downloading…" then "Processing N of M…", and the
      // final tally — including "nothing matched" — streams into History).
      onStarted?.();
      onClose();
    } catch (e) {
      setError((e as Error).message || "Failed to start processing");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Process past emails"
      description="Apply your rules to older inbox mail in a date range."
      onClose={onClose}
      footer={
        <button
          onClick={run}
          disabled={busy || tooWide || !start}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-700 transition-colors disabled:opacity-50"
        >
          {busy ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="Play" size={13} />}
          {estimate && estimate.will_process > 0
            ? `Process ${estimate.will_process.toLocaleString()} email${
                estimate.will_process === 1 ? "" : "s"
              }`
            : "Process emails"}
        </button>
      }
    >
      <div className="flex flex-wrap gap-1.5">
        {PAST_PRESETS.map((p) => (
          <button
            key={p.days}
            onClick={() => {
              setStart(isoDaysAgo(p.days));
              setEnd(isoDaysAgo(0));
            }}
            className="text-[11px] px-2 py-1 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="From">
          <input
            type="date"
            value={start}
            min={minStart}
            max={end || undefined}
            onChange={(e) => setStart(e.target.value)}
            className={INPUT_CLS}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            value={end}
            min={start || undefined}
            onChange={(e) => setEnd(e.target.value)}
            className={INPUT_CLS}
          />
        </Field>
      </div>
      {/* The number, before the button — not after the bill. Every email in the
          range is one classification call, and until this existed the only way
          to discover a range held 4,000 of them was to spend 4,000 calls. */}
      {tooWide ? (
        <p className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2.5 py-2">
          That range covers {span.toLocaleString()} days. Processing past emails
          is capped at one year per run, because every email in the range costs
          an AI call. Narrow the range — or run it a year at a time.
        </p>
      ) : estimating ? (
        <p className="text-[11px] text-muted-foreground px-2.5 py-2">
          Counting emails in this range…
        </p>
      ) : estimate ? (
        <div
          className={`text-[11px] rounded-md px-2.5 py-2 space-y-1 ${
            estimate.will_process >= PAST_COSTLY_THRESHOLD
              ? "text-amber-500 bg-amber-500/10"
              : "text-muted-foreground bg-secondary/50"
          }`}
        >
          {estimate.eligible === 0 ? (
            <p>
              {estimate.already_processed > 0
                ? `All ${estimate.already_processed.toLocaleString()} emails in this range have already been processed. Nothing to do.`
                : "No emails found in this range yet — the run will fetch it from your mailbox first."}
            </p>
          ) : (
            <p>
              <strong className="font-medium">
                {estimate.will_process.toLocaleString()}{" "}
                {estimate.will_process === 1 ? "email" : "emails"}
              </strong>{" "}
              will be sent to the AI — one classification call each
              {estimate.will_process >= PAST_COSTLY_THRESHOLD
                ? ". Large runs cost real money; a narrower range costs less and teaches the assistant just as much."
                : "."}
            </p>
          )}
          {estimate.capped && (
            <p>
              {estimate.eligible.toLocaleString()} are eligible, but one run
              covers at most {estimate.limit.toLocaleString()} — the oldest
              first. Run it again to continue.
            </p>
          )}
          {estimate.held_back > 0 && (
            <p>
              {estimate.held_back.toLocaleString()} of these were fetched by
              Clean older mail and have never been seen by the AI.
            </p>
          )}
          {estimate.already_processed > 0 && estimate.eligible > 0 && (
            <p>
              {estimate.already_processed.toLocaleString()} more in this range
              are already processed and will be skipped.
            </p>
          )}
        </div>
      ) : null}
      <div className="flex items-center justify-between">
        <LabeledToggle
          label="Unread only"
          labelRight="All mail"
          enabled={includeRead}
          onChange={setIncludeRead}
        />
      </div>
      <div className="flex items-center justify-between">
        <LabeledToggle
          label="Categorize only"
          labelRight="Also draft replies"
          enabled={draftReplies}
          onChange={setDraftReplies}
        />
      </div>
      <div className="flex items-center justify-between">
        <LabeledToggle
          label="Skip already processed"
          labelRight="Reprocess everything"
          enabled={!skipProcessed}
          onChange={(v) => setSkipProcessed(!v)}
        />
      </div>
      <p className="text-[11px] text-muted-foreground">
        Runs the matched actions on{" "}
        {includeRead ? "every email" : "unread mail"} in the range
        {skipProcessed
          ? " that the rules haven't already been run over"
          : ", including mail the rules have already processed"}
        . A progress bar appears above the tabs while it runs, and applied
        actions stream into the History tab.
      </p>
      {!skipProcessed && (
        <p className="text-[11px] text-amber-500 bg-amber-500/10 rounded-md px-2.5 py-2">
          Every email in the range will be classified again — one AI call each,
          mostly to reapply labels they already carry. Worth it after changing a
          rule; wasteful otherwise.
        </p>
      )}
      {/* Say what drafting costs BEFORE it's switched on, not after the drafts
          appear. Old threads are usually finished conversations, so the default
          files them and leaves them alone. */}
      <p
        className={`text-[11px] rounded-md px-2.5 py-2 ${
          draftReplies
            ? "text-amber-500 bg-amber-500/10"
            : "text-muted-foreground bg-secondary/50"
        }`}
      >
        {draftReplies
          ? "Draft replies will be written for matching mail — one AI call per " +
            "email, on conversations that may have ended long ago. Labels, " +
            "folders and archiving still apply either way."
          : "Labels, folders and archiving only — no drafts will be written, " +
            "and no AI calls spent replying to old threads."}
      </p>
      {result && (
        <div className="text-xs text-emerald-400 bg-emerald-500/10 rounded-md px-2.5 py-2">
          {result}
        </div>
      )}
      {error && (
        <div className="text-xs text-destructive bg-destructive/10 rounded-md px-2.5 py-2">
          {error}
        </div>
      )}
    </Modal>
  );
}

/**
 * Attachments for a draft/reply/forward action (inbox-zero parity). Files are
 * stored in the email-assistant workspace: drag-and-drop or click to upload from
 * your device, or pick an existing artifact. The agent attaches them when it
 * drafts. "AI-selected" lets the assistant choose which to attach at draft time.
 */
function ActionAttachments({
  attachments,
  onChange,
}: {
  attachments: RuleActionAttachment[];
  onChange: (next: RuleActionAttachment[]) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    setErr(null);
    try {
      const up = await uploadEmailArtifacts(files);
      onChange([
        ...attachments,
        ...up.map((u) => ({ path: u.path, name: u.name, ai_selected: false })),
      ]);
    } catch (e) {
      setErr((e as Error).message || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const pick = (a: EmailArtifact) => {
    if (attachments.some((x) => x.path === a.path)) return;
    onChange([...attachments, { path: a.path, name: a.name, ai_selected: false }]);
  };

  return (
    <div className="space-y-1.5">
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {attachments.map((att, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-md border border-border bg-secondary/40 text-foreground"
            >
              <Icon name="Paperclip" size={9} className="text-muted-foreground" />
              {att.name || att.path || "attachment"}
              {att.ai_selected && (
                <span className="text-primary" title="AI-selected source">✨</span>
              )}
              <button
                onClick={() => onChange(attachments.filter((_, j) => j !== i))}
                className="text-muted-foreground hover:text-destructive"
              >
                <Icon name="X" size={9} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Drag-and-drop / click-to-upload zone */}
      <div
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          addFiles(Array.from(e.dataTransfer.files));
        }}
        className={`flex items-center justify-center gap-2 rounded-lg border border-dashed px-3 py-3 text-[11px] cursor-pointer transition-colors ${
          dragOver
            ? "border-primary bg-primary/5 text-primary"
            : "border-border text-muted-foreground hover:border-primary/40 hover:bg-secondary/40"
        }`}
      >
        {uploading ? (
          <>
            <Icon name="Loader2" size={13} className="animate-spin" /> Uploading…
          </>
        ) : (
          <>
            <Icon name="Upload" size={13} /> Drag &amp; drop files, or click to upload
          </>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          addFiles(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />

      <div className="flex items-center gap-3">
        <button
          onClick={() => setShowPicker(true)}
          className="flex items-center gap-1 text-[11px] text-primary hover:opacity-80"
        >
          <Icon name="FolderOpen" size={12} /> Choose from artifacts
        </button>
        {attachments.some((a) => !a.ai_selected) && (
          <Button variant="text" size="none" layout="" onClick={() =>
              onChange(
                attachments.map((a) => ({ ...a, ai_selected: !a.ai_selected })),
              )
            } className="text-[10px]">
            {attachments.every((a) => a.ai_selected)
              ? "Always attach these"
              : "Let the assistant choose which to attach"}
          </Button>
        )}
      </div>
      {err && (
        <div className="text-[10px] text-destructive bg-destructive/10 rounded px-2 py-1">
          {err}
        </div>
      )}

      {showPicker && (
        <ArtifactPickerDialog
          selectedPaths={attachments.map((a) => a.path || "")}
          onPick={pick}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}

/** Popup to browse + pick a file from the email-assistant workspace. */
function ArtifactPickerDialog({
  selectedPaths,
  onPick,
  onClose,
}: {
  selectedPaths: string[];
  onPick: (a: EmailArtifact) => void;
  onClose: () => void;
}) {
  const [artifacts, setArtifacts] = useState<EmailArtifact[] | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    listEmailArtifacts()
      .then(setArtifacts)
      .catch(() => setArtifacts([]));
  }, []);

  const visible = (artifacts ?? []).filter(
    (a) => !filter || a.name.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <Modal
      title="Choose an attachment"
      description="Pick a file from the email assistant's workspace."
      onClose={onClose}
      maxWidth="max-w-md"
    >
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Search files…"
        className={INPUT_CLS}
      />
      <div className="max-h-72 overflow-y-auto space-y-1">
        {artifacts === null ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-3">
            <Icon name="Loader2" size={14} className="animate-spin" /> Loading…
          </div>
        ) : visible.length === 0 ? (
          <div className="text-xs text-muted-foreground py-3">
            No files yet. Upload one by dragging it onto the action.
          </div>
        ) : (
          visible.map((a) => {
            const picked = selectedPaths.includes(a.path);
            return (
              <button
                key={a.path}
                onClick={() => onPick(a)}
                disabled={picked}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-border text-left hover:border-primary/40 hover:bg-secondary/40 transition-colors disabled:opacity-50"
              >
                <Icon name="Paperclip" size={12} className="text-muted-foreground flex-shrink-0" />
                <span className="flex-1 min-w-0">
                  <span className="block text-xs text-foreground truncate">
                    {a.name}
                  </span>
                  <span className="block text-[10px] text-muted-foreground truncate">
                    {a.category} · {(a.size / 1024).toFixed(0)} KB
                  </span>
                </span>
                {picked && <Icon name="Check" size={12} className="text-emerald-500" />}
              </button>
            );
          })
        )}
      </div>
    </Modal>
  );
}

// Example rule prompts, lifted from inbox-zero (examples.ts), as full
// natural-language sentences. Clicking one appends it to the Add-rules prompt;
// the AI turns the text into structured rules. `@[Label]` mentions are written
// out plainly here. Emails/links are illustrative placeholders.
const EXAMPLE_PERSONA_KEYS = [
  "General", "Founder", "Sales", "Recruiter", "Developer", "Support", "Investor",
];

const EXAMPLE_PROMPTS: Record<string, string[]> = {
  General: [
    "Label urgent emails as Urgent",
    "Label emails from @mycompany.com addresses as Team",
    "Forward receipts to jane@accounting.com and label them Receipt",
    "Label newsletters as Newsletter and archive them",
    "Label marketing and promotional emails as Marketing and archive them",
    "Reply to cold emails telling them I'm not interested, then mark them as spam",
    "Label high priority emails as High Priority",
    "If someone asks to set up a call, draft a reply with my calendar link: https://cal.com/example",
    "If a founder sends me an investor update, label it Investor Update and archive it",
    "If someone asks for a discount, reply with the discount code INBOX20",
    "If people ask me to speak at an event, label the email Speaker Opportunity and archive it",
    "Label emails from customers as Customer",
    "Label legal documents as Legal",
    "Label Stripe emails as Stripe and archive them",
  ],
  Founder: [
    "If someone asks to set up a call, draft a reply with my calendar link: https://cal.com/example",
    "Label feedback from customers about our product as Customer Feedback",
    "Label emails from customers who need help and support as Customer Support",
    "Label emails from investors as Investor",
    "Label legal documents as Legal",
    "Label emails about travel as Travel",
    "Label recruitment related emails as Hiring",
  ],
  Sales: [
    "Label emails from prospects as Prospect",
    "Label emails from customers as Customer",
    "Label emails about deal negotiations as Deal Discussion",
    "If someone asks for pricing, draft a reply with our pricing page link: https://company.com/pricing",
    "If someone requests a demo, draft a reply with my calendar link: https://cal.com/example",
    "Label emails containing signed contracts as Signed Contract and forward to legal@company.com",
    "If a customer mentions churn risk, label as Churn Risk and draft an urgent note to customer success",
    "If someone asks about enterprise pricing, draft a reply asking about their company size and requirements",
  ],
  Recruiter: [
    "Label emails from candidates as Candidate",
    "Label emails from hiring managers as Hiring Manager",
    "If someone applies for a job, label as New Application and draft a reply acknowledging their application",
    "Label emails containing resumes or CVs as Resume",
    "Label emails about interview scheduling as Interview Scheduling",
    "Label emails from job boards as Job Board and archive them",
    "Label emails about salary negotiations as Compensation",
    "If an internal employee refers someone, label as Employee Referral",
  ],
  Developer: [
    "Label server errors, deployment failures, and other alerts as Alert and forward to oncall@company.com",
    "Label emails from GitHub as GitHub and archive them",
    "Label emails from Stripe as Stripe and archive them",
    "Label emails about bug reports as Bug",
    "If someone reports a security vulnerability, label as Security and forward to security@company.com",
    "Label emails from recruiters as Recruiter and archive them",
  ],
  Support: [
    "Label customer requests for help as Support Ticket",
    "If someone reports a critical issue, label as Urgent Support and forward to urgent@company.com",
    "Label bug reports as Bug and forward to engineering@company.com",
    "Label feature requests as Feature Request and forward to product@company.com",
    "If someone asks for a refund, draft a reply with our refund policy link: https://company.com/refund-policy",
    "Label positive feedback as Testimonial and forward to marketing@company.com",
  ],
  Investor: [
    "If a founder asks to set up a call, draft a reply with my calendar link: https://cal.com/example",
    "If a founder sends me an investor update, label it Investor Update and archive it",
    "Forward pitch decks to analyst@vc.com and label them Pitch Deck",
    "Label emails from LPs as LP",
    "Label emails containing term sheets as Term Sheet",
    "Label due diligence related emails as Due Diligence",
    "Label emails about portfolio company exits as Exit Opportunity",
  ],
};

/**
 * The primary "Add rule" experience, matching inbox-zero: describe your rules in
 * plain English (one per line). "Choose from examples" reveals a list of real
 * example rules (lifted from inbox-zero) you click to APPEND to the box. "Create
 * rules" turns the text into structured rules via the AI; "Add rule manually"
 * opens the structured editor instead.
 */
function AddRuleDialog({
  accountId,
  onManual,
  onCreated,
  onClose,
}: {
  accountId: string;
  onManual: () => void;
  onCreated: (created: AutomationRule[]) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [persona, setPersona] = useState("General");
  const [showExamples, setShowExamples] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const examples = EXAMPLE_PROMPTS[persona] ?? EXAMPLE_PROMPTS.General;

  const append = (line: string) =>
    setText((t) => {
      const base = t.replace(/\n+$/, "");
      return (base ? base + "\n" : "") + "* " + line;
    });

  const create = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await generateRules(accountId, text);
      if (!res.created || res.created.length === 0) {
        setError(res.error || "Couldn't create any rules — try rephrasing.");
        return;
      }
      onCreated(res.created);
    } catch (e) {
      setError((e as Error).message || "Failed to create rules.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Add rules"
      description="Describe what you want, one rule per line — the assistant turns it into rules."
      onClose={onClose}
      maxWidth="max-w-xl"
      footer={
        <>
          <button
            onClick={onManual}
            disabled={busy}
            className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors disabled:opacity-50"
          >
            Add rule manually
          </button>
          <Button layout="flex items-center" onClick={create} disabled={busy || !text.trim()} className="ml-auto">
            {busy ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="Wand2" size={13} />}
            Create rules
          </Button>
        </>
      }
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        autoFocus
        placeholder={
          "* Label urgent emails as Urgent\n" +
          "* Forward receipts to jane@accounting.com and label them Receipt\n" +
          "* Archive newsletters and marketing"
        }
        className={`${INPUT_CLS} resize-y leading-relaxed`}
      />
      {error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2 py-1.5">
          {error}
        </div>
      )}

      <div className="space-y-2">
        <button
          onClick={() => setShowExamples((v) => !v)}
          className="flex items-center gap-1.5 text-[11px] font-medium text-primary hover:opacity-80"
        >
          <Icon name="Sparkles" size={12} />
          {showExamples ? "Hide examples" : "Choose from examples"}
        </button>
        {showExamples && (
          <>
            {/* Persona tabs */}
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_PERSONA_KEYS.map((p) => (
                <button
                  key={p}
                  onClick={() => setPersona(p)}
                  className={`text-[11px] px-2.5 py-1 rounded-full transition-colors ${
                    persona === p
                      ? "bg-primary/15 text-primary"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            {/* Full-sentence examples — the leading icon shows the action the
                example performs; click to append it to the prompt. */}
            <div className="grid grid-cols-1 gap-1.5 max-h-64 overflow-y-auto pr-1">
              {examples.map((ex) => {
                const at = exampleActionType(ex);
                return (
                  <button
                    key={ex}
                    onClick={() => append(ex)}
                    title={`Adds an example that will ${ACTION_META[at].label.toLowerCase()} — click to append`}
                    className="group flex items-start gap-2 text-left text-[11px] px-2.5 py-2 rounded-lg border border-border text-foreground hover:border-primary/40 hover:bg-secondary/40 transition-colors"
                  >
                    <span className={`mt-0.5 flex-shrink-0 ${actionIconColor(at)}`}>
                      <ActionIcon type={at} size={12} />
                    </span>
                    <span className="flex-1">{ex}</span>
                    <Icon name="Plus" size={11} className="text-muted-foreground/50 mt-0.5 flex-shrink-0 reveal-on-hover transition-opacity" />
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ── Shared result pills + Fix flow (used by Test & History) ──────────────────

/** Friendly name for an action type string (handles unknowns gracefully). */
