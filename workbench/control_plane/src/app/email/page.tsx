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
import { ConnectChoices } from "./components/ConnectChoices";
import { ConnectEmptyState } from "./components/ConnectEmptyState";
import { DisconnectDialog } from "./components/DisconnectDialog";
import { MailboxAvatar, MailboxChip } from "./components/MailboxChip";
import { MailboxEditDialog, type MailboxEdit } from "./components/MailboxEditDialog";
import { updateEmailAccount } from "./lib/api";
import { FirstSyncBanner } from "./components/FirstSyncBanner";
import { OnboardingPanel } from "./components/OnboardingPanel";
import { OnboardingRulesStep } from "./components/OnboardingRulesStep";
import { RemoveOlderMailDialog } from "./components/RemoveOlderMailDialog";
import { StorageNotice } from "./components/StorageNotice";
import { StorageStep } from "./components/StorageStep";
import Modal from "@/components/ui/Modal";
import {
  useEmailStore, isRealFolder, backfillKey, foldersInScope, scopeBusy, ALL_INBOXES,
} from "./lib/emailStore";
import { Email, EmailAccount, AutomationFeature } from "./lib/types";
import {
  connectQuery,
  emailSurface,
  rangeStepProviderFrom,
  wantsConnectChoices,
  firstSyncTick,
  FIRST_SYNC_POLL_MS,
  reconnectProvider,
  rememberMailboxesBeforeConnect,
  shouldPollFirstSync,
  type ConnectProviderId,
} from "./lib/connect";
import { firstSyncPanels, importProgress, onboardingStage } from "./lib/onboarding";
import { pickSettingsMailbox } from "./lib/mailboxSettings";
import { folderLabel } from "./lib/utils";
import {
  attentionMailbox,
  ownAddresses,
  pooledMailboxes,
  removalMailbox,
  replyRecipients,
  storageMailbox,
} from "./lib/mailbox";
import { keepStorage, readStorageKept, withRemovalMeter } from "./lib/storage";
import { isSearchActive } from "./lib/searchFilters";

