"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useViewMode } from "@/components/ViewModeProvider";
import { useMobileDrawer } from "@/components/AppShell";
import { AccountSidebar } from "./components/AccountSidebar";
import { EmailList } from "./components/EmailList";
import { EmailToolbar } from "./components/EmailToolbar";
import { QuickFilters } from "./components/QuickFilters";
import { SearchBar } from "./components/SearchBar";
import { MailboxActions } from "./components/MailboxActions";
import { EmailDetail } from "./components/EmailDetail";
import { EmailAssistantChat } from "./components/EmailAssistantChat";
import { ComposePanel } from "./components/ComposePanel";
import { CommandPalette, Command } from "./components/CommandPalette";
import { TaskCaptureModal } from "./components/TaskCaptureModal";
import { AutomationView } from "./components/automation/AutomationView";
import { useEmailStore, isRealFolder } from "./lib/emailStore";
import { Email, AutomationFeature } from "./lib/types";
import { folderLabel } from "./lib/utils";
import { isSearchActive } from "./lib/searchFilters";

export default function EmailPage() {
  const { isMobile } = useViewMode();

  // Desktop UI state
  const [leftOpen, setLeftOpen] = useState(true);
  const [listOpen, setListOpen] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [oauthStatus, setOauthStatus] = useState<{
    gmail: boolean; microsoft: boolean; checked: boolean;
  }>({ gmail: false, microsoft: false, checked: false });

  // Mobile-specific state
  const [mobileView, setMobileView] = useState<"inbox" | "detail">("inbox");

  // Email Automation overlay (Assistant / Unsubscribe / Archive / Analytics)
  const [automationFeature, setAutomationFeature] =
    useState<AutomationFeature | null>(null);

  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();

  // ── Zustand store ──
  const {
    accounts,
    emails,
    emailsTotal,
    folders,
    accountsLoading,
    emailsLoading,
    loadingMore,
    backfilling,
    backfillExhausted,
    selectedAccountId,
    selectedFolder,
    selectedEmailId,
    selectedEmailOverride,
    searchQuery,
    searchFilters,
    composeOpen,
    composeDefaults,
    pendingSend,
    taskCaptureNotice,
    taskCapturePopupEmailId,
    closeTaskCapturePopup,
    notifyTaskCaptured,
    pendingChatPrompt,
    error,
    authErrors,
    fetchAccounts,
    fetchEmails,
    loadMoreEmails,
    backfillOlder,
    selectAccount,
    setDefaultAccount,
    selectFolder,
    selectEmail,
    openCompose,
    closeCompose,
    updateEmail,
    deleteEmail,
    triggerSync,
    syncStatus,
    sendEmail,
    softRefresh,
  } = useEmailStore();

  // Fetch on mount
  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  // Deep link: /email?account=<id>&email=<id> opens a SPECIFIC message —
  // the link tasks put on email-origin items ("Open"). The account param is
  // consumed by the store's initial-account pick; the email param is ours:
  // open once the right account is active (openEmailById fetches the
  // message even if it isn't in the loaded folder page).
  const deepLinkedEmailRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedAccountId) return;
    let emailParam: string | null = null;
    try {
      emailParam = new URL(window.location.href).searchParams.get("email");
    } catch {
      return;
    }
    if (!emailParam || deepLinkedEmailRef.current === emailParam) return;
    deepLinkedEmailRef.current = emailParam;
    useEmailStore.getState().openEmailById(emailParam);
  }, [selectedAccountId]);

  // Fetch emails when account or folder changes
  useEffect(() => {
    if (selectedAccountId) {
      fetchEmails();
    }
  }, [selectedAccountId, selectedFolder, fetchEmails]);

  // Background auto-refresh: silently re-pull the current folder so changes the
  // assistant or upstream make (labels, drafts, new mail, archives) show up
  // without a manual reload. Pauses while the tab is hidden; refreshes the
  // moment the user returns to the tab. softRefresh no-ops past page 1.
  useEffect(() => {
    if (!selectedAccountId) return;
    const tick = () => {
      if (document.visibilityState === "visible") softRefresh();
    };
    const id = setInterval(tick, 20000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [selectedAccountId, softRefresh]);

  // Derived data
  const selectedAccount = accounts.find((a) => a.id === selectedAccountId) ?? null;
  // Prefer the loaded-list message; fall back to an out-of-list message opened
  // by id from a chat card (so "Open in inbox" works from any folder/view).
  const selectedEmail =
    emails.find((e) => e.id === selectedEmailId) ??
    (selectedEmailOverride?.id === selectedEmailId ? selectedEmailOverride : null);
  // Unread badge: for a real folder, count the mail that belongs to it; for a
  // pseudo-folder (All/Starred) or a search, the displayed list already spans
  // folders, so match against selectedFolder would count nothing — count every
  // unread row on screen instead.
  const countAllShown =
    !isRealFolder(selectedFolder) || isSearchActive(searchQuery, searchFilters);
  const unreadCount = emails.filter(
    (e) => (countAllShown || e.folder === selectedFolder) && !e.isRead
  ).length;
  // The provider can't page a pseudo-folder (there's no "all"/"starred" folder
  // to ask for older mail from), so never offer "load older" on those views.
  const canBackfillFolder =
    isRealFolder(selectedFolder) && !backfillExhausted[selectedFolder];
  // "processing" (the background rules/labels pipeline after H1) counts as busy
  // too, so the top-bar refresh button keeps spinning until it settles.
  const syncing = selectedAccountId
    ? syncStatus[selectedAccountId] === "syncing" ||
      syncStatus[selectedAccountId] === "processing"
    : false;

  // Reset mobile view when folder/account changes
  useEffect(() => {
    setMobileView("inbox");
  }, [selectedFolder, selectedAccountId]);

  // ── Onboarding detection ──
  // Check OAuth status and decide whether to show the setup guide
  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch("/api/integrations/status");
        if (!res.ok) return;
        const data: Array<{ service: string; configured: boolean }> = await res.json();
        const gmailOk = data.find((i) => i.service === "gmail-oauth")?.configured ?? false;
        const msOk = data.find((i) => i.service === "microsoft-oauth")?.configured ?? false;
        setOauthStatus({ gmail: gmailOk, microsoft: msOk, checked: true });
      } catch {
        setOauthStatus((prev) => ({ ...prev, checked: true }));
      }
    };
    void check();
  }, []);

  // Show onboarding when accounts fetched and none exist
  useEffect(() => {
    if (!accountsLoading && accounts.length === 0 && oauthStatus.checked) {
      setShowOnboarding(true);
    }
  }, [accountsLoading, accounts.length, oauthStatus.checked]);

  const dismissOnboarding = () => setShowOnboarding(false);

  // ── Mobile drawer content builders ──

  // Two separate mobile drawers: "Inbox" shows accounts + folders only,
  // "Automation" shows the email-automation app list only.
  const accountsDrawerRef = useRef<React.ReactNode>(null);
  const automationDrawerRef = useRef<React.ReactNode>(null);

  const handleAccountSelect = useCallback(
    (id: string) => {
      selectAccount(id);
      setMobileView("inbox");
      closeDrawer();
    },
    [selectAccount, closeDrawer]
  );

  const handleFolderSelect = useCallback(
    (f: string) => {
      selectFolder(f);
      // Picking a folder is a "take me to my mail" signal — leave any open
      // automation scene (Assistant / Reply Zero / Chat / …) and show the inbox.
      setAutomationFeature(null);
      if (isMobile) {
        setMobileView("inbox");
        closeDrawer();
      }
    },
    [selectFolder, isMobile, closeDrawer]
  );

  const handleConnect = useCallback((provider: "gmail" | "microsoft" | "imap") => {
    if (provider === "imap") {
      window.location.href = "/email?addAccount=imap";
    } else {
      // Navigate to the BFF, never straight at the gateway host. A top-level
      // navigation carries no Bearer and no X-User-Email (the session cookie is
      // on this origin, not api.*), so the gated authorize route 401s every
      // caller. api/email/oauth/[provider]/authorize runs server-side, attaches
      // the identity, and re-issues the provider redirect. Identity now comes
      // from the session on the server — never from a `user_email` parameter.
      //
      // URLSearchParams already percent-encodes values — don't pre-encode with
      // encodeURIComponent or redirect_after ends up double-encoded and the
      // callback treats it as a relative path (→ /email/oauth/https%3A%2F%2F… 404).
      const params = new URLSearchParams({ redirect_after: window.location.href });
      window.location.href = `/api/email/oauth/${provider}/authorize?${params.toString()}`;
    }
  }, []);

  const handleAddAccount = useCallback(() => {
    setShowAddModal(true);
  }, []);

  const handleOpenAutomation = useCallback(
    (feature: AutomationFeature) => {
      setAutomationFeature(feature);
      if (isMobile) closeDrawer();
    },
    [isMobile, closeDrawer]
  );

  accountsDrawerRef.current = (
    <AccountSidebar
      accounts={accounts}
      selectedAccountId={selectedAccountId ?? ""}
      onAccountSelect={handleAccountSelect}
      folders={folders}
      selectedFolder={selectedFolder}
      onFolderSelect={handleFolderSelect}
      onAddAccount={handleAddAccount}
      onSetDefault={setDefaultAccount}
      showAutomation={false}
    />
  );

  automationDrawerRef.current = (
    <AccountSidebar
      accounts={accounts}
      selectedAccountId={selectedAccountId ?? ""}
      onAccountSelect={handleAccountSelect}
      folders={folders}
      selectedFolder={selectedFolder}
      onFolderSelect={handleFolderSelect}
      onSetDefault={setDefaultAccount}
      onOpenAutomation={handleOpenAutomation}
      activeAutomation={automationFeature}
      showMailbox={false}
    />
  );

  // Listen for bottom-nav tab events from AppShell MobileBottomNav.
  useEffect(() => {
    const handler = (e: Event) => {
      const tab = (e as CustomEvent<string>).detail;
      if (tab === "email-accounts" && accountsDrawerRef.current) {
        // "Inbox" — folders/labels only (no automation section).
        openDrawer(accountsDrawerRef.current);
      } else if (tab === "email-automation" && automationDrawerRef.current) {
        // "Automation" — the email-automation app list.
        openDrawer(automationDrawerRef.current);
      } else if (tab === "email-ai") {
        // "AI Chat" — jump straight to the email chat agent (a full scene,
        // like Assistant / Reply Zero), not a bottom drawer.
        setAutomationFeature("chat");
        closeDrawer();
      }
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, [openDrawer, closeDrawer]);

  // Opening an email from a chat card (EmailToolCards) must leave any open
  // automation scene — the full-screen Chat otherwise covers the inbox so the
  // selected email never shows. On mobile, switch to the detail view + close
  // the chat drawer.
  useEffect(() => {
    const onOpen = () => {
      setAutomationFeature(null);
      if (isMobile) {
        setMobileView("detail");
        closeDrawer();
      }
    };
    window.addEventListener("cc-email-open", onOpen);
    return () => window.removeEventListener("cc-email-open", onOpen);
  }, [isMobile, closeDrawer]);

  // When the Assistant "Fix" flow queues a chat prompt, surface the AI chat so
  // the prompt lands in its input. The chat is a full scene on both mobile and
  // desktop, so open it the same way.
  useEffect(() => {
    if (!pendingChatPrompt) return;
    setAutomationFeature("chat");
    closeDrawer();
  }, [pendingChatPrompt, closeDrawer]);

  const handleEmailSelect = useCallback(
    (id: string) => {
      selectEmail(id);
      if (isMobile) setMobileView("detail");
    },
    [selectEmail, isMobile]
  );

  const handleToolbarAction = useCallback(
    (action: string, email: Email | null) => {
      if (!email) return;
      switch (action) {
        case "delete":
          deleteEmail(email.id);
          break;
        case "archive":
          updateEmail(email.id, { folder: "archive" });
          break;
        case "flag":
          updateEmail(email.id, { isFlagged: !email.isFlagged });
          break;
        case "mark-read":
          updateEmail(email.id, { isRead: true });
          break;
        case "reply": {
          const quoteSrc = email.bodyText || email.snippet || "";
          openCompose({
            to: email.from.email,
            subject: email.subject.startsWith("Re:") ? email.subject : `Re: ${email.subject}`,
            quote: `On ${email.receivedAt}, ${email.from.name} wrote:\n> ${quoteSrc.replace(/\n/g, "\n> ")}`,
            replyToMessageId: email.providerMessageId,
          });
          break;
        }
        case "reply-all": {
          const quoteSrc = email.bodyText || email.snippet || "";
          const allTo = [email.from.email, ...(email.to || []).filter(t => t.email !== email.from.email).map(t => t.email)].join(", ");
          openCompose({
            to: allTo,
            subject: email.subject.startsWith("Re:") ? email.subject : `Re: ${email.subject}`,
            quote: `On ${email.receivedAt}, ${email.from.name} wrote:\n> ${quoteSrc.replace(/\n/g, "\n> ")}`,
            replyToMessageId: email.providerMessageId,
          });
          break;
        }
        case "forward": {
          const quoteSrc = email.bodyText || email.snippet || "";
          openCompose({
            to: "",
            subject: email.subject.startsWith("Fwd:") ? email.subject : `Fwd: ${email.subject}`,
            quote: `---------- Forwarded message ----------\nFrom: ${email.from.name} <${email.from.email}>\nDate: ${email.receivedAt}\nSubject: ${email.subject}\n\n${quoteSrc}`,
          });
          break;
        }
        // move, label - will need folder/label picker (future)
      }
    },
    [deleteEmail, updateEmail, openCompose]
  );

  const handleBack = () => setMobileView("inbox");

  // ── Keyboard shortcuts (Gmail/Superhuman style) ──
  const navigateList = useCallback(
    (dir: 1 | -1) => {
      if (emails.length === 0) return;
      const idx = emails.findIndex((e) => e.id === selectedEmailId);
      const next =
        idx === -1 ? 0 : Math.min(emails.length - 1, Math.max(0, idx + dir));
      handleEmailSelect(emails[next].id);
    },
    [emails, selectedEmailId, handleEmailSelect]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Command palette — works even while typing in a field.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.key === "Escape") {
        setPaletteOpen(false);
        return;
      }
      // Don't hijack typing, modifier combos, or when a modal owns the keys.
      const t = e.target as HTMLElement | null;
      const typing =
        !!t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable);
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (composeOpen || showAddModal || showOnboarding || paletteOpen) return;
      // An automation scene (Assistant / Chat / Email Cleaner / …) replaces the
      // inbox panes and owns its own shortcuts — don't act on the background
      // selectedEmail while one is open.
      if (automationFeature) return;

      const em = selectedEmail;
      switch (e.key) {
        case "c":
          e.preventDefault();
          openCompose();
          break;
        case "j":
          e.preventDefault();
          navigateList(1);
          break;
        case "k":
          e.preventDefault();
          navigateList(-1);
          break;
        case "r":
          if (em) { e.preventDefault(); handleToolbarAction("reply", em); }
          break;
        case "a":
          if (em) { e.preventDefault(); handleToolbarAction("reply-all", em); }
          break;
        case "f":
          if (em) { e.preventDefault(); handleToolbarAction("forward", em); }
          break;
        case "e":
          if (em) { e.preventDefault(); updateEmail(em.id, { folder: "archive" }); }
          break;
        case "s":
          if (em) { e.preventDefault(); updateEmail(em.id, { isStarred: !em.isStarred }); }
          break;
        case "u":
          if (em) { e.preventDefault(); updateEmail(em.id, { isRead: !em.isRead }); }
          break;
        case "#":
        case "Delete":
          if (em) { e.preventDefault(); deleteEmail(em.id); }
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    selectedEmail, navigateList, openCompose, handleToolbarAction,
    updateEmail, deleteEmail, composeOpen, showAddModal, showOnboarding,
    paletteOpen, automationFeature,
  ]);

  // Command palette entries (Cmd/Ctrl+K).
  const commands = useMemo<Command[]>(() => {
    const cmds: Command[] = [
      { id: "compose", label: "Compose new email", hint: "C", run: () => openCompose() },
    ];
    const em = selectedEmail;
    if (em) {
      cmds.push(
        { id: "reply", label: "Reply", hint: "R", run: () => handleToolbarAction("reply", em) },
        { id: "reply-all", label: "Reply all", hint: "A", run: () => handleToolbarAction("reply-all", em) },
        { id: "forward", label: "Forward", hint: "F", run: () => handleToolbarAction("forward", em) },
        { id: "archive", label: "Archive", hint: "E", run: () => updateEmail(em.id, { folder: "archive" }) },
        { id: "delete", label: "Delete", hint: "#", run: () => deleteEmail(em.id) },
        { id: "star", label: em.isStarred ? "Remove star" : "Star", hint: "S", run: () => updateEmail(em.id, { isStarred: !em.isStarred }) },
        { id: "read", label: em.isRead ? "Mark as unread" : "Mark as read", hint: "U", run: () => updateEmail(em.id, { isRead: !em.isRead }) },
        { id: "flag", label: em.isFlagged ? "Clear flag" : "Flag / mark important", run: () => updateEmail(em.id, { isFlagged: !em.isFlagged }) },
      );
    }
    for (const f of folders) {
      cmds.push({ id: `go-${f.key}`, label: `Go to ${f.label}`, run: () => handleFolderSelect(f.key) });
    }
    if (selectedAccountId) {
      cmds.push({ id: "sync", label: "Sync now", run: () => triggerSync(selectedAccountId) });
    }
    return cmds;
  }, [
    selectedEmail, folders, selectedAccountId, openCompose,
    handleToolbarAction, updateEmail, deleteEmail, handleFolderSelect, triggerSync,
  ]);

  // ── Render ──

  return (
    <div className="flex h-full w-full bg-background overflow-hidden select-none">
      {/* Loading overlay */}
      {accountsLoading && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/80">
          <div className="flex gap-2 items-center text-sm text-muted-foreground">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            Loading accounts…
          </div>
        </div>
      )}

      {/* Onboarding modal — shown when no accounts exist */}
      {showOnboarding && !accountsLoading && accounts.length === 0 && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-background/85 backdrop-blur-sm">
          <div className="bg-card border border-border rounded-2xl shadow-2xl p-6 w-full max-w-md mx-4 chat-fade-in max-h-[90vh] overflow-y-auto">
            {/* Header */}
            <div className="text-center mb-5">
              <div className="w-12 h-12 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto mb-3">
                <AppIcon name="Mail" className="w-6 h-6 text-primary" />
              </div>
              <h2 className="text-lg font-semibold text-foreground">Welcome to Email</h2>
              <p className="text-sm text-muted-foreground mt-1">
                A few setup steps to get your inbox connected.
              </p>
            </div>

            {/* Steps */}
            <div className="space-y-4 mb-5">
              {/* Step 1: OAuth */}
              <div className={`p-3 rounded-xl border ${oauthStatus.gmail && oauthStatus.microsoft ? "border-emerald-500/20 bg-emerald-500/5" : "border-amber-500/20 bg-amber-500/5"}`}>
                <div className="flex items-start gap-3">
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5 text-xs font-bold ${oauthStatus.gmail && oauthStatus.microsoft ? "bg-emerald-500/20 text-emerald-400" : "bg-amber-500/20 text-amber-400"}`}>
                    {oauthStatus.gmail && oauthStatus.microsoft ? <AppIcon name="CheckCircle2" size={14} /> : "1"}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground">
                      {oauthStatus.gmail && oauthStatus.microsoft
                        ? "OAuth configured"
                        : "Configure OAuth (one-time)"}
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {oauthStatus.gmail && oauthStatus.microsoft
                        ? "Google + Microsoft OAuth are ready."
                        : "Register Metorite with Google and Microsoft so you can sign in with Gmail or Outlook."}
                    </p>
                    {(!oauthStatus.gmail || !oauthStatus.microsoft) && (
                      <a
                        href="/integrations?tab=apis&search=OAuth"
                        className="inline-flex items-center gap-1 text-xs text-primary hover:opacity-80 mt-1.5 transition-opacity"
                      >
                        <AppIcon name="Settings" size={11} /> Open setup guides
                        <AppIcon name="ExternalLink" size={10} />
                      </a>
                    )}
                  </div>
                </div>
              </div>

              {/* Step 2: Connect account */}
              <div className="p-3 rounded-xl border border-border bg-secondary/30">
                <div className="flex items-start gap-3">
                  <div className="w-6 h-6 rounded-full bg-muted flex items-center justify-center shrink-0 mt-0.5 text-xs font-bold text-muted-foreground">
                    2
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground">
                      Connect your first account
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Choose a provider below. IMAP works immediately — no OAuth setup needed.
                    </p>
                    <div className="flex flex-wrap gap-2 mt-2">
                      <button
                        onClick={() => handleConnect("gmail")}
                        disabled={!oauthStatus.gmail}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                        title={!oauthStatus.gmail ? "Configure Gmail OAuth first (Step 1)" : "Connect Gmail account"}
                      >
                        <span className="w-4 h-4 rounded-full bg-red-500/15 text-red-400 flex items-center justify-center text-[9px] font-bold">G</span>
                        Gmail
                      </button>
                      <button
                        onClick={() => handleConnect("microsoft")}
                        disabled={!oauthStatus.microsoft}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                        title={!oauthStatus.microsoft ? "Configure Microsoft OAuth first (Step 1)" : "Connect Outlook account"}
                      >
                        <span className="w-4 h-4 rounded-full bg-blue-500/15 text-blue-400 flex items-center justify-center text-[9px] font-bold">M</span>
                        Outlook
                      </button>
                      <button
                        onClick={() => handleConnect("imap")}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs hover:bg-secondary transition-colors"
                      >
                        <span className="w-4 h-4 rounded-full bg-amber-500/15 text-amber-400 flex items-center justify-center text-[9px] font-bold">IM</span>
                        IMAP/SMTP
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex gap-2">
              <button
                onClick={dismissOnboarding}
                className="flex-1 py-2 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors"
              >
                Maybe later
              </button>
              <a
                href="/integrations?tab=email"
                className="flex-1 py-2 rounded-lg bg-primary hover:opacity-90 text-xs font-medium text-primary-foreground text-center transition-colors flex items-center justify-center gap-1.5"
              >
                Manage in Integrations
                <AppIcon name="ArrowRight" size={12} />
              </a>
            </div>
          </div>
        </div>
      )}

      {/* Error banner — only for non-onboarding errors */}
      {error && accounts.length > 0 && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-50 bg-destructive text-destructive-foreground text-xs px-4 py-2 rounded-lg shadow-lg flex items-center gap-2">
          <span>{error}</span>
          <button
            onClick={() => useEmailStore.getState().clearError()}
            className="underline hover:no-underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ═══ DESKTOP: Left sidebar — accounts + folders ═══ */}
      {!isMobile && (
        <div
          className={`flex-shrink-0 border-r border-border transition-all duration-200 overflow-hidden ${
            leftOpen ? "w-56" : "w-0"
          }`}
        >
          {leftOpen && (
            <AccountSidebar
              accounts={accounts}
              selectedAccountId={selectedAccountId ?? ""}
              onAccountSelect={selectAccount}
              folders={folders.length > 0 ? folders : []}
              selectedFolder={selectedFolder}
              onFolderSelect={handleFolderSelect}
              onAddAccount={handleAddAccount}
              onSetDefault={setDefaultAccount}
              onOpenAutomation={handleOpenAutomation}
              activeAutomation={automationFeature}
            />
          )}
        </div>
      )}

      {/* ═══ Email Automation overlay (replaces panes, keeps left sidebar) ═══ */}
      {automationFeature === "chat" ? (
        // Chat is a full scene (like Assistant / Reply Zero), not a side rail.
        <div className="flex-1 min-w-0 overflow-hidden">
          <EmailAssistantChat
            selectedAccountId={selectedAccountId}
            selectedEmailId={selectedEmailId}
            onClose={() => setAutomationFeature(null)}
          />
        </div>
      ) : automationFeature ? (
        <div className="flex-1 min-w-0 overflow-hidden">
          <AutomationView
            feature={automationFeature}
            accountId={selectedAccountId}
            selectedEmailId={selectedEmailId}
            onClose={() => setAutomationFeature(null)}
            onArchived={fetchEmails}
            onNavigate={setAutomationFeature}
            onOpenEmail={(id) => {
              // A dashboard row navigates to its conversation: fetch/select the
              // message (it may live outside the loaded folder) and drop back
              // to the mailbox so the reading pane is visible.
              useEmailStore.getState().openEmailById(id);
              setAutomationFeature(null);
            }}
            onFilterLabel={(label) => {
              // Category chip → the inbox filtered to that label (same path a
              // label chip in the list uses), dropping back to the mailbox.
              useEmailStore.getState().selectLabel(label);
              setAutomationFeature(null);
            }}
            onFilterSender={(email) => {
              // Noisy-sender row → the mailbox filtered to that sender, so the
              // user can triage/unsubscribe from the actual mail.
              useEmailStore
                .getState()
                .setSearchFilters([{ kind: "from", value: email }]);
              setAutomationFeature(null);
            }}
            onDraftReply={async (id) => {
              // ✍️ on a needs-reply row: open the thread, then hand the viewer a
              // "reply-ai" command so it opens the composer AND drafts a reply.
              // openEmailById is awaited so the message is loaded before the
              // command fires (the viewer binds its reply handler to the open
              // message). openEmailById clears viewerCommand, so it must be set
              // after the await, not before.
              await useEmailStore.getState().openEmailById(id);
              setAutomationFeature(null);
              useEmailStore.getState().setViewerCommand("reply-ai");
            }}
            onNudge={async (id) => {
              // 🔔 on a waiting-on-them row: same flow as a draft, but the
              // "nudge" command opens a reply-all and seeds a follow-up prompt.
              await useEmailStore.getState().openEmailById(id);
              setAutomationFeature(null);
              useEmailStore.getState().setViewerCommand("nudge");
            }}
          />
        </div>
      ) : (
      <>
      {/* ═══ Main area ═══ */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* ── DESKTOP top bar ──
            Three tracks: identity (panes + folder) left, SEARCH centred, actions
            right. Both side tracks are basis-0 + grow, so free space splits
            evenly and the search sits on the bar's true centre.
            The side tracks' minimums matter: the actions are fixed-size icons
            that cannot shrink, so that track is min-w-fit and always reserves
            its width — otherwise the expanded search bar squeezes the track and
            the icons overflow ON TOP of it, swallowing its clicks. The left
            track keeps min-w-0 so a long folder name truncates instead. When
            space runs out the search bar gives way (it is the only shrinkable
            track), so centring degrades gracefully rather than overlapping. */}
        {!isMobile && (
          <div className="flex items-center gap-3 px-3 py-2 border-b border-border flex-shrink-0 bg-card">
            <div className="flex items-center gap-1.5 flex-1 basis-0 min-w-0">
              <IconBtn
                icon={leftOpen ? themedIcon("PanelLeftClose") : themedIcon("PanelLeftOpen")}
                label={leftOpen ? "Hide accounts" : "Show accounts"}
                onClick={() => setLeftOpen((v) => !v)}
              />
              <div className="w-px h-4 bg-border" />
              <IconBtn
                icon={themedIcon("Columns2")}
                label={listOpen ? "Hide email list" : "Show email list"}
                onClick={() => setListOpen((v) => !v)}
                active={listOpen}
              />
              <div className="w-px h-4 bg-border" />
              <div className="flex items-center gap-1.5 ml-1 min-w-0">
                <h1 className="text-sm font-medium text-foreground truncate">
                  {folderLabel(selectedFolder)}
                </h1>
                {unreadCount > 0 && (
                  <span className="text-[10px] px-1.5 py-0.5 bg-primary/15 text-primary rounded-full flex-shrink-0">
                    {unreadCount} unread
                  </span>
                )}
              </div>
            </div>

            <div className="flex justify-center flex-shrink min-w-0">
              <SearchBar />
            </div>

            <div className="flex items-center gap-1 flex-1 basis-0 justify-end min-w-fit">
              <MailboxActions selectedEmail={selectedEmail} />
              <div className="w-px h-4 bg-border" />
              <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={() => setPaletteOpen(true)} title="Command palette (Ctrl/Cmd+K)" className="gap-1 px-2 py-1 rounded">
                <span className="text-[10px] border border-border rounded px-1 leading-tight">
                  ⌘K
                </span>
              </Button>
            </div>
          </div>
        )}

        {/* ── MOBILE: detail top bar (back button) ── */}
        {isMobile && mobileView === "detail" && selectedEmail && (
          <div className="flex items-center gap-2 px-2 py-2 border-b border-border flex-shrink-0 bg-card">
            <Button variant="ghost" size="icon-sm" radius="keep" layout="" onClick={handleBack} aria-label="Back to inbox" className="rounded">
              <AppIcon name="ArrowLeft" size={16} />
            </Button>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-foreground truncate">
                {selectedEmail.subject}
              </div>
              <div className="text-[10px] text-muted-foreground truncate">
                {selectedEmail.from.name}
              </div>
            </div>
          </div>
        )}

        {/* ── MOBILE: inbox top bar ── */}
        {isMobile && mobileView === "inbox" && (
          <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0 bg-card">
            <button
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent("cc-mobile-nav", { detail: "email-accounts" })
                );
              }}
              className="flex items-center gap-2 min-w-0 hover:opacity-80 transition-opacity"
            >
              {selectedAccount && (
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0 text-[10px] font-semibold"
                  style={{ backgroundColor: selectedAccount.color }}
                >
                  {selectedAccount.avatar}
                </div>
              )}
              <div className="min-w-0">
                <div className="text-xs font-medium text-foreground">
                  {folderLabel(selectedFolder)}
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {selectedAccount?.emailAddress ?? ""}
                </div>
              </div>
            </button>
            <div className="flex items-center gap-1">
              {unreadCount > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 bg-primary/15 text-primary rounded-full">
                  {unreadCount} unread
                </span>
              )}
              <Button variant="ghost" size="icon-sm" layout="" onClick={() => selectedAccountId && triggerSync(selectedAccountId)} disabled={!selectedAccountId || syncing} aria-label="Refresh" title="Refresh">
                <AppIcon name="RefreshCw" size={16} className={syncing ? "animate-spin" : ""} />
              </Button>
            </div>
          </div>
        )}

        {/* ── MOBILE: search ──
            Its own row: the top bar has no room to centre a scope dropdown,
            pills and an input beside the account chip. */}
        {isMobile && mobileView === "inbox" && (
          <div className="px-3 py-2 border-b border-border flex-shrink-0 bg-card">
            <SearchBar />
          </div>
        )}

        {/* ── Reconnect banner: account auth/sync is failing ── */}
        {selectedAccount &&
          (selectedAccount.syncStatus === "error" || authErrors[selectedAccount.id]) && (
          <div className="flex items-start gap-2 px-3 py-2 border-b border-amber-500/30 bg-amber-500/10 flex-shrink-0">
            <AppIcon name="AlertCircle" size={14} className="text-amber-400 mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-foreground">
                <span className="font-medium">{selectedAccount.emailAddress}</span> can&apos;t
                reach the provider — message bodies, folders and statuses may be stale.
              </p>
              <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                {selectedAccount.syncError || authErrors[selectedAccount.id] || "The connection may have expired. Reconnect to restore full access."}
              </p>
            </div>
            {(selectedAccount.provider === "gmail" || selectedAccount.provider === "microsoft") ? (
              <button
                onClick={() => handleConnect(selectedAccount.provider as "gmail" | "microsoft")}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 text-[11px] font-medium transition-colors flex-shrink-0"
              >
                <AppIcon name="ExternalLink" size={11} /> Reconnect
              </button>
            ) : (
              <a
                href="/integrations?tab=email"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 text-[11px] font-medium transition-colors flex-shrink-0"
              >
                <AppIcon name="ExternalLink" size={11} /> Fix in Integrations
              </a>
            )}
          </div>
        )}

        {/* ── Unified action toolbar — spans the list + viewer columns, just
            below the top bar (desktop only; mobile keeps per-view toolbars). ── */}
        {!isMobile && <EmailToolbar />}

        {/* ── Quick-filter chips — one-click triage buckets (Needs reply,
            Follow-up, Newsletter, …) that replaced the old Rapid Inbox view.
            Hidden on the mobile detail screen (no list to filter there). ── */}
        {(!isMobile || mobileView === "inbox") && <QuickFilters />}

        {/* ── Content: email list + detail ── */}
        <div className="flex-1 flex min-w-0 overflow-hidden">
          {/* DESKTOP: email list pane */}
          {!isMobile && (
            <div
              className={`flex-shrink-0 border-r border-border transition-all duration-200 overflow-hidden ${
                listOpen ? "w-96" : "w-0"
              }`}
            >
              {listOpen && (
                <EmailList
                  emails={emails}
                  selectedId={selectedEmailId}
                  onSelect={handleEmailSelect}
                  onCompose={() => openCompose()}
                  onToolbarAction={handleToolbarAction}
                  loading={emailsLoading}
                  total={emailsTotal}
                  onLoadMore={loadMoreEmails}
                  loadingMore={loadingMore}
                  onBackfill={backfillOlder}
                  backfilling={backfilling}
                  canBackfill={canBackfillFolder}
                />
              )}
            </div>
          )}

          {/* MOBILE: email list (full width) */}
          {isMobile && mobileView === "inbox" && (
            <div className="flex-1 min-w-0 overflow-y-auto">
              <EmailList
                emails={emails}
                selectedId={selectedEmailId}
                onSelect={handleEmailSelect}
                onCompose={() => openCompose()}
                onToolbarAction={handleToolbarAction}
                loading={emailsLoading}
                total={emailsTotal}
                onLoadMore={loadMoreEmails}
                loadingMore={loadingMore}
                onBackfill={backfillOlder}
                backfilling={backfilling}
                canBackfill={canBackfillFolder}
              />
            </div>
          )}

          {/* MOBILE: email detail (full width) */}
          {isMobile && mobileView === "detail" && (
            <div className="flex-1 min-w-0 overflow-y-auto">
              <EmailDetail email={selectedEmail} />
            </div>
          )}

          {/* DESKTOP: email detail pane */}
          {!isMobile && (
            <div className="flex-1 min-w-0 overflow-hidden">
              <EmailDetail email={selectedEmail} />
            </div>
          )}
        </div>
      </div>

      </>
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
      />

      {/* Add-to-Tasks clarify popup */}
      {taskCapturePopupEmailId && (() => {
        const popupEmail = emails.find((e) => e.id === taskCapturePopupEmailId);
        const acctId = popupEmail?.accountId ?? selectedAccountId;
        if (!acctId) return null;
        return (
          <TaskCaptureModal
            accountId={acctId}
            emailId={taskCapturePopupEmailId}
            onClose={closeTaskCapturePopup}
            onCaptured={notifyTaskCaptured}
          />
        );
      })()}

      {/* Captured-to-Tasks toast */}
      {taskCaptureNotice && (() => {
        const n = taskCaptureNotice;
        // A non-created notice with no task title is a plain message (draft/
        // error), not an "already in Tasks" idempotent hit.
        const isMessage = !n.created && !n.title.includes("“") &&
          (n.title.startsWith("Could not") || n.title.startsWith("Save"));
        // Friendly disposition label + why-it's-that.
        const dispLabel: Record<string, string> = {
          WAITING: n.assigneeName ? `Delegated to ${n.assigneeName}` : "Follow-up",
          NEXT: "Next action",
          CALENDAR: "Scheduled",
          SOMEDAY: "Someday",
          INBOX: "Inbox",
        };
        const disp = n.disposition ? dispLabel[n.disposition] : null;
        const due = n.dueAt
          ? new Date(n.dueAt).toLocaleDateString(undefined, {
              month: "short", day: "numeric",
            })
          : null;
        return (
          <div className="fixed bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px)+1rem)] sm:bottom-4 left-1/2 -translate-x-1/2 z-[80] flex items-center gap-3 bg-card border border-border shadow-xl rounded-lg px-4 py-2.5 text-xs">
            <span className="text-foreground">
              {isMessage ? (
                n.title
              ) : n.created ? (
                <>Captured to Tasks: &ldquo;{n.title}&rdquo;</>
              ) : (
                <>Already in Tasks: &ldquo;{n.title}&rdquo;</>
              )}
            </span>
            {n.created && disp && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 text-primary px-2 py-0.5 font-medium">
                {disp}
                {due ? ` · due ${due}` : ""}
              </span>
            )}
            <a href="/tasks" className="text-primary font-medium hover:opacity-80">
              Open Tasks
            </a>
            <button
              onClick={() => useEmailStore.getState().clearTaskCaptureNotice()}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        );
      })()}

      {/* Undo-send toast — on mobile, sits above the fixed bottom nav bar */}
      {pendingSend && (
        <div className="fixed bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px)+1rem)] sm:bottom-4 left-1/2 -translate-x-1/2 z-[80] flex items-center gap-3 bg-card border border-border shadow-xl rounded-lg px-4 py-2.5 text-xs">
          <span className="text-foreground">Sending message…</span>
          <button
            onClick={() => useEmailStore.getState().undoSend()}
            className="text-primary font-medium hover:opacity-80"
          >
            Undo
          </button>
        </div>
      )}

      <ComposePanel
        open={composeOpen}
        onClose={closeCompose}
        accountId={selectedAccountId ?? ""}
        onSend={async (params) => {
          if (!selectedAccountId) return;
          await sendEmail({
            accountId: selectedAccountId,
            ...params,
          });
        }}
        defaultTo={composeDefaults?.to}
        defaultSubject={composeDefaults?.subject}
        defaultCc={composeDefaults?.cc}
        replyToBody={composeDefaults?.replyToBody}
        quote={composeDefaults?.quote}
        replyToMessageId={composeDefaults?.replyToMessageId}
        messageId={composeDefaults?.messageId}
        initialAttachments={composeDefaults?.attachments}
        initialArtifacts={composeDefaults?.artifacts}
      />

      {/* Add Account Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowAddModal(false)} />
          <div className="relative bg-card border border-border rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-foreground">Add Email Account</h3>
              <button
                onClick={() => setShowAddModal(false)}
                className="p-1 rounded-md hover:bg-secondary text-muted-foreground transition-colors"
              >
                <AppIcon name="X" size={16} />
              </button>
            </div>
            <div className="space-y-2">
              <button
                onClick={() => { setShowAddModal(false); handleConnect("gmail"); }}
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-border hover:border-red-400/40 hover:bg-red-500/5 transition-colors text-left"
              >
                <span className="w-8 h-8 rounded-full bg-red-500/15 text-red-400 flex items-center justify-center text-xs font-bold">G</span>
                <div>
                  <div className="text-sm font-medium text-foreground">Google / Gmail</div>
                  <div className="text-[11px] text-muted-foreground">Sign in with Google OAuth</div>
                </div>
              </button>
              <button
                onClick={() => { setShowAddModal(false); handleConnect("microsoft"); }}
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-border hover:border-blue-400/40 hover:bg-blue-500/5 transition-colors text-left"
              >
                <span className="w-8 h-8 rounded-full bg-blue-500/15 text-blue-400 flex items-center justify-center text-xs font-bold">M</span>
                <div>
                  <div className="text-sm font-medium text-foreground">Microsoft / Outlook</div>
                  <div className="text-[11px] text-muted-foreground">Sign in with Microsoft OAuth</div>
                </div>
              </button>
              <button
                onClick={() => { setShowAddModal(false); handleConnect("imap"); }}
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-border hover:border-amber-400/40 hover:bg-amber-500/5 transition-colors text-left"
              >
                <span className="w-8 h-8 rounded-full bg-amber-500/15 text-amber-400 flex items-center justify-center text-xs font-bold">IM</span>
                <div>
                  <div className="text-sm font-medium text-foreground">IMAP / SMTP</div>
                  <div className="text-[11px] text-muted-foreground">Manual server configuration</div>
                </div>
              </button>
            </div>
            <p className="mt-4 text-[10px] text-muted-foreground text-center">
              Credentials are encrypted at rest with AES-256-GCM
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function IconBtn({
  icon: Icon,
  label,
  onClick,
  active,
}: {
  icon: React.ElementType;
  label: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`p-1.5 rounded transition-colors ${
        active === false
          ? "text-muted-foreground hover:text-foreground hover:bg-secondary"
          : active
            ? "text-primary bg-primary/10 hover:bg-primary/15"
            : "text-muted-foreground hover:text-foreground hover:bg-secondary"
      }`}
    >
      <Icon size={15} />
    </button>
  );
}
