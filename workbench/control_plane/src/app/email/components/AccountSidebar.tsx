"use client";

import AppIcon, { themedIcon } from "@/components/Icon";
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { useState } from "react";
import { EmailAccount, EmailFolder, AutomationFeature } from "../lib/types";
import { MailboxAvatar, mailboxLabel } from "./MailboxChip";
import { allInboxesFolders } from "../lib/emailStore";
import { hasAllInboxes, pooledMailboxes, separateMark, separateToggle } from "../lib/mailbox";

interface AccountSidebarProps {
  accounts: EmailAccount[];
  selectedAccountId: string;
  onAccountSelect: (id: string) => void;
  folders: EmailFolder[];
  selectedFolder: string;
  onFolderSelect: (folder: string) => void;
  onAddAccount?: () => void;
  /** Make an account the user's default mailbox (the inbox the UI opens on). */
  onSetDefault?: (id: string) => void;
  /**
   * Ask to disconnect a mailbox (§10.3 step 8). The page owns the confirm
   * dialog. Without this prop the account menu is not drawn.
   */
  onDisconnect?: (account: EmailAccount) => void;
  /** Rename and recolour a mailbox (EM-T8b). The page owns the dialog. */
  onEditMailbox?: (account: EmailAccount) => void;
  /** "Keep separate" (false) or "Show in All inboxes" (true): the store sends
   *  the `PATCH` (EM-T8g-2, D-EM-28). Without it the menu offers neither. */
  onToggleSeparate?: (id: string, pooled: boolean) => void;
  /** All inboxes is the view (EM-T8d, D-EM-22). */
  viewAll?: boolean;
  /** All inboxes: the count of each well-known folder, summed over each
   *  mailbox (`allFolderCounts` of the store, EM-T8f-3). Null until it lands. */
  folderSums?: Readonly<Record<string, number>> | null;
  /** Open All inboxes. Without it, the row is not drawn. */
  onSelectAll?: () => void;
  /** Open one of the Email Automation feature views. */
  onOpenAutomation?: (feature: AutomationFeature) => void;
  /** Currently-open automation feature, for highlighting. */
  activeAutomation?: AutomationFeature | null;
  /** Show the mailbox nav (accounts + folders). Default true.
   *  Set false for the standalone "Automation" mobile drawer. */
  showMailbox?: boolean;
  /** Show the Email Automation app list. Default true.
   *  Set false for the "Inbox" mobile drawer (folders only). */
  showAutomation?: boolean;
}

/**
 * The items of the mailbox menu. It holds no hook, so a test calls it and
 * runs each `onSelect` (EM-T8g-2 fence `email-separate-menu`).
 */
export function accountMenuItems(
  account: EmailAccount,
  accounts: ReadonlyArray<EmailAccount>,
  on: {
    onEditMailbox?: (account: EmailAccount) => void;
    onSetDefault?: (id: string) => void;
    onToggleSeparate?: (id: string, pooled: boolean) => void;
    onDisconnect?: (account: EmailAccount) => void;
  },
): CtxItem[] {
  const { onEditMailbox, onSetDefault, onToggleSeparate, onDisconnect } = on;
  const items: CtxItem[] = [{ kind: "label", label: account.emailAddress }];
  if (onEditMailbox) {
    items.push({
      kind: "item",
      label: "Name and colour",
      icon: themedIcon("Palette"),
      onSelect: () => onEditMailbox(account),
    });
  }
  if (!account.isDefault && onSetDefault) {
    items.push({
      kind: "item",
      label: "Set as default mailbox",
      icon: themedIcon("Star"),
      onSelect: () => onSetDefault(account.id),
    });
  }
  // "Keep separate", or "Show in All inboxes" for a separate mailbox
  // (EM-T8g-2 item 1). With one mailbox the menu offers neither.
  const toggle = onToggleSeparate ? separateToggle(account, accounts) : null;
  if (toggle && onToggleSeparate) {
    items.push({
      kind: "item",
      label: toggle.label,
      icon: themedIcon(toggle.icon),
      onSelect: () => onToggleSeparate(account.id, toggle.nextPooled),
    });
  }
  if (onDisconnect) {
    items.push({ kind: "sep" });
    items.push({
      kind: "item",
      label: "Disconnect mailbox",
      icon: themedIcon("Unplug"),
      danger: true,
      onSelect: () => onDisconnect(account),
    });
  }
  return items;
}

const AUTOMATION_ITEMS: {
  key: AutomationFeature;
  label: string;
  icon: React.ElementType;
}[] = [
  { key: "chat", label: "Chat", icon: themedIcon("MessageSquare") },
  { key: "digest", label: "Dashboard", icon: themedIcon("LayoutDashboard") },
  { key: "unsubscribe", label: "Email Cleaner", icon: themedIcon("MailMinus") },
  { key: "ai-settings", label: "AI Settings", icon: themedIcon("Sparkles") },
  { key: "analytics", label: "Analytics", icon: themedIcon("BarChart3") },
];

