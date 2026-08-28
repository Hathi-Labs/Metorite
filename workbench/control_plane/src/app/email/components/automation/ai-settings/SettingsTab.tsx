"use client";

// The Assistant "Advanced Settings" tab and all of its sub-dialogs (writing
// style, personal instructions, signature, reset rules, follow-ups, digest,
// knowledge base, learned preferences/patterns, cold senders, org domains).
// Extracted from AISettingsView.tsx.

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import {
  approveKnowledge, createKnowledge, deleteKnowledge, deleteLearnedPattern,
  deleteRulePattern,
  generateWritingStyle, getAssistantSettings, getVoiceProfile, listColdSenders,
  listKnowledge,
  listLearnedPatterns, listRulePatterns, resetRules,
  listRuleGuidance, deleteRuleGuidance,
  reviewRulePatterns, saveAssistantSettings, scanFollowUps, updateKnowledge,
  upsertColdSender,
} from "../../../lib/api";
import {
  AssistantSettings, ColdBlockerMode, ColdSender, DRAFT_CONFIDENCE_OPTIONS,
  DraftConfidence, KnowledgeEntry, LLMConfigResponse, LearnedPattern,
  LearnedRulePattern, RuleGuidance, VoiceProfile, WEEKDAYS,
} from "../../../lib/types";
import { SignatureEditor } from "../../SignatureEditor";
import { DigestSettingsDialog } from "../DigestSettingsDialog";
import { Modal, SectionHeader, SettingCard, Toggle } from "../ui";
import { VoiceProfileDialog } from "./VoiceProfileDialog";
import {
  Empty, Field, IconAction, INPUT_BASE, INPUT_CLS, Spinner, summary,
} from "./common";

// ── Settings tab ────────────────────────────────────────────────────────────

const CONFIGURE_BTN =
  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border " +
  "text-xs text-muted-foreground hover:text-foreground hover:bg-secondary " +
  "transition-colors";

/** Normalize a user-entered domain to a bare host (mirrors the backend's
 *  normalize_domain): strip a leading @, an email local-part, a trailing path. */
function normDomain(raw: string): string {
  let d = raw.trim().toLowerCase().replace(/^@+/, "");
  if (d.includes("@")) d = d.split("@").pop() ?? "";
  return d.split("/")[0].trim();
}

/** Advanced-settings editor for the extra "your organisation" domains. The
 *  account's own domain is always internal (shown as a locked chip); the user
 *  can add more so multi-brand / secondary-domain mail also counts as internal
 *  (outbound) instead of being mislabelled as a received Receipt/Newsletter. */