export default function EmailPage() {
  const { isMobile } = useViewMode();

  // Desktop UI state
  const [leftOpen, setLeftOpen] = useState(true);
  const [listOpen, setListOpen] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The mailbox the disconnect dialog asks about. `null` closes the dialog.
  const [disconnecting, setDisconnecting] = useState<EmailAccount | null>(null);
  // The mailbox whose name and colour the member edits (EM-T8b).
  const [editingMailbox, setEditingMailbox] = useState<EmailAccount | null>(null);
  // The id of the mailbox that "Remove older mail from Metorite" acts on
  // (EM-T6e). The notice or the step that names a mailbox sets it, never the
  // selection (D3). `null` closes the dialog.
  const [removingId, setRemovingId] = useState<string | null>(null);
  // The mailboxes that the member keeps at the storage limit ("Keep it as it
  // is", D6). Ids only, from `localStorage`. Read once on the client: the
  // step draws only after the first account read, so no server render
  // shows it.
  const [storageKept, setStorageKept] = useState<string[]>(() => readStorageKept());

  // Mobile-specific state
  const [mobileView, setMobileView] = useState<"inbox" | "detail">("inbox");

  // Email Automation overlay (Assistant / Unsubscribe / Archive / Analytics)
  const [automationFeature, setAutomationFeature] =
    useState<AutomationFeature | null>(null);
  // YYYY-MM-DD. The guided setup's "Sort my imported mail" opens AI Settings
  // with "Process past emails" on this date (EM-T6d). The Rules tab reports
  // when it opened the dialog, and the page clears the date then. So a return
  // to the Rules tab does not open the dialog again (fix round 1, P2). Every
  // other way into an automation view clears it too.
  const [processPastFrom, setProcessPastFrom] = useState<string | null>(null);

  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();

  // ── Zustand store ──
  const {
    accounts,
    emails,
    emailsTotal,
    folders,
    accountsLoading,
    accountsLoaded,
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
    refreshAccounts,
    fetchFolders,
    fetchEmails,
    loadMoreEmails,
    backfillOlder,
    selectAccount,
    setDefaultAccount,
    deleteAccount,
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
    replaceAccount,
    viewAll,
    selectAll,
    allFolderCounts,
    setInAllInboxes,
  } = useEmailStore();
  // The mailbox that the composer sends from: the mailbox of the mail a
  // reply answers, else the selected one (EM-T8a, D-EM-20). New mail in All
  // inboxes goes from the default mailbox (D-EM-20, D-EM-25).
  const defaultAccountId = accounts.find((a) => a.isDefault)?.id ?? null;
  const composeAccountId =
    composeDefaults?.accountId ||
    (viewAll ? defaultAccountId || selectedAccountId : selectedAccountId);

  // Automation acts on one mailbox, and All inboxes names none. So opening it
  // leaves All inboxes for the mailbox of the open mail, else the default, and
  // the switcher names it. The open mail stays open (EM-T8d review F4, F5).
  // The chat has its own All inboxes scope (EM-T8e-3), so it keeps the page in
  // All inboxes and gets `ALL_INBOXES` as its scope, never the hidden mailbox.
  useEffect(() => {
    if (!automationFeature || automationFeature === "chat" || !viewAll) return;
    const st = useEmailStore.getState();
    const open = st.selectedEmailOverride ?? st.emails.find((e) => e.id === st.selectedEmailId);
    const target = open?.accountId || defaultAccountId || st.selectedAccountId;
    if (!target) return;
    const keep = { selectedEmailId: st.selectedEmailId, selectedEmailOverride: st.selectedEmailOverride };
    st.selectAccount(target);
    if (open?.accountId === target) useEmailStore.setState(keep);
  }, [automationFeature, viewAll, defaultAccountId]);

  // Fetch on mount
  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  // /email?connect=1 opens the connect choices (the callback page's "Try
  // again" for a provider that is not live). With no mailbox, the empty state
  // already shows them.
  const connectParamRef = useRef(false);
  // /email?connect=1&provider=microsoft opens the range step of that
  // provider at once. The callback page's "Try again" sends it, because that
  // retry is a first connect and must carry the range (EM-T6d fix round 1).
  // Read once on the client. The connect choices mount only after the first
  // account read, so no server render shows them.
  const [rangeStepProvider, setRangeStepProvider] = useState<ConnectProviderId | null>(() =>
    typeof window === "undefined" ? null : rangeStepProviderFrom(window.location.search)
  );
  useEffect(() => {
    if (connectParamRef.current || accounts.length === 0) return;
    if (!wantsConnectChoices(window.location.search)) return;
    connectParamRef.current = true;
    setShowAddModal(true);
  }, [accounts.length]);

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
  // The count of the header in All inboxes. A separate mailbox is not in All
  // inboxes, so it does not count (EM-T8g-2 item 2).
  const pooledCount = pooledMailboxes(accounts).length;
  // The mailbox that the reconnect banner names: the selected one, or in All
  // inboxes the first mailbox that needs it, so a second mailbox cannot fail
  // out of sight (EM-T8d review). A separate mailbox counts too (EM-T8g-2).
  const attentionAccount = attentionMailbox({ viewAll, selectedAccountId, accounts, authErrors });
  // The guided setup of the mailbox in view, read ONCE for the storage step
  // and the rules step (EM-T6d, EM-T6e D6).
  const setupStage = selectedAccount
    ? onboardingStage(selectedAccount, { storageKept: storageKept.includes(selectedAccount.id) })
    : null;
  // The mailbox that the storage notice names (EM-T6e, D3): the one in view,
  // or in All inboxes the first pooled mailbox at the limit. The reconnect
  // banner wins, and so does the storage step of the setup.
  const storageAccount = storageMailbox({
    viewAll,
    selectedAccountId,
    accounts,
    attentionId: attentionAccount?.id ?? null,
    storageStepId: setupStage === "storage" ? selectedAccountId : null,
  });
  // The live store copy of the mailbox of the removal dialog. A disconnect
  // that takes the mailbox away closes the dialog. The page shortcuts read
  // this value, never `removingId` (review round 1).
  const removingAccount = removalMailbox(accounts, removingId);
  // The dialog of a mailbox that left the list draws nothing, so its
  // `onClose` never runs. Clear the id here, or a re-read that brings the
  // mailbox back would open the dialog again. This is the adjustment of
  // state during render that React documents. An effect would draw one
  // stale frame first, and the lint rule `set-state-in-effect` refuses it.
  if (removingId !== null && removingAccount === null) setRemovingId(null);
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
  // "Load older" pages one mailbox, so All inboxes offers none (EM-T8d).
  const canBackfillFolder =
    !viewAll &&
    isRealFolder(selectedFolder) &&
    !backfillExhausted[backfillKey(selectedAccountId, selectedFolder)];
  // "processing" (the background rules/labels pipeline after H1) counts as busy
  // too, so the top-bar refresh button keeps spinning until it settles.
  const syncing = scopeBusy({ viewAll, selectedAccountId, accounts, syncStatus });

  // Reset mobile view when folder/account changes. A mail that "Open in
  // inbox" opened in its OWN mailbox switches the account too, and it keeps
  // the detail view: the override marks that case (EM-T8a review).
  useEffect(() => {
    if (useEmailStore.getState().selectedEmailOverride) return;
    setMobileView("inbox");
  }, [selectedFolder, selectedAccountId, viewAll]);

  // ── No mailbox yet ──
  // The empty state replaces the panes. There is no setup step for a member:
  // the deployment owns the Microsoft app (EM-T3b, spec §10.3).
  const surface = emailSurface({
    loaded: accountsLoaded,
    loading: accountsLoading,
    count: accounts.length,
  });
  const noAccounts = surface === "empty";

  // Tell the mobile bottom bar, so it hides the email tabs that would open
  // empty sheets (AppShell listens for `cc-email-empty`).
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("cc-email-empty", { detail: noAccounts }));
  }, [noAccounts]);
  useEffect(
    () => () => {
      window.dispatchEvent(new CustomEvent("cc-email-empty", { detail: false }));
    },
    []
  );

  // ── First sync ──
  // A new mailbox reports initial_sync_done=false until its first sync ends.
  // Re-read the accounts quietly until no account is pending. An errored
  // account is not pending (isFirstSyncPending), so a failed first sync stops
  // the poll and the reconnect banner takes over. A hidden tab makes no
  // request, and the poll ticks once when the tab comes back. The interval
  // stops when the page unmounts.
  const firstSyncPending = shouldPollFirstSync(accounts);
  // One panel for each mailbox whose first sync runs, the mailbox in view
  // first. With two or more mailboxes each panel names its mailbox
  // (EM-T8f-3, §11.6 case 19).
  const importPanels = firstSyncPanels(accounts, viewAll ? null : selectedAccountId);
  useEffect(() => {
    if (!firstSyncPending) return;
    let cancelled = false;
    const tick = () =>
      void firstSyncTick({
        hidden: () => cancelled || document.hidden,
        before: () => useEmailStore.getState().accounts,
        refresh: refreshAccounts,
        selected: () => useEmailStore.getState().selectedAccountId,
        onFinished: (id) => {
          if (cancelled) return;
          void fetchFolders(id);
          void fetchEmails();
        },
      });
    const id = setInterval(tick, FIRST_SYNC_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [firstSyncPending, refreshAccounts, fetchFolders, fetchEmails]);

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

  // All inboxes (EM-T8d, D-EM-22).
  const handleSelectAll = useCallback(() => {
    selectAll();
    setMobileView("inbox");
    closeDrawer();
  }, [selectAll, closeDrawer]);

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

  const handleConnect = useCallback((provider: ConnectProviderId, loginHint?: string, importMonths?: number) => {
    // Navigate to the BFF, never straight at the gateway host. A top-level
    // navigation carries no Bearer and no X-User-Email (the session cookie is
    // on this origin, not api.*), so the gated authorize route 401s every
    // caller. api/email/oauth/[provider]/authorize runs server-side, attaches
    // the identity, and re-issues the provider redirect. Identity now comes
    // from the session on the server — never from a `user_email` parameter.
    //
    // `loginHint` is the mailbox address on a reconnect, so Microsoft opens
    // on the right account. It is a hint, never an identity (EM-T3a item 3).
    //
    // `importMonths` is the range of the first import, from the range step
    // (EM-T6d). The reconnect banner passes none, because a reconnect keeps
    // the range of the mailbox (D-EM-13).
    //
    // connectQuery uses URLSearchParams, which already percent-encodes —
    // don't pre-encode or redirect_after ends up double-encoded and the
    // callback treats it as a relative path (→ /email/oauth/https%3A%2F%2F… 404).
    const query = connectQuery(window.location.href, loginHint, importMonths);
    // An ADD (no hint) keeps the ids of the mailboxes, so the callback can
    // tell a mailbox that was already connected (EM-T8c, §11.6 case 2). A
    // deliberate reconnect names its mailbox and stores none.
    if (!loginHint) {
      rememberMailboxesBeforeConnect(useEmailStore.getState().accounts.map((a) => a.id));
    }
    window.location.href = `/api/email/oauth/${provider}/authorize?${query}`;
  }, []);

  const handleDisconnectRequest = useCallback(
    (account: EmailAccount) => {
      setDisconnecting(account);
      closeDrawer();
    },
    [closeDrawer]
  );

  // Rename and recolour a mailbox (EM-T8b, §11.7.2 item 5).
  const handleEditMailbox = useCallback(
    (account: EmailAccount) => {
      setEditingMailbox(account);
      closeDrawer();
    },
    [closeDrawer]
  );

  // The display label of ANOTHER mailbox can change with this one (§11.4: a
  // default steps aside for a chosen label), so the accounts are read again.
  const saveMailboxEdit = useCallback(
    async (id: string, edit: MailboxEdit): Promise<string | null> => {
      try {
        await updateEmailAccount(id, {
          ...(edit.label !== undefined ? { label: edit.label } : {}),
          colorSlot: edit.colorSlot,
        });
      } catch (e) {
        return e instanceof Error && e.message ? e.message : "The mailbox could not be saved.";
      }
      await refreshAccounts();
      return null;
    },
    [refreshAccounts]
  );

  // "Keep separate" and "Show in All inboxes" (EM-T8g-2 item 1). The store
  // sends the PATCH, and in All inboxes it reads the list again.
  const toggleSeparate = useCallback(
    (id: string, pooled: boolean) => {
      void setInAllInboxes(id, pooled);
    },
    [setInAllInboxes]
  );

  const handleAddAccount = useCallback(() => {
    setShowAddModal(true);
  }, []);

  const handleOpenAutomation = useCallback(
    (feature: AutomationFeature) => {
      setProcessPastFrom(null);
      setAutomationFeature(feature);
      if (isMobile) closeDrawer();
    },
    [isMobile, closeDrawer]
  );

  // The guided setup opens an automation view (EM-T6d). Only its "Sort my
  // imported mail" passes `pastFrom`, which opens Process past emails. The
  // Rules tab clears it through `onProcessPastOpened`.
  const openFromSetup = useCallback(
    (feature: AutomationFeature, pastFrom: string | null = null) => {
      setProcessPastFrom(pastFrom);
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
      onDisconnect={handleDisconnectRequest}
      onEditMailbox={handleEditMailbox}
      onToggleSeparate={toggleSeparate}
      viewAll={viewAll}
      folderSums={allFolderCounts}
      onSelectAll={handleSelectAll}
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
        // A reply and a forward send from the mailbox of the mail, never from
        // the selected view (EM-T8a, D-EM-19, edge case 25).
        case "reply": {
          const quoteSrc = email.bodyText || email.snippet || "";
          const accts = useEmailStore.getState().accounts;
          const { to } = replyRecipients(
            email, "reply", ownAddresses(accts),
            accts.find((a) => a.id === email.accountId)?.emailAddress);
          openCompose({
            accountId: email.accountId || undefined,
            to: to.join(", "),
            subject: email.subject.startsWith("Re:") ? email.subject : `Re: ${email.subject}`,
            quote: `On ${email.receivedAt}, ${email.from.name} wrote:\n> ${quoteSrc.replace(/\n/g, "\n> ")}`,
            replyToMessageId: email.providerMessageId,
          });
          break;
        }
        case "reply-all": {
          const quoteSrc = email.bodyText || email.snippet || "";
          // Each address of the member leaves the recipients (D-EM-27, MB-7).
          const accts = useEmailStore.getState().accounts;
          const { to, cc } = replyRecipients(
            email, "reply-all", ownAddresses(accts),
            accts.find((a) => a.id === email.accountId)?.emailAddress);
          openCompose({
            accountId: email.accountId || undefined,
            to: to.join(", "),
            cc: cc.length ? cc.join(", ") : undefined,
            subject: email.subject.startsWith("Re:") ? email.subject : `Re: ${email.subject}`,
            quote: `On ${email.receivedAt}, ${email.from.name} wrote:\n> ${quoteSrc.replace(/\n/g, "\n> ")}`,
            replyToMessageId: email.providerMessageId,
          });
          break;
        }
        case "forward": {
          const quoteSrc = email.bodyText || email.snippet || "";
          openCompose({
            accountId: email.accountId || undefined,
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
      if (composeOpen || showAddModal || noAccounts || disconnecting || editingMailbox || removingAccount || paletteOpen) return;
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
    updateEmail, deleteEmail, composeOpen, showAddModal, noAccounts,
    disconnecting, editingMailbox, removingAccount, paletteOpen, automationFeature,
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
    for (const f of foldersInScope(folders, viewAll)) {
      cmds.push({ id: `go-${f.key}`, label: `Go to ${f.label}`, run: () => handleFolderSelect(f.key) });
    }
    if (selectedAccountId) {
      cmds.push({
        id: "sync",
        label: viewAll ? "Sync each mailbox now" : "Sync now",
        run: () => useEmailStore.getState().syncScope(),
      });
    }
    return cmds;
  }, [
    selectedEmail, folders, selectedAccountId, openCompose, viewAll,
    handleToolbarAction, updateEmail, deleteEmail, handleFolderSelect,
  ]);

  // ── Render ──

  if (noAccounts) {
    return (
      <ConnectEmptyState
        onConnect={(provider, importMonths) => handleConnect(provider, undefined, importMonths)}
        initialProvider={rangeStepProvider}
        loadError={error}
        onRetry={() => void fetchAccounts()}
      />
    );
  }

  return (
    <div className="flex h-full w-full bg-background overflow-hidden select-none">
      {/* Loading overlay */}
      {surface === "loading" && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/80">
          <div className="flex gap-2 items-center text-sm text-muted-foreground">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            Loading accounts…
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
              onDisconnect={handleDisconnectRequest}
              onEditMailbox={handleEditMailbox}
              onToggleSeparate={toggleSeparate}
              viewAll={viewAll}
              folderSums={allFolderCounts}
              onSelectAll={handleSelectAll}
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
            pageScope={viewAll ? ALL_INBOXES : selectedAccountId}
            selectedEmailId={selectedEmailId}
            onClose={() => setAutomationFeature(null)}
          />
        </div>
      ) : automationFeature ? (
        <div className="flex-1 min-w-0 overflow-hidden">
          <AutomationView
            feature={automationFeature}
            accountId={selectedAccountId}
            accounts={accounts}
            onPickMailbox={(id) => {
              // The header picker (EM-T8f-2, MB-11). It selects through the
              // store, because RulesTab reads the folders of the selected
              // mailbox. The Process past date belongs to the mailbox of the
              // setup, so a pick clears it.
              pickSettingsMailbox(id, {
                accounts,
                current: selectedAccountId,
                selectAccount,
                clearProcessPastFrom: () => setProcessPastFrom(null),
              });
            }}
            selectedEmailId={selectedEmailId}
            onClose={() => setAutomationFeature(null)}
            onArchived={fetchEmails}
            onNavigate={(feature) => {
              setProcessPastFrom(null);
              setAutomationFeature(feature);
            }}
            processPastFrom={processPastFrom}
            onProcessPastOpened={() => setProcessPastFrom(null)}
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
              // A label belongs to the mailbox of the dashboard, so All inboxes
              // opens that mailbox first (EM-T8d review).
              const st = useEmailStore.getState();
              if (st.viewAll && st.selectedAccountId) st.selectAccount(st.selectedAccountId);
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
                {/* The scope of the list, for two or more mailboxes (§11.4). */}
                {accounts.length > 1 && (viewAll ? (
                  <span className="text-[11px] text-muted-foreground truncate flex-shrink-0">
                    All inboxes · {pooledCount} mailboxes
                  </span>
                ) : selectedAccount ? (
                  <>
                    <MailboxChip account={selectedAccount} />
                    <span className="text-[11px] text-muted-foreground truncate">
                      {selectedAccount.emailAddress}
                    </span>
                  </>
                ) : null)}
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
              {viewAll ? (
                <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground">
                  <AppIcon name="Inbox" size={13} />
                </span>
              ) : selectedAccount ? (
                <MailboxAvatar account={selectedAccount} />
              ) : null}
              <div className="min-w-0">
                <div className="text-xs font-medium text-foreground">
                  {folderLabel(selectedFolder)}
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {viewAll ? `All inboxes · ${pooledCount} mailboxes` : selectedAccount?.emailAddress ?? ""}
                </div>
              </div>
            </button>
            <div className="flex items-center gap-1">
              {unreadCount > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 bg-primary/15 text-primary rounded-full">
                  {unreadCount} unread
                </span>
              )}
              <Button variant="ghost" size="icon-sm" layout="" onClick={() => useEmailStore.getState().syncScope()} disabled={!selectedAccountId || syncing} aria-label="Refresh" title="Refresh">
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

        {/* ── Reconnect banner: account auth/sync is failing ──
            In All inboxes it names the first mailbox that needs it, so a
            second mailbox cannot fail out of sight (EM-T8d review). */}
        {attentionAccount && (
          <div className="flex items-start gap-2 px-3 py-2 border-b border-warning/30 bg-warning/10 flex-shrink-0">
            <AppIcon name="AlertCircle" size={14} className="text-warning mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs text-foreground">
                <span className="font-medium">{attentionAccount.emailAddress}</span>{" "}can&apos;t
                reach the provider — message bodies, folders and statuses may be stale.
              </p>
              <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                {attentionAccount.syncError || authErrors[attentionAccount.id] || "The connection may have expired. Reconnect to restore full access."}
              </p>
            </div>
            {(() => {
              const provider = reconnectProvider(attentionAccount);
              if (!provider) {
                return (
                  <span className="text-[11px] text-muted-foreground flex-shrink-0 max-w-[40%]">
                    Disconnect it from the account menu, then connect it again.
                  </span>
                );
              }
              return (
                <Button
                  variant="secondary"
                  size="sm"
                  icon="RefreshCw"
                  className="flex-shrink-0"
                  onClick={() => handleConnect(provider, attentionAccount.emailAddress)}
                >
                  {provider === "microsoft" ? "Reconnect Outlook" : "Reconnect Gmail"}
                </Button>
              );
            })()}
          </div>
        )}

        {/* ── Storage notice (EM-T6e, D2 and D3) ──
            Below the reconnect banner, which wins for its own mailbox. The
            action opens the dialog for the mailbox that the notice NAMES. */}
        {storageAccount && (
          <StorageNotice
            // A prefix: the rules step beside it keys on the bare id, and
            // after "Keep it as it is" both draw for one mailbox.
            key={`storage-notice-${storageAccount.id}`}
            account={storageAccount}
            named={accounts.length > 1}
            onRemove={() => setRemovingId(storageAccount.id)}
          />
        )}

        {/* ── First sync of a new mailbox, one panel for each ──
            The import panel draws only when the gateway reports progress
            (`import_phase`, EM-T6b). Every other pending mailbox keeps the
            banner: one from before EM-T6, or a gateway before EM-T6b. */}
        {importPanels.map(({ account, surface, named }) =>
          surface === "progress" ? (
            <OnboardingPanel
              key={account.id}
              address={account.emailAddress}
              mailbox={named ? account : undefined}
              progress={importProgress(account, { now: new Date() })}
            />
          ) : (
            <FirstSyncBanner
              key={account.id}
              address={account.emailAddress}
              mailbox={named ? account : undefined}
            />
          ),
        )}

        {/* ── The storage step of the guided setup (EM-T6e, D6) ──
            For the mailbox in view, when its import stopped at the limit and
            the meter is still at it. "Keep it as it is" moves it on to the
            rules step. Not a modal. */}
        {selectedAccount && setupStage === "storage" && (
          <StorageStep
            key={`storage-step-${selectedAccount.id}`}
            account={selectedAccount}
            named={accounts.length > 1}
            onRemove={() => setRemovingId(selectedAccount.id)}
            onKeep={() => setStorageKept(keepStorage(selectedAccount.id))}
          />
        )}

        {/* ── The rules step of the guided setup (EM-T6d items 8 to 11) ──
            For the mailbox in view, once its import ended. Not a modal. */}
        {selectedAccount && setupStage === "rules" && (
          <OnboardingRulesStep
            key={selectedAccount.id}
            account={selectedAccount}
            onOpenAutomation={openFromSetup}
            onFinished={(updated) => {
              // The server's copy first, so a failed re-read cannot bring
              // the setup back (fix round 1, P3).
              replaceAccount(updated);
              void refreshAccounts();
            }}
            mailboxes={accounts}
          />
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
                <>Captured to My Tasks: &ldquo;{n.title}&rdquo;</>
              ) : (
                <>Already in My Tasks: &ldquo;{n.title}&rdquo;</>
              )}
            </span>
            {n.created && disp && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 text-primary px-2 py-0.5 font-medium">
                {disp}
                {due ? ` · due ${due}` : ""}
              </span>
            )}
            <a href="/tasks" className="text-primary font-medium hover:opacity-80">
              Open My Tasks
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
        accountId={composeAccountId ?? ""}
        defaultFromId={composeDefaults?.fromAccountId}
        onSend={async (params) => {
          // The From row of the composer chose the mailbox (EM-T8c).
          const sender = params.accountId || composeAccountId;
          if (!sender) return;
          await sendEmail({ ...params, accountId: sender });
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

      {/* Add a mailbox — the same choices as the empty state */}
      <Modal
        open={showAddModal}
        onClose={() => {
          setShowAddModal(false);
          // The next "Add account" opens the list, not the retry's step.
          setRangeStepProvider(null);
        }}
        title="Connect a mailbox"
        icon="Mail"
        size="sm"
      >
        <div className="px-3 py-3">
          <ConnectChoices
            initialProvider={rangeStepProvider}
            onConnect={(provider, importMonths) => {
              setShowAddModal(false);
              handleConnect(provider, undefined, importMonths);
            }}
          />
        </div>
      </Modal>

      <MailboxEditDialog
        account={editingMailbox}
        onSave={saveMailboxEdit}
        onClose={() => setEditingMailbox(null)}
      />

      {/* "Remove older mail from Metorite" (EM-T6e). Both routes take the id
          of the mailbox that the notice or the step named (D3). */}
      <RemoveOlderMailDialog
        account={removingAccount}
        named={accounts.length > 1}
        onClose={() => setRemovingId(null)}
        onRemoved={(id, result) => {
          // The meter of the answer first, so a failed re-read still shows
          // it (A14). The store is read at call time, because a removal can
          // run for two minutes and the list can change meanwhile.
          const current = useEmailStore.getState().accounts.find((a) => a.id === id);
          if (current) replaceAccount(withRemovalMeter(current, result));
          void refreshAccounts();
        }}
        onRefresh={async (id) => (await refreshAccounts())?.find((a) => a.id === id) ?? null}
      />

      <DisconnectDialog
        account={disconnecting}
        accounts={accounts}
        onDisconnect={deleteAccount}
        onClose={() => setDisconnecting(null)}
      />
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
