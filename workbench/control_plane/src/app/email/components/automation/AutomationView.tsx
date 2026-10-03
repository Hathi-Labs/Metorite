"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { SelectButton } from "@/components/ui/SelectButton";
import { AutomationFeature, EmailAccount } from "../../lib/types";
import { settingsHeader } from "../../lib/mailboxSettings";
import { MailboxChip } from "../MailboxChip";
import { AISettingsView } from "./AISettingsView";
import { DashboardView } from "./DashboardView";
import { BulkUnsubscribeView } from "./BulkUnsubscribeView";
import { AnalyticsView } from "./AnalyticsView";

interface AutomationViewProps {
  feature: AutomationFeature;
  accountId: string | null;
  /** Each mailbox of the member. The header names the mailbox in view and
   *  offers the others (EM-T8f-2, MB-11). */
  accounts: ReadonlyArray<EmailAccount>;
  /** A pick in the header picker. The page runs `pickSettingsMailbox`, which
   *  calls the store's `selectAccount`, because `RulesTab` reads the folders
   *  of the selected mailbox. */
  onPickMailbox: (accountId: string) => void;
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
  /** Called once the Rules tab opened that dialog, so the page clears the date. */
  onProcessPastOpened?: () => void;
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

/**
 * The header of the four automation views (EM-T8f-2, MB-11, §11.4).
 *
 * One header for AI Settings, the Dashboard, the Email Cleaner and Analytics,
 * on desktop and on mobile. It names the mailbox that the view changes, as
 * label and address: a setting always names one mailbox (§11.0 rule 5).
 * With two or more mailboxes it draws the chip and a picker of each mailbox.
 * The picker has no All inboxes option. With one mailbox it draws the name as
 * text, and no chip and no picker (§11.0).
 *
 * The subtitle shows from the `sm` width up, so the name of the mailbox keeps
 * the second line on a phone. `settingsHeader` in `lib/mailboxSettings.ts`
 * decides what shows. Fence: `email-settings-header-names-mailbox` in
 * `lib/mailboxSettings.test.ts`.
 */
export function AutomationHeader({
  feature,
  accounts,
  accountId,
  onClose,
  onPickMailbox,
}: {
  feature: AutomationFeature;
  accounts: ReadonlyArray<EmailAccount>;
  accountId: string | null;
  onClose: () => void;
  onPickMailbox: (accountId: string) => void;
}) {
  const meta = META[feature];
  const Icon = meta.icon;
  const mailbox = settingsHeader(accounts, accountId);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 border-b border-border flex-shrink-0 bg-card">
      <Button variant="ghost" size="icon-sm" radius="keep" layout="" onClick={onClose} aria-label="Back to inbox" className="rounded-md">
        <AppIcon name="ArrowLeft" size={16} />
      </Button>
      <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center flex-shrink-0">
        <Icon size={15} />
      </div>
      <div className="min-w-0 flex-1">
        <h1 className="text-sm font-semibold text-foreground leading-tight">
          {meta.title}
          {mailbox ? (
            <span className="hidden sm:inline text-[11px] font-normal text-muted-foreground"> · {meta.subtitle}</span>
          ) : null}
        </h1>
        {mailbox ? (
          <p className="flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground leading-tight">
            <span className="flex-shrink-0">for</span>
            {mailbox.several ? (
              <>
                <MailboxChip account={mailbox.account} />
                <span className="truncate">· {mailbox.address}</span>
              </>
            ) : (
              <span className="truncate">{mailbox.name}</span>
            )}
          </p>
        ) : (
          <p className="text-[11px] text-muted-foreground leading-tight">{meta.subtitle}</p>
        )}
      </div>
      {mailbox && mailbox.several ? (
        <SelectButton
          label="Change the mailbox"
          value={mailbox.account.id}
          options={mailbox.options}
          widthClass="max-w-[14rem]"
          onChange={onPickMailbox}
        />
      ) : null}
    </div>
  );
}

export function AutomationView({
  feature,
  accountId,
  accounts,
  onPickMailbox,
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
  onProcessPastOpened,
}: AutomationViewProps) {
  return (
    <div className="flex flex-col h-full w-full bg-background">
      <AutomationHeader
        feature={feature}
        accounts={accounts}
        accountId={accountId}
        onClose={onClose}
        onPickMailbox={onPickMailbox}
      />

      {/* Body. The key remounts the views on a change of mailbox, so an
          answer still in flight for the mailbox before lands in a view that
          is gone. Without it, the rules of A can show under "for B", and a
          toggle there changes A (EM-T8f-2 review F1). Each load also has a
          `guardedLoad` of its own. */}
      <div key={accountId ?? ""} className="flex-1 min-h-0">
        {feature === "ai-settings" && (
          <AISettingsView
            accountId={accountId}
            selectedEmailId={selectedEmailId}
            processPastFrom={processPastFrom}
            onProcessPastOpened={onProcessPastOpened}
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
