"use client";

import AppIcon, { themedIcon } from "@/components/Icon";
import { useState } from "react";
import { EmailAccount, EmailFolder, AutomationFeature } from "../lib/types";

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
  onOpenAutomation,
  activeAutomation,
  showMailbox = true,
  showAutomation = true,
}: AccountSidebarProps) {
  const [accountsExpanded, setAccountsExpanded] = useState(true);

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
            {accounts.map((account) => (
              <div
                key={account.id}
                className={`group flex items-center gap-1 w-full px-2 py-2 rounded-md transition-colors ${
                  selectedAccountId === account.id
                    ? "bg-sidebar-accent text-sidebar-foreground"
                    : "hover:bg-sidebar-accent/60 text-sidebar-foreground/70 hover:text-sidebar-foreground"
                }`}
              >
                <button
                  onClick={() => onAccountSelect(account.id)}
                  className="flex items-center gap-2.5 flex-1 min-w-0 text-left"
                >
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0"
                    style={{
                      backgroundColor: account.color,
                      fontSize: "10px",
                      fontWeight: 600,
                    }}
                  >
                    {account.avatar}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1">
                      <span className="text-xs font-medium truncate">{account.label}</span>
                      {account.isDefault && (
                        <AppIcon name="Star"
                          size={10}
                          className="text-amber-400 fill-amber-400 flex-shrink-0"
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
                    className="p-1 rounded reveal-on-hover text-muted-foreground hover:text-amber-400 transition-opacity flex-shrink-0"
                  >
                    <AppIcon name="Star" size={11} />
                  </button>
                )}
                {selectedAccountId === account.id && (
                  <AppIcon name="Check" size={11} className="text-primary flex-shrink-0" />
                )}
                {account.unreadCount > 0 && selectedAccountId !== account.id && (
                  <span className="bg-primary text-primary-foreground text-[9px] rounded-full px-1.5 py-0.5 flex-shrink-0">
                    {account.unreadCount}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

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
        {folders.map(({ label, key, count, type }) => {
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