function OrgDomainsCard({
  ownDomain,
  domains,
  onChange,
}: {
  ownDomain: string;
  domains: string[];
  onChange: (next: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const add = () => {
    const d = normDomain(input);
    setInput("");
    if (!d || d === ownDomain || domains.includes(d)) return;
    onChange([...domains, d]);
  };
  return (
    <SettingCard
      title="Organisation domains"
      description="Mail from these domains is treated as your own side (outbound) — so it's never mislabelled as a received Receipt or Newsletter, and a teammate's reply counts toward a thread's status. Your account domain is always included."
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {ownDomain && (
          <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] bg-secondary border border-border text-muted-foreground">
            {ownDomain}
            <span className="text-[9px] uppercase tracking-wide opacity-60">
              your domain
            </span>
          </span>
        )}
        {domains.map((d) => (
          <span
            key={d}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-primary/10 border border-primary/20 text-foreground"
          >
            {d}
            <button
              onClick={() => onChange(domains.filter((x) => x !== d))}
              className="text-muted-foreground hover:text-destructive transition-colors"
              aria-label={`Remove ${d}`}
            >
              <Icon name="X" size={11} />
            </button>
          </span>
        ))}
        {!ownDomain && domains.length === 0 && (
          <span className="text-[11px] text-muted-foreground italic">
            No extra domains — your account domain is used.
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-2.5">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="add a domain, e.g. acmecorp.io"
          className={`${INPUT_BASE} flex-1`}
        />
        <button
          onClick={add}
          disabled={!input.trim()}
          className="flex items-center gap-1 px-2.5 py-2 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:opacity-40"
        >
          <Icon name="Plus" size={12} /> Add
        </button>
      </div>
    </SettingCard>
  );
}

export function SettingsTab({ accountId }: { accountId: string | null }) {
  const [settings, setSettings] = useState<AssistantSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [llm, setLlm] = useState<LLMConfigResponse | null>(null);
  const [dialog, setDialog] = useState<
    | "followup"
    | "digest"
    | "writingstyle"
    | "voiceprofile"
    | "personal"
    | "signature"
    | "knowledge"
    | "learned"
    | "patterns"
    | "resetrules"
    | null
  >(null);
  const [voiceProfile, setVoiceProfile] = useState<VoiceProfile | null>(null);

  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    getVoiceProfile(accountId)
      .then((p) => !cancelled && setVoiceProfile(p))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  useEffect(() => {
    fetch("/api/settings/llm")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setLlm(d))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getAssistantSettings(accountId)
      .then((s) => !cancelled && setSettings(s))
      .catch((e) => !cancelled && setError(e.message || "Failed to load settings"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  // Persist the whole settings object. Toggles/selects persist immediately;
  // the free-text cards rely on the bottom "Save settings" button.
  const persist = async (next: AssistantSettings) => {
    setSettings(next);
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await saveAssistantSettings(next);
      setSettings(res);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError((e as Error).message || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  if (!accountId) return <Empty>Select an account first.</Empty>;
  if (loading || !settings) return <Spinner label="Loading settings…" />;

  const s = settings; // narrowed
  const persistPatch = (p: Partial<AssistantSettings>) => persist({ ...s, ...p });
  const followUpOn = s.follow_up_awaiting_days > 0 || s.follow_up_needs_reply_days > 0;
  const digestOn = s.digest_frequency !== "OFF";

  return (
    <div className="h-full overflow-y-auto px-4 sm:px-5 py-4">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* ── Drafting ── */}
        <div className="space-y-2">
          <SettingCard
            title="Auto draft replies"
            description="Automatically draft replies written in your tone to emails needing a reply."
            right={
              <Toggle
                enabled={s.draft_replies}
                onChange={(v) => persistPatch({ draft_replies: v })}
              />
            }
          />
          <SettingCard
            title="Draft confidence"
            description="How sure should the AI be before drafting a reply?"
            right={
              <select
                value={s.draft_confidence}
                onChange={(e) =>
                  persistPatch({
                    draft_confidence: e.target.value as DraftConfidence,
                  })
                }
                className={`${INPUT_CLS} w-44 py-1`}
              >
                {DRAFT_CONFIDENCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            }
          />
        </div>

        {/* ── Updates ── */}
        <div className="space-y-2">
          <SectionHeader>Updates</SectionHeader>
          <SettingCard
            title="Follow-up reminders"
            description="Label emails where you haven't heard back or haven't replied."
            right={
              <div className="flex items-center gap-2">
                {followUpOn && (
                  <button onClick={() => setDialog("followup")} className={CONFIGURE_BTN}>
                    <Icon name="Settings" size={12} /> Configure
                  </button>
                )}
                <Toggle
                  enabled={followUpOn}
                  onChange={(v) =>
                    persistPatch(
                      v
                        ? { follow_up_awaiting_days: 3, follow_up_needs_reply_days: 3 }
                        : { follow_up_awaiting_days: 0, follow_up_needs_reply_days: 0 }
                    )
                  }
                />
              </div>
            }
          />
          <SettingCard
            title="Digest"
            description="Get a daily summary of your emails."
            right={
              <div className="flex items-center gap-2">
                {digestOn && (
                  <button onClick={() => setDialog("digest")} className={CONFIGURE_BTN}>
                    <Icon name="Settings" size={12} /> Configure
                  </button>
                )}
                <Toggle
                  enabled={digestOn}
                  onChange={(v) =>
                    persistPatch({ digest_frequency: v ? "DAILY" : "OFF" })
                  }
                />
              </div>
            }
          />
        </div>

        {/* ── Your voice ── (editors live in popups to keep the tab uncluttered) */}
        <div className="space-y-2">
          <SectionHeader>Your voice</SectionHeader>
          <SettingCard
            title="Writing style"
            description={summary(s.writing_style, "Define your tone and style — used to draft replies in your voice.")}
            right={<EditBtn onClick={() => setDialog("writingstyle")} set={!!s.writing_style} />}
          />
          <SettingCard
            title="Voice profile"
            description={voiceProfileSummary(voiceProfile)}
            right={
              <EditBtn
                onClick={() => setDialog("voiceprofile")}
                set={voiceProfile?.status === "READY"}
                label={
                  voiceProfile?.status === "READY" ||
                  voiceProfile?.status === "BUILDING"
                    ? "Manage"
                    : undefined
                }
              />
            }
          />
          <SettingCard
            title="Personal instructions"
            description={summary(
              [s.about, s.personal_instructions].filter(Boolean).join(" • "),
              "Tell the AI about yourself and how you'd like it to handle your emails.",
            )}
            right={<EditBtn onClick={() => setDialog("personal")} set={!!(s.about || s.personal_instructions)} />}
          />
          <SettingCard
            title="Email signature"
            description={summary(s.signature, "Set your email signature to include in drafted messages.")}
            right={<EditBtn onClick={() => setDialog("signature")} set={!!s.signature} />}
          />
        </div>

        {/* ── Knowledge ── (managed in popups) */}
        <div className="space-y-2">
          <SectionHeader>Knowledge</SectionHeader>
          <SettingCard
            title="Draft knowledge base"
            description="Facts, snippets and boilerplate the assistant can pull into drafts."
            right={<EditBtn onClick={() => setDialog("knowledge")} label="Manage" />}
          />
          <SettingCard
            title="Learned patterns"
            description="Everything the assistant has picked up from your corrections: the lessons that sharpen its judgement on every sender, and the senders pinned to a rule that bypass it altogether."
            right={<EditBtn onClick={() => setDialog("patterns")} label="Manage" />}
          />
          <SettingCard
            title="Writing preferences"
            description="How the assistant adapts its drafting from the edits you make before sending."
            right={<EditBtn onClick={() => setDialog("learned")} label="Manage" />}
          />
        </div>

        {/* ── Advanced ── */}
        <div className="space-y-2">
          <SectionHeader>Advanced</SectionHeader>
          <SettingCard
            title="Run rules automatically on new mail"
            description="Processes incoming inbox mail with your enabled rules as it arrives. Turn it off to stop auto-running (rules still apply when you run them manually or process past emails)."
            right={
              <Toggle
                enabled={s.auto_run}
                onChange={(v) => persistPatch({ auto_run: v })}
              />
            }
          />
          <SettingCard
            title="Multi-rule execution"
            description="When on, the AI applies every rule that matches an email (inbox-zero multi-rule). Off = only the single best-matching rule runs."
            right={
              <Toggle
                enabled={s.multi_rule_execution}
                onChange={(v) => persistPatch({ multi_rule_execution: v })}
              />
            }
          />
          <SettingCard
            title="Sensitive data protection"
            description="Skip auto-drafting replies on emails that look like they contain passwords, OTPs, or card/account numbers."
            right={
              <Toggle
                enabled={s.sensitive_data_protection}
                onChange={(v) => persistPatch({ sensitive_data_protection: v })}
              />
            }
          />
          <SettingCard
            title="Cold-email blocker"
            description="Handle first-time, unsolicited senders."
            right={
              <select
                value={s.cold_email_blocker}
                onChange={(e) =>
                  persistPatch({
                    cold_email_blocker: e.target.value as ColdBlockerMode,
                  })
                }
                className={`${INPUT_CLS} w-44 py-1`}
              >
                <option value="OFF">Off</option>
                <option value="LABEL">Label as “Cold Email”</option>
                <option value="ARCHIVE">Label and archive</option>
              </select>
            }
          />
          <ColdSendersList accountId={accountId} />
          <OrgDomainsCard
            ownDomain={s.own_domain || ""}
            domains={s.org_domains || []}
            onChange={(next) => persistPatch({ org_domains: next })}
          />
          {([
            {
              key: "rule_model" as const,
              title: "Rule evaluation model",
              description:
                "Classifies and labels each incoming email against your rules. A fast tier is recommended for this high-volume task.",
              value: s.rule_model,
              def: "tier-fast",
            },
            {
              key: "draft_model" as const,
              title: "Draft writing model",
              description:
                "Writes background reply drafts, follow-ups, and rule draft actions. A powerful tier is recommended for reply quality — nobody is waiting on these.",
              value: s.draft_model,
              def: "tier-powerful",
            },
            {
              key: "compose_model" as const,
              title: "Manual draft model",
              description:
                "The composer's \"Draft with AI\" button — you wait on this one, so a fast tier is recommended (reasoning tiers can take 30s+ per click).",
              value: s.compose_model,
              def: "tier-fast",
            },
            {
              key: "chat_model" as const,
              title: "Email chat model",
              description:
                "The model the assistant chat panel uses inside the email app.",
              value: s.chat_model,
              def: "tier-balanced",
            },
          ]).map((cfg) => (
            <SettingCard
              key={cfg.key}
              title={cfg.title}
              description={cfg.description}
              right={
                <select
                  value={cfg.value || cfg.def}
                  onChange={(e) =>
                    persistPatch({ [cfg.key]: e.target.value })
                  }
                  className={`${INPUT_CLS} w-56 py-1`}
                >
                  {llm ? (
                    <>
                      {llm && (
                        <optgroup label="Tiers (auto-routing)">
                          {llm.tiers.map((t) => (
                            <option key={t.tier_name} value={t.tier_name}>
                              {t.tier_name}
                              {t.tier_name === cfg.def ? " (default)" : ""}
                            </option>
                          ))}
                        </optgroup>
                      )}
                      {/* ⚠️ A RAW-MODEL OPTGROUP WAS HERE, and D32.7 is why
                          it is not. **Customers never see a model.** Tiers are
                          the only vocabulary, and the
                          Console answers a bare model id with a 400 rather
                          than coercing it — so a value picked here would stop
                          working the day `ROUTER_SERVING_ENABLED` flips.

                          This is H-72's defect on a second surface. /tasks was
                          the urgent one because /tasks is LIVE; /email is
                          `preview` (WS-17), so this closes the same hole
                          before anyone can fall into it.

                          ⚠️ This stops NEW ones. It does not heal a value
                          already saved: that still shows below, because hiding
                          it would leave somebody staring at a picker that
                          disagrees with what their email AI actually runs on.
                          Which tier an existing model id should become is a
                          product decision (H-72). */}
                      {cfg.value &&
                        !llm?.tiers.some((t) => t.tier_name === cfg.value) && (
                          <option value={cfg.value}>
                            {cfg.value} (not a tier — ask us)
                          </option>
                        )}
                    </>
                  ) : (
                    <option value={cfg.value || cfg.def}>
                      {cfg.value || cfg.def} (default)
                    </option>
                  )}
                </select>
              }
            />
          ))}
        </div>

        {/* ── Danger zone ── */}
        <div className="space-y-2">
          <SectionHeader>Danger zone</SectionHeader>
          <div className="bg-card border border-destructive/30 rounded-xl px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h3 className="text-sm font-medium text-foreground">
                  Reset rules
                </h3>
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                  Delete all your rules and restore the default Inbox Zero set
                  (Reply, Newsletter, Marketing, Calendar, Receipt,
                  Notification, Cold Email…). On Outlook the cleanup categories
                  are both labeled and filed into folders; on Gmail they apply
                  labels.
                </p>
              </div>
              <button
                onClick={() => setDialog("resetrules")}
                className="flex flex-shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-lg border border-destructive/40 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
              >
                <Icon name="RotateCcw" size={13} /> Reset rules
              </button>
            </div>
          </div>
        </div>

        {/* Each setting saves on its own (toggle / dialog) — show live status. */}
        <div className="flex items-center gap-2 pb-2 h-5">
          {saving && (
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Icon name="Loader2" className="animate-spin" size={12} /> Saving…
            </span>
          )}
          {saved && <span className="text-[11px] text-emerald-400">Saved ✓</span>}
          {error && <span className="text-[11px] text-destructive">{error}</span>}
        </div>
      </div>

      {dialog === "resetrules" && (
        <ResetRulesDialog
          accountId={accountId}
          onClose={() => setDialog(null)}
          // The rule names were only consumed by the digest dialog, which is
          // now self-fetching (DigestSettingsDialog) — nothing to sync here.
          onDone={() => setDialog(null)}
        />
      )}
      {dialog === "followup" && (
        <FollowUpDialog
          settings={s}
          onSave={(next) => {
            persist(next);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "digest" && (
        <DigestSettingsDialog
          accountId={accountId}
          onClose={() => setDialog(null)}
          onSaved={(next) => setSettings(next)}
        />
      )}
      {dialog === "writingstyle" && (
        <WritingStyleDialog
          settings={s}
          accountId={accountId}
          onSave={(next) => {
            persist(next);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "voiceprofile" && voiceProfile && (
        <VoiceProfileDialog
          accountId={accountId}
          profile={voiceProfile}
          onProfileChange={setVoiceProfile}
          onClose={() => {
            setDialog(null);
            // A build may have been started (or finished) in the dialog —
            // refresh so the card's summary reflects it.
            getVoiceProfile(accountId)
              .then(setVoiceProfile)
              .catch(() => {});
          }}
        />
      )}
      {dialog === "personal" && (
        <PersonalInstructionsDialog
          settings={s}
          onSave={(next) => {
            persist(next);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "signature" && (
        <SignatureDialog
          settings={s}
          onSave={(next) => {
            persist(next);
            setDialog(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "knowledge" && (
        <Modal
          title="Draft knowledge base"
          description="Facts and snippets the assistant can pull into drafts."
          onClose={() => setDialog(null)}
        >
          <KnowledgeBase accountId={accountId} />
        </Modal>
      )}
      {dialog === "learned" && (
        <Modal
          title="Writing preferences"
          description="Preferences picked up from how you edit drafts before sending."
          onClose={() => setDialog(null)}
        >
          <LearnedPreferences accountId={accountId} />
        </Modal>
      )}
      {dialog === "patterns" && (
        <Modal
          title="Learned patterns"
          description="Everything the assistant has picked up from your corrections: the lessons that sharpen its judgement on every sender, and the senders pinned to a rule that bypass it altogether."
          onClose={() => setDialog(null)}
        >
          <LearnedPatternsList accountId={accountId} />
        </Modal>
      )}
    </div>
  );
}

/** One-line summary for the Voice profile card, from the profile's state. */
function voiceProfileSummary(p: VoiceProfile | null): string {
  if (!p || p.status === "EMPTY") {
    return "Teach the assistant to write like you — it studies emails you've already written, over a date range you pick.";
  }
  if (p.status === "BUILDING") return "Building — studying your emails…";
  if (p.status === "FAILED") {
    return `Last build failed${p.last_error ? `: ${p.last_error}` : ""} — open to try again.`;
  }
  const range =
    p.range_start || p.range_end
      ? ` (${p.range_start ?? "…"} → ${p.range_end ?? "today"})`
      : "";
  const suggested =
    p.suggested_knowledge > 0
      ? ` · ${p.suggested_knowledge} knowledge suggestion${p.suggested_knowledge === 1 ? "" : "s"} to review`
      : "";
  return `Learned from ${p.analyzed_count} email${p.analyzed_count === 1 ? "" : "s"}${range} · ${p.enabled ? "on" : "off"}${suggested}`;
}

/** Small "Edit"/"Manage" button used by the Settings rows to open an editor popup. */
function EditBtn({
  onClick,
  label,
  set,
}: {
  onClick: () => void;
  label?: string;
  set?: boolean;
}) {
  return (
    <button onClick={onClick} className={CONFIGURE_BTN}>
      <Icon name="Settings" size={12} /> {label ?? (set ? "Edit" : "Set up")}
    </button>
  );
}

/** Trim a settings value into a one-line preview; fall back to the help text. */
function WritingStyleDialog({
  settings,
  accountId,
  onSave,
  onClose,
}: {
  settings: AssistantSettings;
  accountId: string;
  onSave: (next: AssistantSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  return (
    <Modal
      title="Writing style"
      description="Define your tone and style — used to draft replies in your voice."
      onClose={onClose}
      footer={
        <Button layout="flex items-center" onClick={() => onSave(draft)} className="ml-auto">
          <Icon name="Check" size={13} /> Save
        </Button>
      }
    >
      <textarea
        value={draft.writing_style}
        onChange={(e) => setDraft({ ...draft, writing_style: e.target.value })}
        rows={6}
        placeholder="e.g. Concise and friendly. 2–3 short sentences. No corporate jargon. Sign off with just my first name."
        className={`${INPUT_CLS} resize-none`}
      />
      <WritingStyleGenerator
        accountId={accountId}
        onGenerated={(ws) => setDraft({ ...draft, writing_style: ws })}
      />
    </Modal>
  );
}

function PersonalInstructionsDialog({
  settings,
  onSave,
  onClose,
}: {
  settings: AssistantSettings;
  onSave: (next: AssistantSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  return (
    <Modal
      title="Personal instructions"
      description="Tell the AI about yourself and how you'd like it to handle your emails."
      onClose={onClose}
      footer={
        <Button layout="flex items-center" onClick={() => onSave(draft)} className="ml-auto">
          <Icon name="Check" size={13} /> Save
        </Button>
      }
    >
      <Field label="About you">
        <textarea
          value={draft.about}
          onChange={(e) => setDraft({ ...draft, about: e.target.value })}
          rows={4}
          placeholder="e.g. I'm the founder of Acme. If I'm CC'd, it's not a Reply. Emails from jane@accounting.com aren't Notifications."
          className={`${INPUT_CLS} resize-none`}
        />
      </Field>
      <Field label="Global instructions">
        <textarea
          value={draft.personal_instructions}
          onChange={(e) => setDraft({ ...draft, personal_instructions: e.target.value })}
          rows={4}
          placeholder="Rules the assistant always follows — e.g. Never commit to dates without checking with me. Don't discuss pricing over email."
          className={`${INPUT_CLS} resize-none`}
        />
      </Field>
    </Modal>
  );
}

function SignatureDialog({
  settings,
  onSave,
  onClose,
}: {
  settings: AssistantSettings;
  onSave: (next: AssistantSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  return (
    <Modal
      title="Email signature"
      description="Appended to the bottom of your replies. Use rich text or full HTML — links and images supported."
      onClose={onClose}
      footer={
        <Button layout="flex items-center" onClick={() => onSave(draft)} className="ml-auto">
          <Icon name="Check" size={13} /> Save
        </Button>
      }
    >
      <SignatureEditor
        value={draft.signature || ""}
        onChange={(html) => setDraft({ ...draft, signature: html })}
      />
    </Modal>
  );
}

// ── Settings: Configure dialogs ──────────────────────────────────────────────

/**
 * Confirmation dialog for the destructive "Reset rules" action: deletes every
 * rule on the account and reinstalls the default Inbox Zero set (provider-aware
 * folder moves vs labels). Requires an explicit confirm before firing.
 */
function ResetRulesDialog({
  accountId,
  onClose,
  onDone,
}: {
  accountId: string;
  onClose: () => void;
  onDone: (ruleNames: string[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await resetRules(accountId);
      onDone(res.installed);
    } catch (e) {
      setError((e as Error).message || "Failed to reset rules.");
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Reset all rules?"
      onClose={busy ? () => {} : onClose}
      maxWidth="max-w-md"
      footer={
        <>
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={busy}
            className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-destructive text-destructive-foreground text-xs font-medium hover:bg-destructive/90 transition-colors disabled:opacity-50"
          >
            {busy ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="RotateCcw" size={13} />}
            Reset rules
          </button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex-shrink-0 text-destructive">
          <Icon name="AlertTriangle" size={18} />
        </span>
        <div className="space-y-2 text-xs text-muted-foreground leading-relaxed">
          <p>
            Are you sure you want to reset all the rules? This{" "}
            <span className="font-medium text-foreground">
              permanently deletes every rule on this account
            </span>{" "}
            — including any you created or customized — and reinstalls the default
            Inbox Zero set.
          </p>
          <p>This can’t be undone.</p>
        </div>
      </div>
      {error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2 py-1.5">
          {error}
        </div>
      )}
    </Modal>
  );
}

function FollowUpDialog({
  settings,
  onSave,
  onClose,
}: {
  settings: AssistantSettings;
  onSave: (next: AssistantSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const num = (v: string) => Math.max(0, parseInt(v || "0", 10) || 0);
  const runScan = async () => {
    setScanning(true);
    setScanMsg(null);
    try {
      const r = await scanFollowUps(settings.account_id);
      if (!r.configured) {
        setScanMsg("Set a reminder window above and Save first, then scan.");
      } else if (r.scanned === 0) {
        setScanMsg("No threads are waiting past your reminder windows. 🎉");
      } else {
        const drafted = r.drafted ? `, drafted ${r.drafted} nudge${r.drafted === 1 ? "" : "s"}` : "";
        setScanMsg(`Found ${r.scanned} — labelled ${r.labeled} "Follow-up"${drafted}.`);
      }
    } catch (e) {
      setScanMsg((e as Error).message || "Scan failed.");
    } finally {
      setScanning(false);
    }
  };
  return (
    <Modal
      title="Follow-up reminders"
      description="Get reminded about conversations that need attention. Saturdays and Sundays don't count."
      onClose={onClose}
      footer={
        <>
          <button
            onClick={runScan}
            disabled={scanning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:opacity-50"
          >
            {scanning ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="RefreshCcw" size={13} />}
            Find follow-ups now
          </button>
          <Button layout="flex items-center" onClick={() => onSave(draft)} className="ml-auto">
            <Icon name="Check" size={13} /> Save
          </Button>
        </>
      }
    >
      <Field label="Remind me when they haven't replied after (days)">
        <input
          type="number"
          min={0}
          max={90}
          value={draft.follow_up_awaiting_days}
          onChange={(e) =>
            setDraft({ ...draft, follow_up_awaiting_days: num(e.target.value) })
          }
          className={INPUT_CLS}
        />
      </Field>
      <Field label="Remind me when I haven't replied after (days)">
        <input
          type="number"
          min={0}
          max={90}
          value={draft.follow_up_needs_reply_days}
          onChange={(e) =>
            setDraft({ ...draft, follow_up_needs_reply_days: num(e.target.value) })
          }
          className={INPUT_CLS}
        />
      </Field>
      <label className="flex items-start gap-2 cursor-pointer pt-1">
        <input
          type="checkbox"
          checked={draft.follow_up_auto_draft}
          onChange={(e) =>
            setDraft({ ...draft, follow_up_auto_draft: e.target.checked })
          }
          className="accent-primary mt-0.5"
        />
        <span>
          <span className="text-xs text-foreground">Auto-generate drafts</span>
          <span className="block text-[11px] text-muted-foreground">
            Draft a nudge when you haven&apos;t heard back.
          </span>
        </span>
      </label>
      <p className="text-[11px] text-muted-foreground pt-1">
        &ldquo;Find follow-ups now&rdquo; scans immediately and labels waiting
        threads &mdash; they also get scanned automatically as mail syncs.
      </p>
      {scanMsg && (
        <div className="text-xs text-foreground bg-secondary/40 rounded-md px-2.5 py-2">
          {scanMsg}
        </div>
      )}
    </Modal>
  );
}

function WritingStyleGenerator({
  accountId,
  onGenerated,
}: {
  accountId: string;
  onGenerated: (style: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await generateWritingStyle(accountId);
      onGenerated(res.writing_style);
    } catch (e) {
      setErr((e as Error).message || "Could not analyze your sent mail.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2 -mt-2">
      <button
        onClick={generate}
        disabled={busy}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-[11px] text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors disabled:opacity-50"
      >
        {busy ? (
          <Icon name="Loader2" className="animate-spin" size={12} />
        ) : (
          <Icon name="Wand2" size={12} />
        )}
        Generate from my sent mail
      </button>
      <span className="text-[10px] text-muted-foreground">
        Analyzes recent sent emails — review before saving.
      </span>
      {err && <span className="text-[10px] text-destructive">{err}</span>}
    </div>
  );
}

function KnowledgeBase({ accountId }: { accountId: string | null }) {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<KnowledgeEntry | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    listKnowledge(accountId)
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [accountId]);

  useEffect(load, [load]);

  const save = async () => {
    if (!editing || !accountId || !editing.title.trim()) return;
    setBusy(true);
    try {
      if (editing.id) await updateKnowledge(editing.id, editing);
      else await createKnowledge({ ...editing, account_id: accountId });
      setEditing(null);
      load();
    } finally {
      setBusy(false);
    }
  };

  const remove = async (entry: KnowledgeEntry) => {
    if (!entry.id || !confirm(`Delete “${entry.title}”?`)) return;
    setEntries((prev) => prev.filter((e) => e.id !== entry.id));
    try {
      await deleteKnowledge(entry.id);
    } catch {
      load();
    }
  };

  // Approve a voice-profile suggestion so it starts feeding drafts.
  const approve = async (entry: KnowledgeEntry) => {
    if (!entry.id) return;
    setEntries((prev) =>
      prev.map((e) => (e.id === entry.id ? { ...e, status: "active" } : e))
    );
    try {
      await approveKnowledge(entry.id);
    } catch {
      load();
    }
  };

  if (!accountId) return null;

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <Icon name="BookOpen" size={14} className="text-primary" /> Knowledge base
        </h3>
        {!editing && (
          <button
            onClick={() => setEditing({ account_id: accountId, title: "", content: "" })}
            className="flex items-center gap-1 text-xs text-primary hover:opacity-80"
          >
            <Icon name="Plus" size={12} /> Add entry
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground mb-3">
        Reference snippets (pricing, FAQs, policies, boilerplate) the assistant
        draws on when drafting replies.
      </p>

      {editing ? (
        <div className="space-y-2 border border-border rounded-lg p-2.5 bg-secondary/30">
          <input
            value={editing.title}
            onChange={(e) => setEditing({ ...editing, title: e.target.value })}
            placeholder="Title — e.g. Pricing & plans"
            className={INPUT_CLS}
          />
          <textarea
            value={editing.content}
            onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            rows={4}
            placeholder="The facts the assistant should use when this topic comes up…"
            className={`${INPUT_CLS} resize-none`}
          />
          <div className="flex items-center gap-2">
            <Button layout="flex items-center" onClick={save} disabled={busy || !editing.title.trim()}>
              {busy ? <Icon name="Loader2" className="animate-spin" size={13} /> : <Icon name="Check" size={13} />}
              Save
            </Button>
            <button
              onClick={() => setEditing(null)}
              className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : loading ? (
        <div className="text-xs text-muted-foreground">Loading…</div>
      ) : entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">No entries yet.</div>
      ) : (
        <div className="space-y-1.5">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className="flex items-start gap-2 border border-border rounded-lg px-3 py-2"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 min-w-0">
                  <div className="text-xs font-medium text-foreground truncate">
                    {entry.title}
                  </div>
                  {entry.status === "suggested" ? (
                    <span
                      title="Proposed by the voice-profile builder — not used in drafts until you approve it"
                      className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 flex-shrink-0"
                    >
                      Suggested
                    </span>
                  ) : entry.source === "voice_profile" ? (
                    <span
                      title="Learned from your past emails and approved by you"
                      className="text-[9px] px-1.5 py-0.5 rounded-full bg-secondary text-muted-foreground flex-shrink-0"
                    >
                      Learned
                    </span>
                  ) : null}
                </div>
                <div className="text-[11px] text-muted-foreground line-clamp-2">
                  {entry.content}
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                {entry.status === "suggested" && (
                  <IconAction
                    title="Approve — start using this in drafts"
                    onClick={() => approve(entry)}
                    className="hover:text-emerald-400"
                  >
                    <Icon name="Check" size={12} />
                  </IconAction>
                )}
                <IconAction title="Edit" onClick={() => setEditing(entry)}>
                  <Icon name="Pencil" size={12} />
                </IconAction>
                <IconAction
                  title="Delete"
                  onClick={() => remove(entry)}
                  className="hover:text-destructive"
                >
                  <Icon name="Trash2" size={12} />
                </IconAction>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LearnedPreferences({ accountId }: { accountId: string | null }) {
  const [patterns, setPatterns] = useState<LearnedPattern[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    listLearnedPatterns(accountId)
      .then(setPatterns)
      .catch(() => setPatterns([]))
      .finally(() => setLoading(false));
  }, [accountId]);

  useEffect(load, [load]);

  const forget = async (id: string) => {
    setPatterns((prev) => prev.filter((p) => p.id !== id));
    try {
      await deleteLearnedPattern(id);
    } catch {
      load();
    }
  };

  if (!accountId || loading || patterns.length === 0) return null;

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon name="Sparkles" size={14} className="text-primary" />
        <h3 className="text-sm font-medium text-foreground">Learned preferences</h3>
      </div>
      <p className="text-[11px] text-muted-foreground mb-3">
        Picked up from how you edit the assistant&apos;s drafts before sending. These
        nudge future drafts — global ones always apply; scoped ones only for that
        sender, company, or topic. Remove any you don&apos;t want.
      </p>
      <div className="space-y-1.5">
        {patterns.map((p) => {
          const scope =
            !p.scope_type || p.scope_type === "GLOBAL"
              ? null
              : p.scope_type === "TOPIC"
                ? `topic: ${p.scope_value}`
                : p.scope_value || p.scope_type.toLowerCase();
          return (
          <div key={p.id} className="flex items-start gap-2">
            <div className="flex-1 min-w-0 text-xs text-foreground/80">
              {p.pattern}
              {scope && (
                <span className="ml-1.5 inline-flex items-center text-[9px] px-1.5 py-0.5 rounded-full bg-secondary text-muted-foreground border border-border align-middle">
                  {scope}
                </span>
              )}
              {p.weight > 1 && (
                <span className="ml-1 text-[10px] text-muted-foreground">
                  ×{p.weight}
                </span>
              )}
            </div>
            <button
              onClick={() => forget(p.id)}
              title="Forget this"
              className="text-muted-foreground hover:text-destructive flex-shrink-0"
            >
              <Icon name="X" size={13} />
            </button>
          </div>
          );
        })}
      </div>
    </div>
  );
}

/** Classification patterns (sender → rule include/exclude), inbox-zero style.
 *  These come from MORE than the Fix button: a Fix correction (FIX), a
 *  consistent AI match auto-learned over time (AI), or a label you add/remove in
 *  your own mail client (LABEL_ADDED / LABEL_REMOVED). The `source` badge makes
 *  which one visible, so a pattern you don't recognise isn't mistaken for a Fix
 *  you made. Conversation-status rules (Reply / Awaiting / FYI / Done)
 *  are never sender-pinned and never appear here. */
const _PATTERN_SOURCE_META: Record<string, { label: string; title: string }> = {
  FIX: { label: "Fix", title: "You taught this via the Fix button" },
  AI: {
    label: "Auto",
    title: "Auto-learned after this sender consistently matched one rule",
  },
  LABEL_ADDED: {
    label: "Label",
    title: "Learned when a label was added in your mail client",
  },
  LABEL_REMOVED: {
    label: "Label",
    title: "Learned when a label was removed in your mail client",
  },
  USER: { label: "Manual", title: "Added manually" },
};

/** A pattern still waiting for a human verdict. Only auto-learned ('AI')
 *  patterns can be in this state — anything the user authored is approved on
 *  creation. Excludes are never gated: they only ever PREVENT a label.
 *  Exported: RulesTab nests each rule's patterns and needs the same verdict. */
export function isPendingReview(p: LearnedRulePattern): boolean {
  return !p.exclude && !p.approved_at && !p.rejected_at;
}

function LearnedPatternsList({ accountId }: { accountId: string | null }) {
  const [patterns, setPatterns] = useState<LearnedRulePattern[]>([]);
  const [guidance, setGuidance] = useState<RuleGuidance[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    Promise.all([
      listRulePatterns(accountId).catch(() => [] as LearnedRulePattern[]),
      listRuleGuidance(accountId).catch(() => [] as RuleGuidance[]),
    ])
      .then(([p, g]) => {
        setPatterns(p);
        setGuidance(g);
      })
      .finally(() => setLoading(false));
  }, [accountId]);

  useEffect(load, [load]);

  const forget = async (id: string) => {
    setPatterns((prev) => prev.filter((p) => p.id !== id));
    try {
      await deleteRulePattern(id);
    } catch {
      load();
    }
  };

  const forgetGuidance = async (id: string) => {
    if (!accountId) return;
    setGuidance((prev) => prev.filter((g) => g.id !== id));
    try {
      await deleteRuleGuidance(id, accountId);
    } catch {
      load();
    }
  };

  const review = async (ids: string[] | undefined, approve: boolean) => {
    if (!accountId) return;
    setBusy(true);
    try {
      await reviewRulePatterns({ accountId, patternIds: ids, approve });
      load();
    } catch {
      load();
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spinner label="Loading learned patterns…" />;
  if (patterns.length === 0 && guidance.length === 0) {
    return (
      <div className="text-center text-xs text-muted-foreground py-6 space-y-1">
        <p>Nothing learned yet.</p>
        <p className="text-[11px]">
          Use the <b>Fix</b> button on a History or Test result to correct a
          classification. By default that teaches the AI, so it gets the same
          judgement right for every sender — not just the one you corrected.
        </p>
      </div>
    );
  }

  // Three groups, because a pattern is in one of three genuinely different
  // states — not two. Approved and rejected are the states the user set; the
  // third is the one the ASSISTANT created and nobody has ruled on yet, which
  // is the whole reason this screen exists.
  const needsReview = patterns.filter(isPendingReview);
  const rejected = patterns.filter((p) => !!p.rejected_at);
  const active = patterns.filter(
    (p) => !p.rejected_at && !isPendingReview(p));

  return (
    <div className="space-y-5">
      {/* Two sections, because these are two different things and conflating
          them is what made the screen unreadable: one CHANGES the AI's
          judgement, the other REPLACES it. A user deciding whether to keep an
          entry needs to know which. */}
      <section>
        <SectionExplainer
          title="Teaching the AI"
          detail={
            "Corrections that go into the classifier's prompt. These change how " +
            "it reasons about every sender — they cost a model call, and they " +
            "generalise."
          }
        />
        {guidance.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-1 py-2">
            No corrections yet. Pressing <b>Fix</b> on a misclassified email
            adds one here.
          </p>
        ) : (
          <div className="space-y-1.5">
            {guidance.map((g) => (
              <div
                key={g.id}
                className="rounded-lg border border-border bg-card px-2.5 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-sky-500/15 text-sky-400 flex-shrink-0">
                    {g.rule_name || "All rules"}
                  </span>
                  <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-secondary text-muted-foreground flex-shrink-0">
                    {g.source === "FIX" ? "From a Fix" : "Added by you"}
                  </span>
                  <button
                    onClick={() => forgetGuidance(g.id)}
                    title="Withdraw this correction"
                    className="ml-auto p-1 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors flex-shrink-0"
                  >
                    <Icon name="Trash2" size={12} />
                  </button>
                </div>
                <p className="mt-1 text-xs text-foreground/90 break-words">
                  {g.guidance}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <SectionExplainer
          title="Skipping the AI"
          detail={
            "Senders pinned to a rule. Their mail is filed without the model " +
            "ever seeing it — free and instant, but it only ever applies to " +
            "that one sender, and a wrong pin is silent."
          }
        />

        {/* Needs review first: these were inferred by the assistant from its own
            agreement with itself, and they are the only ones that need a human.
            The explanation lives in the group header rather than a separate
            banner above the list — one amber box saying the same thing twice was
            most of what made this screen noisy. */}
        {needsReview.length > 0 && (
          <PatternGroup
            title="Needs your review"
            count={needsReview.length}
            tone="review"
            detail={
              "The assistant inferred these. They already affect how new mail " +
              "is classified, but the Email Cleaner won't project one across " +
              "your whole mailbox until you confirm it."
            }
            action={
              <button
                onClick={() => review(undefined, true)}
                disabled={busy}
                className="text-[11px] px-2 py-1 rounded-md bg-amber-500/20 text-amber-500 hover:bg-amber-500/30 transition-colors disabled:opacity-50 whitespace-nowrap"
              >
                {busy ? "Approving…" : `Approve all ${needsReview.length}`}
              </button>
            }
          >
            {needsReview.map((p) => (
              <PatternRow
                key={p.id}
                p={p}
                tone="review"
                busy={busy}
                onApprove={() => review([p.id], true)}
                onReject={() => review([p.id], false)}
              />
            ))}
          </PatternGroup>
        )}

        {active.length > 0 && (
          <PatternGroup
            title="In force"
            count={active.length}
            tone="active"
            detail={
              "Applied to every matching message, by both the classifier and " +
              "the Email Cleaner."
            }
          >
            {active.map((p) => (
              <PatternRow
                key={p.id}
                p={p}
                tone="active"
                busy={busy}
                onReject={() => review([p.id], false)}
                onForget={() => forget(p.id)}
              />
            ))}
          </PatternGroup>
        )}

        {/* Collapsed. A rejected pattern is a RECORD, not a thing to act on —
            the engine never returns it to anyone. Left expanded it padded the
            list with rows the user had already dismissed, which is exactly the
            noise this cleanup is removing. Kept visible-on-demand because
            "why isn't this sender being filed?" is answered here. */}
        {rejected.length > 0 && (
          <details className="rounded-lg border border-border/60 bg-card/30">
            <summary className="cursor-pointer select-none px-3 py-2 text-[11px] text-muted-foreground hover:text-foreground transition-colors">
              {rejected.length === 1
                ? "1 rejected pattern"
                : `${rejected.length} rejected patterns`}
              <span className="text-muted-foreground/70">
                {" — never applied. Kept so you can see what was ruled out."}
              </span>
            </summary>
            <div className="px-2 pb-2 space-y-1.5">
              {rejected.map((p) => (
                <PatternRow
                  key={p.id}
                  p={p}
                  tone="rejected"
                  busy={busy}
                  onApprove={() => review([p.id], true)}
                  onForget={() => forget(p.id)}
                />
              ))}
            </div>
          </details>
        )}
      </section>
    </div>
  );
}

/** A labelled group of patterns, with the count in the heading.

 *  The count belongs in the header and not in a badge somewhere else: the
 *  question this screen answers is "how many has the assistant decided on its
 *  own that I haven't checked?", and that number should be readable without
 *  counting rows. */
function PatternGroup({
  title, count, detail, tone, action, children,
}: {
  title: string;
  count: number;
  detail: string;
  tone: "review" | "active";
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h5
            className={`text-[11px] font-semibold ${
              tone === "review" ? "text-amber-500" : "text-foreground"
            }`}
          >
            {`${title} · ${count}`}
          </h5>
          <p className="text-[10px] text-muted-foreground leading-snug">
            {detail}
          </p>
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

/** One pattern. Two rows, not one: a sender address plus a rule name plus a
 *  reach count does not fit one line in a dialog, and `truncate` cut the address
 *  mid-word — leaving the one thing you need to judge the pattern (who it is)
 *  unreadable. Chips and actions share the top row; the pattern itself gets the
 *  full width below and wraps.
 *  Exported: RulesTab renders the same rows nested under each rule, so the two
 *  surfaces can't drift (one row component, two places it appears). */
export function PatternRow({
  p, tone, busy, onApprove, onReject, onForget,
}: {
  p: LearnedRulePattern;
  tone: "review" | "active" | "rejected";
  busy: boolean;
  onApprove?: () => void;
  onReject?: () => void;
  onForget?: () => void;
}) {
  return (
    <div
      className={`rounded-lg px-3 py-2 border ${
        tone === "review"
          ? "bg-amber-500/5 border-amber-500/30"
          : tone === "rejected"
            ? "bg-transparent border-border/60 opacity-70"
            : "bg-card border-border"
      }`}
    >
      <div className="flex items-center gap-2">
        {/* Drained of colour once rejected. A green "Always match" on a pattern
            the engine never returns to anyone asserts a live effect that isn't
            there — the group header says these are dead, and the row must not
            contradict it. */}
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded-md whitespace-nowrap ${
            tone === "rejected"
              ? "bg-muted text-muted-foreground line-through"
              : p.exclude
                ? "bg-red-500/15 text-red-400"
                : "bg-emerald-500/15 text-emerald-400"
          }`}
        >
          {p.exclude ? "Never match" : "Always match"}
        </span>
        {_PATTERN_SOURCE_META[p.source] && (
          <span
            title={_PATTERN_SOURCE_META[p.source].title}
            className="text-[10px] px-1.5 py-0.5 rounded-md whitespace-nowrap bg-muted text-muted-foreground"
          >
            {_PATTERN_SOURCE_META[p.source].label}
          </span>
        )}
        {/* The number that makes review possible: without it, "is this pattern
            right?" cannot be answered from this screen. */}
        {p.reach !== undefined && p.reach > 0 && (
          <span
            className="text-[10px] text-muted-foreground whitespace-nowrap"
            title="Approximate — matched by sender/subject substring, excluding Trash and Junk"
          >
            {p.reach === 1
              ? "about 1 email"
              : `about ${p.reach.toLocaleString()} emails`}
          </span>
        )}
        {/* Actions are driven by which handlers the group passed, so every state
            offers the right verbs: pending → approve / reject; in-force → reject
            (the sticky "this is wrong", which the cleaner's re-learn respects —
            unlike Forget, which just deletes the row and lets the assistant
            re-infer it) plus forget; rejected → restore (approve) plus forget. */}
        <span className="ml-auto flex items-center gap-2">
          {onApprove && (
            <button
              onClick={onApprove}
              disabled={busy}
              title={tone === "rejected"
                ? "Restore — approve this pattern again"
                : "Approve — let the cleaner use this"}
              className="text-emerald-500 hover:text-emerald-400 disabled:opacity-50"
            >
              <Icon name="Check" size={14} />
            </button>
          )}
          {onReject && (
            <button
              onClick={onReject}
              disabled={busy}
              title="Reject — this pattern is wrong (and won't be re-learned)"
              className="text-muted-foreground hover:text-destructive disabled:opacity-50"
            >
              <Icon name="X" size={14} />
            </button>
          )}
          {onForget && (
            <button
              onClick={onForget}
              disabled={busy}
              title="Forget this"
              className="text-muted-foreground hover:text-destructive disabled:opacity-50"
            >
              <Icon name="Trash2" size={13} />
            </button>
          )}
        </span>
      </div>
      <div className="mt-1 text-xs text-foreground/80 break-words">
        <span className="text-muted-foreground">
          {p.pattern_type === "SUBJECT" ? "Subject" : "From"}{" "}
        </span>
        <span className="break-all">{p.value}</span>
        <span className="text-muted-foreground"> → </span>
        <span className="text-foreground">
          {p.rule_name || "(deleted rule)"}
        </span>
      </div>
    </div>
  );
}

/** A section heading that says what the section IS FOR. Without it the two
 *  halves look like the same list split arbitrarily, and the distinction that
 *  matters — improve the AI vs replace it — is invisible. */
function SectionExplainer({
  title, detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div className="mb-2">
      <h4 className="text-xs font-semibold text-foreground">{title}</h4>
      <p className="text-[11px] text-muted-foreground leading-snug">{detail}</p>
    </div>
  );
}

function ColdSendersList({ accountId }: { accountId: string | null }) {
  const [cold, setCold] = useState<ColdSender[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    listColdSenders(accountId)
      .then(setCold)
      .catch(() => setCold([]))
      .finally(() => setLoading(false));
  }, [accountId]);

  useEffect(load, [load]);

  const whitelist = async (email: string) => {
    if (!accountId) return;
    setBusy(email);
    try {
      await upsertColdSender({
        accountId,
        fromEmail: email,
        status: "USER_REJECTED_COLD",
      });
      setCold((prev) =>
        prev.map((c) =>
          c.from_email === email ? { ...c, status: "USER_REJECTED_COLD" } : c
        )
      );
    } catch {
      load();
    } finally {
      setBusy(null);
    }
  };

  if (loading || cold.length === 0) return null;

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <h3 className="text-sm font-medium text-foreground mb-2">Cold senders</h3>
      <div className="space-y-1.5">
        {cold.map((c) => (
          <div key={c.from_email} className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <div className="text-xs text-foreground truncate">{c.from_email}</div>
              {c.reason && (
                <div className="text-[10px] text-muted-foreground truncate">
                  {c.reason}
                </div>
              )}
            </div>
            {c.status === "USER_REJECTED_COLD" ? (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 flex-shrink-0">
                Not cold
              </span>
            ) : busy === c.from_email ? (
              <Icon name="Loader2" className="animate-spin text-muted-foreground" size={13} />
            ) : (
              <button
                onClick={() => whitelist(c.from_email)}
                className="text-[10px] px-2 py-1 rounded-md border border-border text-muted-foreground hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors flex-shrink-0"
              >
                Not cold
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Shared bits ─────────────────────────────────────────────────────────────