export function AccountSidebar({
  accounts,
  selectedAccountId,
  onAccountSelect,
  folders,
  selectedFolder,
  onFolderSelect,
  onAddAccount,
  onSetDefault,
  onDisconnect,
  onEditMailbox,
  onToggleSeparate,
  viewAll = false,
  folderSums = null,
  onSelectAll,
  onOpenAutomation,
  activeAutomation,
  showMailbox = true,
  showAutomation = true,
}: AccountSidebarProps) {
  const [accountsExpanded, setAccountsExpanded] = useState(true);
  // All inboxes shows for two or more pooled mailboxes. Its count is the sum
  // of their Inbox counts (§11.4), and a selected mailbox row is never also
  // selected. A separate mailbox adds to neither (EM-T8g-2 item 2).
  const pooled = pooledMailboxes(accounts);
  const showAll = !!onSelectAll && hasAllInboxes(accounts);
  const allUnread = pooled.reduce((n, a) => n + (a.unreadCount || 0), 0);
  const isSelected = (id: string) => !viewAll && selectedAccountId === id;
  // In All inboxes only the folders that every mailbox has show, because a
  // custom folder belongs to one mailbox. Each well-known folder shows its
  // sum over each mailbox, and each other folder shows no count (§11.4
  // "Folders", EM-T8f-3).
  const shownFolders = viewAll ? allInboxesFolders(folders, folderSums) : folders;
  // The account menu: which account, and where to draw it.
  const [menu, setMenu] = useState<{ account: EmailAccount; x: number; y: number } | null>(null);

  return (
    <div className="flex flex-col h-full bg-sidebar text-sidebar-foreground overflow-hidden">
      {showMailbox && (
      <>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-4 border-b border-sidebar-border flex-shrink-0">
        <span className="text-xs tracking-widest uppercase text-muted-foreground font-semibold">
          Accounts
        </span>
        <button
          className="p-1 rounded hover:bg-sidebar-accent text-muted-foreground hover:text-sidebar-foreground transition-colors"
          title="Add email account"
          onClick={onAddAccount}
        >
          <AppIcon name="Plus" size={14} />
        </button>
      </div>

      {/* Accounts list */}
      <div className="flex-shrink-0 px-2 pb-2">
        <button
          onClick={() => setAccountsExpanded((v) => !v)}
          className="flex items-center gap-1.5 w-full px-2 py-1 text-xs text-muted-foreground hover:text-sidebar-foreground transition-colors"
        >
          {accountsExpanded ? <AppIcon name="ChevronDown" size={12} /> : <AppIcon name="ChevronRight" size={12} />}
          <span>Email Accounts</span>
        </button>

        {accountsExpanded && (
          <div className="mt-1 space-y-0.5">
            {showAll && (
              <button
                onClick={onSelectAll}
                aria-pressed={viewAll}
                className={`flex items-center gap-2.5 w-full px-2 py-2 rounded-md transition-colors text-left ${
                  viewAll
                    ? "bg-sidebar-accent text-sidebar-foreground"
                    : "hover:bg-sidebar-accent/60 text-sidebar-foreground/70 hover:text-sidebar-foreground"
                }`}
              >
                <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground">
                  <AppIcon name="Inbox" size={13} />
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-xs font-medium truncate">All inboxes</span>
                  <span className="block text-[10px] text-muted-foreground truncate">
                    {pooled.length} mailboxes
                  </span>
                </span>
                {viewAll ? (
                  <AppIcon name="Check" size={11} className="text-primary flex-shrink-0" />
                ) : allUnread > 0 ? (
                  <span className="bg-primary text-primary-foreground text-[10px] rounded-full px-1.5 py-0.5 flex-shrink-0">
                    {allUnread}
                  </span>
                ) : null}
              </button>
            )}
            {accounts.map((account) => (
              <div
                key={account.id}
                className={`group flex items-center gap-1 w-full px-2 py-2 rounded-md transition-colors ${
                  isSelected(account.id)
                    ? "bg-sidebar-accent text-sidebar-foreground"
                    : "hover:bg-sidebar-accent/60 text-sidebar-foreground/70 hover:text-sidebar-foreground"
                }`}
              >
                <button
                  onClick={() => onAccountSelect(account.id)}
                  className="flex items-center gap-2.5 flex-1 min-w-0 text-left"
                >
                  <MailboxAvatar account={account} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1">
                      <span className="text-xs font-medium truncate">{mailboxLabel(account)}</span>
                      {separateMark(account, accounts) && (
                        // A separate mailbox says so in words, beside its chip:
                        // All inboxes leaves it out (EM-T8g-2 item 1).
                        <Badge size="xs" title="Kept out of All inboxes" className="flex-shrink-0">
                          {separateMark(account, accounts)}
                        </Badge>
                      )}
                      {account.syncStatus === "error" && (
                        // A mailbox that stopped syncing shows it in the switcher,
                        // so All inboxes cannot hide it (§11.4, EM-T8d review).
                        <AppIcon name="AlertCircle"
                          size={10}
                          className="text-warning flex-shrink-0"
                          aria-label="Needs attention"
                        />
                      )}
                      {account.isDefault && (
                        <AppIcon name="Star"
                          size={10}
                          className="text-warning fill-warning flex-shrink-0"
                          aria-label="Default mailbox"
                        />
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {account.emailAddress}
                    </div>
                  </div>
                </button>
                {/* Set-as-default: revealed on hover for non-default accounts. */}
                {!account.isDefault && onSetDefault && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSetDefault(account.id);
                    }}
                    title="Set as default mailbox"
                    className="p-1 rounded reveal-on-hover text-muted-foreground hover:text-warning transition-opacity flex-shrink-0"
                  >
                    <AppIcon name="Star" size={11} />
                  </button>
                )}
                {isSelected(account.id) && (
                  <AppIcon name="Check" size={11} className="text-primary flex-shrink-0" />
                )}
                {(onDisconnect || onEditMailbox || onToggleSeparate) && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    icon="MoreHorizontal"
                    onClick={(e) => {
                      e.stopPropagation();
                      const r = e.currentTarget.getBoundingClientRect();
                      setMenu({ account, x: r.left, y: r.bottom + 4 });
                    }}
                    aria-label={`Account menu for ${account.emailAddress}`}
                    aria-haspopup="menu"
                    title="Account menu"
                    className="flex-shrink-0"
                  />
                )}
                {account.unreadCount > 0 && !isSelected(account.id) && (
                  <span className="bg-primary text-primary-foreground text-[9px] rounded-full px-1.5 py-0.5 flex-shrink-0">
                    {account.unreadCount}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={accountMenuItems(menu.account, accounts, {
            onEditMailbox, onSetDefault, onToggleSeparate, onDisconnect,
          })}
          onClose={() => setMenu(null)}
        />
      )}

      <div className="border-t border-sidebar-border mx-3 mb-2 flex-shrink-0" />
      </>
      )}

      {/* Email Automation — sits between accounts and folders */}
      {showAutomation && (
        <div className="flex-shrink-0 px-2 py-2.5">
          <div className="flex items-center gap-1.5 px-2 py-1 text-[10px] tracking-widest uppercase text-muted-foreground font-semibold">
            <AppIcon name="Zap" size={11} className="text-primary" />
            <span>Email Automation</span>
          </div>
          <div className="mt-1 space-y-0.5">
            {AUTOMATION_ITEMS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => onOpenAutomation?.(key)}
                className={`flex items-center gap-2.5 w-full px-2 py-1.5 rounded-md transition-colors text-left ${
                  activeAutomation === key
                    ? "bg-primary/15 text-primary"
                    : "text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                }`}
              >
                <Icon size={13} className="flex-shrink-0" />
                <span className="text-xs">{label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {showMailbox && showAutomation && (
        <div className="border-t border-sidebar-border mx-3 mb-2 flex-shrink-0" />
      )}

      {/* Folders */}
      {showMailbox && (
      <div className="flex-1 overflow-y-auto px-2 space-y-0.5 scrollbar-hide">
        {shownFolders.map(({ label, key, count, type }) => {
          const IconComponent = getFolderIcon(key, type);
          return (
            <button
              key={key}
              onClick={() => onFolderSelect(key)}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-md transition-colors text-left ${
                selectedFolder === key
                  ? "bg-primary/15 text-primary"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
              }`}
            >
              <IconComponent size={14} className="flex-shrink-0" />
              <span className="flex-1 text-xs">{label}</span>
              {count > 0 && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                    selectedFolder === key
                      ? "bg-primary text-primary-foreground"
                      : "bg-secondary text-muted-foreground"
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
      )}
    </div>
  );
}

// Helper to map folder key to Lucide icon component.
function getFolderIcon(key: string, type?: "system" | "user"): React.ElementType {
  const map: Record<string, React.ElementType> = {
    all: themedIcon("Mails"),
    inbox: themedIcon("Inbox"),
    starred: themedIcon("Star"),
    snoozed: themedIcon("Clock"),
    sent: themedIcon("Send"),
    drafts: themedIcon("FileText"),
    archive: themedIcon("Archive"),
    junk: themedIcon("ShieldAlert"),
    labels: themedIcon("Tag"),
    trash: themedIcon("Trash2"),
  };
  if (map[key]) return map[key];
  // User-created provider folders/labels get a generic folder icon.
  return type === "user" ? themedIcon("Folder") : themedIcon("Inbox");
}
