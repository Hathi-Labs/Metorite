"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { AutomationFeature } from "../../lib/types";
import { AISettingsView } from "./AISettingsView";
import { DashboardView } from "./DashboardView";
import { BulkUnsubscribeView } from "./BulkUnsubscribeView";
import { AnalyticsView } from "./AnalyticsView";

interface AutomationViewProps {
  feature: AutomationFeature;
  accountId: string | null;
  selectedEmailId: string | null;
  onClose: () => void;
  onArchived?: () => void;
  /** Switch to a sibling automation surface without going back to the inbox.
   *  Analytics reports problems whose fix lives on another screen; making the
   *  user retrace their steps through the sidebar is how a finding gets lost. */
  onNavigate?: (feature: AutomationFeature) => void;
  /** Open a message in the mailbox reading pane (dashboard row navigation). */
  onOpenEmail?: (messageId: string) => void;
  /** Filter the mailbox by a category label (dashboard category click-through). */
  onFilterLabel?: (label: string) => void;
  /** Filter the mailbox by a sender (dashboard noisy-sender click-through). */
  onFilterSender?: (email: string) => void;
  /** Open a thread and start an AI-drafted reply (dashboard ✍️ row action). */
  onDraftReply?: (messageId: string) => void;
  /** Open a waiting-on-them thread and start an AI follow-up nudge. */
  onNudge?: (messageId: string) => void;
  /** YYYY-MM-DD. AI Settings opens "Process past emails" from this date. The
   *  guided setup sends it, so the member sorts the mail they imported
   *  (EM-T6d). */
  processPastFrom?: string | null;
}

const META: Record<
  AutomationFeature,
  { title: string; subtitle: string; icon: React.ElementType }
> = {
  // 'chat' renders as its own full scene (EmailAssistantChat) in the page, not
  // via this host — this entry just keeps the META map exhaustive.
  chat: {
    title: "Chat",
    subtitle: "Conversational AI assistant",
    icon: themedIcon("MessageSquare"),
  },
  "ai-settings": {
    title: "AI Settings",
    subtitle: "Rules, testing & history",
    icon: themedIcon("Sparkles"),
  },
  digest: {
    // The feature KEY stays "digest" (routing/state churn for zero gain); the
    // surface is the mailbox dashboard — open loops, promises, and traffic.
    title: "Dashboard",
    subtitle: "Open loops, commitments & daily traffic — act from here",
    icon: themedIcon("LayoutDashboard"),
  },
  unsubscribe: {
    title: "Email Cleaner",
    subtitle: "Unsubscribe, auto-archive & clear out your whole mailbox",
    icon: themedIcon("MailMinus"),
  },
  analytics: {
    title: "Analytics",
    subtitle: "Inbox trends & activity",
    icon: themedIcon("BarChart3"),
  },
};

export function AutomationView({
  feature,
  accountId,
  selectedEmailId,
  onClose,
  onArchived,
  onNavigate,
  onOpenEmail,
  onFilterLabel,
  onFilterSender,
  onDraftReply,
  onNudge,
  processPastFrom = null,
}: AutomationViewProps) {
  const meta = META[feature];
  const Icon = meta.icon;

  return (
    <div className="flex flex-col h-full w-full bg-background">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border flex-shrink-0 bg-card">
        <Button variant="ghost" size="icon-sm" radius="keep" layout="" onClick={onClose} aria-label="Back to inbox" className="rounded-md">
          <AppIcon name="ArrowLeft" size={16} />
        </Button>
        <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center flex-shrink-0">
          <Icon size={15} />
        </div>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-foreground leading-tight">
            {meta.title}
          </h1>
          <p className="text-[11px] text-muted-foreground leading-tight">
            {meta.subtitle}
          </p>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0">
        {feature === "ai-settings" && (
          <AISettingsView
            accountId={accountId}
            selectedEmailId={selectedEmailId}
            processPastFrom={processPastFrom}
          />
        )}
        {feature === "digest" && (
          <DashboardView
            accountId={accountId}
            onOpenEmail={onOpenEmail}
            onFilterLabel={onFilterLabel}
            onFilterSender={onFilterSender}
            onDraftReply={onDraftReply}
            onNudge={onNudge}
          />
        )}
        {feature === "unsubscribe" && (
          <BulkUnsubscribeView accountId={accountId} onArchived={onArchived} />
        )}
        {feature === "analytics" && (
          <AnalyticsView accountId={accountId} onNavigate={onNavigate} />
        )}
      </div>
    </div>
  );
}
