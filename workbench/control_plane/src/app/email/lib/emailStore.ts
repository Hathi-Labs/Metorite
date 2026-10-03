import { create } from "zustand";
import { Email, EmailAccount, EmailFolder, EMAIL_CATEGORIES, RunMessageResult } from "./types";
import * as api from "./api";
import type { EmailFolderRaw } from "./api";
import {
  SearchFilter,
  filterKey,
  isSearchActive,
  toSearchParams,
} from "./searchFilters";
import { QUICK_ACTIONS, MOCK_ACCOUNTS, MOCK_EMAILS, MOCK_FOLDERS } from "./mockData";
import { splitQuotedText } from "./quoting";
import { disconnectFailureText, type DisconnectOutcome } from "./connect";
import { nextDefaultAfter } from "./mailboxSettings";
import {
  hasAllInboxes,
  isSeparate,
  mailboxToOpen,
  poolHome,
  pooledMailboxes,
  withPoolFlag,
  type PoolFlag,
} from "./mailbox";

/**
 * Dev-only demo mode. With NEXT_PUBLIC_EMAIL_DEMO=1 (set in .env.local) the
 * store falls back to the bundled mock accounts/emails whenever the backend is
 * unreachable or returns nothing — so the email UI is explorable without a
 * connected mailbox.
 *
 * Double-gated: it requires BOTH a non-production build AND the explicit flag.
 * In a production build `process.env.NODE_ENV === "production"`, so DEMO is a
 * compile-time `false` and the bundler dead-code-eliminates every demo branch
 * (and the mock-data imports). Dummy data can never reach a deployment.
 */
const DEMO =
  process.env.NODE_ENV !== "production" &&
  process.env.NEXT_PUBLIC_EMAIL_DEMO === "1";

/** Mock messages for an account, scoped to the active folder (demo mode only). */
function demoEmailsFor(accountId: string | null, folder: string): Email[] {
  return MOCK_EMAILS.filter((e) => {
    if (accountId && e.accountId !== accountId) return false;
    if (folder === "starred") return e.isStarred;
    if (folder === "snoozed")
      return !!e.snoozedUntil && new Date(e.snoozedUntil).getTime() > Date.now();
    if (folder === FOLDER_ALL) return !FOLDER_ALL_EXCLUDES.has(e.folder);
    return e.folder === folder;
  });
}

/**
 * Canonical folder keys shared with the backend (`providers/base.py`).
 * Provider folder names map onto these so the inbox/sent/etc. tabs line up
 * regardless of provider-specific naming ("Sent Items" vs "SENT").
 */
/** How many messages to request per page (and per "Load more" click). */
const PAGE_SIZE = 100;

const CANONICAL_ALIASES: Record<string, string> = {
  inbox: "inbox",
  "sent items": "sent", sentitems: "sent", "sent mail": "sent", sent: "sent",
  drafts: "drafts", draft: "drafts",
  "deleted items": "trash", deleteditems: "trash", trash: "trash", bin: "trash",
  archive: "archive",
  "junk email": "junk", junkemail: "junk", junk: "junk", spam: "junk",
};

function toCanonical(name: string): string {
  return CANONICAL_ALIASES[name.trim().toLowerCase()] ?? name.trim().toLowerCase();
}

/** The pseudo-folder spanning every folder of mail that ARRIVED. Not a real
 *  provider folder — the backend turns it into a "folder NOT IN (...)" scope
 *  (core.folder_scope). Note it is NOT the search bar's "All folders" scope:
 *  that one also spans sent mail, because searching is a hunt for a message
 *  rather than a view of the inbox. See core.FOLDER_ALL_SEARCH_EXCLUDES. */
export const FOLDER_ALL = "all";

/** Folders `all` deliberately leaves out — must mirror the backend's
 *  FOLDER_ALL_EXCLUDES, since this set both filters the list and derives the
 *  sidebar's All count (withAllCount); a copy that drifted would print a badge
 *  the list underneath it could not account for.
 *
 *  Sent and drafts are here because All answers "what came in?" and neither of
 *  them ever did — folding sent mail in doubles every conversation, and a draft
 *  is unfinished text that belongs in a composer, not a reading list. Replies
 *  stay visible in the thread view, which ignores the folder filter, and both
 *  have their own sidebar entries. "spam" and "draft" canonicalise to "junk"
 *  and "drafts" above, so neither needs an entry. */
const FOLDER_ALL_EXCLUDES = new Set(["junk", "trash", "sent", "drafts"]);

/**
 * The pseudo-folders: sidebar entries that are VIEWS, not real provider folders.
 * `starred` is a flag and `all` is a scope over other folders — a message can be
 * listed under either but can never be moved INTO one, and neither can be paged
 * from the provider.
 *
 * Anything that treats a folder key as a real destination must filter through
 * `isRealFolder`. Each move-to picker used to spell `key !== "starred"` inline,
 * which is exactly why adding `all` silently offered mail a folder that doesn't
 * exist.
 */
const PSEUDO_FOLDERS = new Set([FOLDER_ALL, "starred", "snoozed"]);

/** True when `key` is a real provider folder — somewhere mail can actually be
 *  moved to, and paged from. False for the All/Starred views. */
export function isRealFolder(key: string): boolean {
  return !PSEUDO_FOLDERS.has(key);
}

/** Default system folders that always appear even before sync, in display order. */
const DEFAULT_SYSTEM_FOLDERS: EmailFolder[] = [
  { icon: "Mails", label: "All", key: FOLDER_ALL, count: 0, type: "system" },
  { icon: "Inbox", label: "Inbox", key: "inbox", count: 0, type: "system" },
  { icon: "Star", label: "Starred", key: "starred", count: 0, type: "system" },
  { icon: "Clock", label: "Snoozed", key: "snoozed", count: 0, type: "system" },
  { icon: "Send", label: "Sent", key: "sent", count: 0, type: "system" },
  { icon: "FileText", label: "Drafts", key: "drafts", count: 0, type: "system" },
  { icon: "Archive", label: "Archive", key: "archive", count: 0, type: "system" },
  { icon: "ShieldAlert", label: "Junk", key: "junk", count: 0, type: "system" },
  { icon: "Trash2", label: "Trash", key: "trash", count: 0, type: "system" },
];

const SYSTEM_KEYS = new Set(DEFAULT_SYSTEM_FOLDERS.map((f) => f.key));

// Gmail's reserved system labels — never surfaced as user folders.
const GMAIL_SYSTEM_LABELS = new Set([
  "chat", "important", "starred", "unread", "category_personal",
  "category_social", "category_promotions", "category_updates",
  "category_forums", "unwanted",
]);

/**
 * Merge real provider folders with the canonical system folders, and append the
 * provider's *own* user folders/labels so the sidebar mirrors the real mailbox
 * structure (two-way: what you see in Outlook/Gmail, you see here).
 */
function mergeFolders(
  providerFolders: EmailFolderRaw[],
  emailCounts: Record<string, number>,
): EmailFolder[] {
  // Index provider folders by canonical key for system-folder count/labels.
  const canonProvider = new Map<string, EmailFolderRaw>();
  for (const f of providerFolders) {
    const key = toCanonical(f.name);
    // Prefer the entry with the most messages if duplicates collapse to one key.
    const existing = canonProvider.get(key);
    if (!existing || (f.message_count || 0) > (existing.message_count || 0)) {
      canonProvider.set(key, f);
    }
  }

  const systemFolders = DEFAULT_SYSTEM_FOLDERS.map((df) => {
    const pf = canonProvider.get(df.key);
    return {
      ...df,
      count: pf?.message_count || emailCounts[df.key] || 0,
      unread: pf?.unread_count ?? undefined,
    };
  });

  // Append user-created provider folders/labels (anything not a system key).
  const userFolders: EmailFolder[] = [];
  const seen = new Set<string>();
  for (const f of providerFolders) {
    const key = toCanonical(f.name);
    if (SYSTEM_KEYS.has(key) || key === "starred") continue;
    if (f.type === "system") continue; // skip provider system folders
    if (GMAIL_SYSTEM_LABELS.has(key) || /^category_/.test(key)) continue;
    if (seen.has(key)) continue;
    seen.add(key);
    userFolders.push({
      icon: "Folder",
      label: f.name,
      key,
      count: f.message_count || emailCounts[key] || 0,
      unread: f.unread_count ?? undefined,
      type: "user",
    });
  }
  userFolders.sort((a, b) => a.label.localeCompare(b.label));

  return withAllCount([...systemFolders, ...userFolders]);
}

/**
 * Give the All pseudo-folder a count, since no provider reports one for it.
 *
 * Sums every real folder it spans — junk/trash/sent/drafts are excluded by def.
 * (FOLDER_ALL_EXCLUDES, the same set that filters the list), and "starred" is a
 * flag over mail that already lives in another folder, so counting it would
 * double-count. Each message lives in exactly one folder, so the sum is the
 * total of what All actually shows.
 */
function withAllCount(folders: EmailFolder[]): EmailFolder[] {
  const total = folders.reduce(
    (sum, f) =>
      f.key === FOLDER_ALL || f.key === "starred" || FOLDER_ALL_EXCLUDES.has(f.key)
        ? sum
        : sum + (f.count || 0),
    0,
  );
  return folders.map((f) => (f.key === FOLDER_ALL ? { ...f, count: total } : f));
}

/**
 * Derive folder counts from email list (fallback when provider folders
 * haven't been fetched yet).
 */
function buildFolders(emails: Email[]): EmailFolder[] {
  const counts: Record<string, number> = {};
  for (const email of emails) {
    counts[email.folder.toLowerCase()] = (counts[email.folder.toLowerCase()] || 0) + 1;
    if (!email.isRead) counts["inbox"] = (counts["inbox"] || 0) + 1;
    if (email.isStarred) counts["starred"] = (counts["starred"] || 0) + 1;
  }

  return withAllCount(
    DEFAULT_SYSTEM_FOLDERS.map((df) => ({
      ...df,
      count: counts[df.key] || 0,
    })),
  );
}

interface EmailState {
  // Data
  accounts: EmailAccount[];
  emails: Email[];
  emailsTotal: number;
  emailsPage: number;
  folders: EmailFolder[];
  /** User-applicable label/category names for the selected account. */
  availableLabels: string[];
  /** Label/category name → assigned colour (preset token), for the selected
   *  account. Names absent here fall back to a deterministic colour. */
  labelColors: Record<string, string | null>;
  quickActions: typeof QUICK_ACTIONS;

  // Loading states
  accountsLoading: boolean;
  /**
   * True once the first `fetchAccounts` has settled, success or failure.
   * Before that, "no accounts" is not known, so the page must not draw the
   * empty state (EM-T3b: no flash of "Connect your email" on a hard load).
   */
  accountsLoaded: boolean;
  emailsLoading: boolean;
  loadingMore: boolean;
  backfilling: boolean;
  foldersLoading: boolean;
  /** Per mailbox and folder: the cursor for paging older provider history
   *  (client-held). The key is `backfillKey(account, folder)` (MB-10). */
  backfillToken: Record<string, string | null>;
  /** Per mailbox and folder: the provider has no older mail left to fetch. */
  backfillExhausted: Record<string, boolean>;
  /** Per-account sync state. "syncing" = the sync request is in flight;
   *  "processing" = mail is persisted and the server is running the rules /
   *  categorize / Reply-Zero / auto-archive pipeline as a background task
   *  (H1 Option C), which the UI catches up to via delayed refetches. */
  syncStatus: Record<string, "idle" | "syncing" | "error" | "processing">;
  /** Accounts whose live provider calls returned 401/403 (stale OAuth), keyed
   *  by account id → error message. Drives the in-app reconnect banner
   *  immediately, without waiting for the next sync to set sync_status. */
  authErrors: Record<string, string>;
  /**
   * All inboxes (EM-T8d, D-EM-22): the list, search and facets read every
   * mailbox of the member. `selectedAccountId` stays a real mailbox, the one
   * that settings, automation and new mail use, so nothing else breaks.
   */
  viewAll: boolean;
  /**
   * All inboxes: the provider count of each well-known folder, summed over
   * each mailbox (EM-T8f-3 item 1). Null until a round of reads lands. It
   * goes back to null when the member leaves All inboxes and when a mailbox
   * leaves (review F4). `folders` stays the tree of ONE mailbox.
   */
  allFolderCounts: Record<string, number> | null;

  // Selection
  selectedAccountId: string | null;
  selectedFolder: string;
  /** Active label/category filter (null = no label filter). */
  selectedLabel: string | null;
  selectedEmailId: string | null;
  /** A message opened by id (e.g. from a chat card) that is NOT in the current
   *  folder's loaded list — fetched on demand so the detail pane can show it
   *  without first switching to the folder it lives in. */
  selectedEmailOverride: Email | null;
  searchQuery: string;
  /** Which folder the search bar looks in. null = "follow the open folder", so
   *  the bar reads "Search Inbox" and re-targets as the user navigates. A folder
   *  key (or "all") is an EXPLICIT override the user picked from the scope
   *  dropdown, and it sticks until they clear the search or change it. */
  searchScope: string | null;
  /** Closable filter pills (tags, from/to, unread/…) narrowing the search. */
  searchFilters: SearchFilter[];
  /** True when the last search was re-ranked semantically (hybrid) by the server
   *  — lets the UI show a "Smart results" indicator. False for lexical/no search. */
  searchIsSemantic: boolean;
  /** Checkbox multi-selection in the list, shared with the unified toolbar so
   *  bulk actions can live in the page-level bar instead of inside EmailList. */
  selectedIds: Set<string>;
  /** Transient command from the unified toolbar (or the dashboard) to the open
   *  email viewer, consumed and cleared by EmailDetail. "reply-ai" opens the
   *  reply composer AND kicks off an AI draft — the dashboard's draft-from-row. */
  viewerCommand:
    | "reply" | "reply-all" | "forward" | "block" | "download" | "reply-ai"
    | "nudge"
    | null;

  // UI
  composeOpen: boolean;
  composeDefaults: {
    /** The mailbox that sends. A reply carries the mailbox of the mail it
     *  answers. Absent = the selected mailbox (EM-T8a, D-EM-20). */
    accountId?: string;
    /** The From that an inline reply chose before a pop-out (EM-T8c). */
    fromAccountId?: string;
    to: string;
    subject: string;
    replyToBody?: string;
    quote?: string;
    replyToMessageId?: string;
    // The LOCAL message id being replied to, so the popped-out composer's
    // "Draft with AI" can load the same reply context the inline reply had
    // (the classifier keys _build_reply_context off email_messages.id, not the
    // provider id). Without it the pop-out drafted context-blind.
    messageId?: string;
    // Carried so an undo-send reopen restores the full message, not a lossy
    // subset. Without these the composer reset Cc/attachments/artifacts to
    // empty on open and the user silently lost them.
    cc?: string;
    attachments?: api.SendAttachment[];
    artifacts?: api.ArtifactAttachmentRef[];
  } | null;
  /** A message queued to send, shown with an "Undo" toast until the timer fires. */
  pendingSend: api.SendEmailParams | null;
  /** A prompt handed from the Assistant's "Fix" flow to the AI chat panel, which
   *  consumes it into its input on the next render then clears it. */
  pendingChatPrompt: string | null;
  error: string | null;

  // ── Assistant "Test/Apply on all" run (lifted here so it survives the
  //    Assistant overlay/TestTab unmounting when the user navigates away) ──
  /** Per-message rule-run results, keyed by message id. */
  testResults: Record<string, RunMessageResult>;
  /** Message ids with a run currently in flight (per-row spinner). */
  testRunningIds: string[];
  /** True while a "Test/Run on all" sweep is iterating. */
  testBulkRunning: boolean;
  /** The mode the active/last sweep used (false = Test/dry-run, true = Apply). */
  testApplyMode: boolean;

  // Actions
  fetchAccounts: () => Promise<void>;
  /**
   * Re-read the accounts with no spinner and no error banner (EM-T3b). The
   * first-sync poll calls it, so the loading overlay must not flash on every
   * tick. Returns the accounts it read, or null when the read failed.
   */
  refreshAccounts: () => Promise<EmailAccount[] | null>;
  fetchFolders: (accountId?: string) => Promise<void>;
  /**
   * All inboxes: read the folders of each mailbox and sum the well-known
   * counts into `allFolderCounts` (EM-T8f-3 item 1). The reads run in
   * parallel, at most `FOLDER_SUM_CONCURRENCY` at one time. Nothing awaits
   * them, so the list never waits on them. A mailbox whose read fails, or
   * takes longer than `FOLDER_SUM_TIMEOUT_MS`, adds nothing. Outside All
   * inboxes it does nothing.
   */
  fetchAllFolderCounts: () => Promise<void>;
  fetchEmails: () => Promise<void>;
  /** Silent background refresh of the current folder's first page (no spinner),
   *  so assistant/upstream changes (labels, drafts, new mail, archives) appear
   *  without a manual reload. No-op while loading or paginated past page 1. */
  softRefresh: () => Promise<void>;
  loadMoreEmails: () => Promise<void>;
  backfillOlder: () => Promise<void>;
  selectAccount: (id: string) => void;
  /** Show the mail of every mailbox of the member (EM-T8d, D-EM-22). */
  selectAll: () => void;
  /** Sync the scope of the view: each mailbox in All inboxes, else the
   *  selected one (EM-T8d review). */
  syncScope: () => void;
  selectFolder: (folder: string) => void;
  /** Filter the list by a label/category (null clears the filter). */
  selectLabel: (label: string | null) => void;
  selectEmail: (id: string | null) => void;
  /** Open a message by id even when it isn't in the current folder's list
   *  (chat-card "Open in inbox"): selects it and, if absent, fetches it so the
   *  detail pane renders it regardless of the active folder/view. */
  openEmailById: (id: string) => Promise<void>;
  /** Toggle one message in the checkbox multi-selection. */
  toggleEmailSelected: (id: string) => void;
  /** Replace the checkbox multi-selection (used by "select all"). */
  setSelectedEmails: (ids: string[]) => void;
  /** Clear the checkbox multi-selection. */
  clearEmailSelection: () => void;
  /** Apply an update to every checkbox-selected message, then clear. */
  bulkUpdateSelected: (updates: Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder">>) => void;
  /** Delete every checkbox-selected message, then clear. */
  bulkDeleteSelected: () => void;
  /** Send a transient command to the open email viewer (reply/forward/etc.). */
  setViewerCommand: (cmd: EmailState["viewerCommand"]) => void;
  setSearchQuery: (q: string) => void;
  /** Point the search bar at a folder ("all" = everything but junk/trash), or
   *  null to go back to following the open folder. */
  setSearchScope: (scope: string | null) => void;
  /** Replace the pill set (the bar owns add/remove via lib/searchFilters). */
  setSearchFilters: (filters: SearchFilter[]) => void;
  /** Drop the text AND the pills, returning to the plain folder list. */
  clearSearch: () => void;
  openCompose: (defaults?: { accountId?: string; fromAccountId?: string; to: string; cc?: string; subject: string; replyToBody?: string; quote?: string; replyToMessageId?: string; messageId?: string }) => void;
  closeCompose: () => void;
  hydrateEmail: (email: Email) => void;
  /** "Captured to Tasks" toast state (email → My Tasks inbox). */
  taskCaptureNotice: {
    title: string;
    created: boolean;
    disposition?: string;
    assigneeName?: string | null;
    dueAt?: string | null;
  } | null;
  /** The email whose "Add to Tasks" clarify popup is open (null = closed).
   *  The popup component owns the preview/enhance async state; the store only
   *  tracks which message it's for. */
  taskCapturePopupEmailId: string | null;
  /** Open the clarify-before-capture popup for an email ("Add to Tasks" now
   *  opens a review popup instead of capturing instantly). */
  captureEmailToTasks: (emailId: string) => void;
  closeTaskCapturePopup: () => void;
  /** Show the "Captured to Tasks" toast (called by the popup on confirm). */
  notifyTaskCaptured: (notice: NonNullable<EmailState["taskCaptureNotice"]>) => void;
  clearTaskCaptureNotice: () => void;
  updateEmail: (id: string, updates: Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder">>) => Promise<void>;
  fetchLabels: (accountId?: string) => Promise<void>;
  /** Set a label/category's colour (preset token); syncs to the provider. */
  /** Set the colour of a label in ONE mailbox: `accountId`, else the
   *  selected one. A label belongs to its mailbox (EM-T8d review). */
  setLabelColor: (name: string, color: string, accountId?: string | null) => Promise<void>;
  applyLabel: (id: string, name: string, add: boolean) => Promise<void>;
  /** Add/remove one category across many messages at once. */
  applyLabelBulk: (ids: string[], name: string, add: boolean) => Promise<void>;
  /** Remove ALL categories from the given messages. */
  clearCategories: (ids: string[]) => Promise<void>;
  deleteEmail: (id: string) => Promise<void>;
  /** Snooze a conversation until `until` (ISO), or bring it back now (until=null).
   *  Optimistically drops it from the current view, then reconciles. */
  snoozeEmail: (id: string, until: string | null) => Promise<void>;
  sendEmail: (params: api.SendEmailParams) => Promise<void>;
  undoSend: () => void;
  /** Create or update a draft (provider + local mirror); returns the saved draft. */
  saveDraft: (params: api.SaveDraftParams) => Promise<Email>;
  /** Send an existing draft natively (Drafts → Sent) and drop it from the list. */
  sendDraft: (accountId: string, draftId: string) => Promise<void>;
  /** Queue a prompt for the AI chat panel (used by the Assistant "Fix" flow). */
  setPendingChatPrompt: (prompt: string | null) => void;
  /** Run rules on one message (Test = dry-run, Apply = execute) and store result. */
  runTestOnMessage: (
    accountId: string, messageId: string, isTest: boolean
  ) => Promise<RunMessageResult | null>;
  /** Sweep a list of messages sequentially; keeps running across navigation. */
  runTestOnAll: (accountId: string, messageIds: string[], isTest: boolean) => Promise<void>;
  /** Request the in-progress sweep to stop after the current message. */
  stopTestRun: () => void;
  /** Clear cached per-message results (e.g. when switching Test↔Apply). */
  clearTestResults: () => void;
  triggerSync: (accountId: string) => Promise<void>;
  /**
   * Disconnect a mailbox: `DELETE /email/accounts/{id}`. `{ ok: true }` on
   * success. On a refusal it sets `error`, re-reads the accounts so the list on
   * screen is the server's, and gives back the text of the refusal. A 409 says
   * that a sync is still writing mail (EM-T4f).
   */
  deleteAccount: (id: string) => Promise<DisconnectOutcome>;
  /** Make an account the user's default mailbox (the inbox the UI opens on). */
  setDefaultAccount: (id: string) => Promise<void>;
  /** Put the server's copy of one account in the list, by id (EM-T6d: after the
   *  onboarding PATCH, so a failed re-read cannot bring the setup back). */
  replaceAccount: (account: EmailAccount) => void;
  /**
   * "Keep separate" (false) or "Show in All inboxes" (true), through
   * `PATCH /email/accounts/{id}` (EM-T8g-2, D-EM-28). In All inboxes it reads
   * the list again, so the rows of a separate mailbox leave at once. Fewer
   * than two pooled mailboxes end All inboxes for the default mailbox. A
   * refusal sets `error`, changes nothing and gives back false.
   */
  setInAllInboxes: (id: string, pooled: boolean) => Promise<boolean>;
  /**
   * The one reconciliation of the pool, after `accounts` changed from
   * `before` (EM-T8g-2 review F1, F3, F5, F7). A toggle, a disconnect, a
   * re-read and a quiet re-read all call it. In All inboxes:
   * - fewer than two pooled mailboxes end All inboxes for the default mailbox
   * - the rows, the checks and the open mail of a mailbox that left the pool
   *   go at once, and the list and the sums are read again
   * - the hidden selected mailbox stays pooled (`poolHome`), and its folders
   *   and labels are read again
   * A change of the pool clears the sums in each view.
   */
  applyPoolChange: (before: ReadonlyArray<EmailAccount>) => void;
  clearError: () => void;
}

/** Is anything narrowing the list — search text or a pill? */
function searchActive(s: EmailState): boolean {
  return isSearchActive(s.searchQuery, s.searchFilters);
}

/**
 * The scope the search bar is pointed at: the user's explicit pick, else the
 * open folder ("Search Inbox"). `all` is the pseudo-folder the backend expands
 * to "every folder but junk/trash".
 */
function effectiveScope(s: EmailState): string {
  return s.searchScope ?? s.selectedFolder;
}

/**
 * Fold the query + scope + pills into search params. ONE builder, used by the
 * first page, "load more" and the background refresh alike — so a paged or
 * refreshed search can't quietly apply a different filter set than page 1 did.
 */
function searchRequest(s: EmailState): api.SearchEmailsParams {
  return {
    q: s.searchQuery.trim() || undefined,
    folder: effectiveScope(s),
    ...toSearchParams(s.searchFilters),
  };
}

/**
 * Identity of the query currently on screen (text + scope + pills).
 *
 * An async fetch must not write its results into a view the user has since
 * changed. Comparing the searchQuery string alone would miss a pill being added
 * or the scope being switched mid-flight, landing stale results under a filter
 * that no longer matches them.
 */
function searchViewKey(s: EmailState): string {
  return JSON.stringify([
    s.searchQuery.trim(),
    effectiveScope(s),
    s.searchFilters.map(filterKey),
  ]);
}

let _debounceTimer: ReturnType<typeof setTimeout> | undefined;
/** Pending "Undo send" timer — fires the real send after the undo window. */
let _sendTimer: ReturnType<typeof setTimeout> | undefined;
/** Cooperative stop flag for the Assistant "Test/Run on all" sweep. Kept at
 *  module scope so it survives TestTab unmounting (run continues in the store). */
let _stopTestRun = false;
/** Post-manual-sync catch-up timers, per account. A manual sync responds as soon
 *  as new mail is persisted; the server then runs the rules / categorize /
 *  Reply-Zero / auto-archive pipeline as a background task (H1 Option C). These
 *  delayed refetches pull in the labels/categories it applies (and drop
 *  auto-archived mail) without waiting for the 20s background poll. Module-scoped
 *  so they survive component unmounts / account switches. */
const _postSyncTimers: Record<string, ReturnType<typeof setTimeout>[]> = {};
/** The summed folder counts of All inboxes (EM-T8f-3): one round of reads at
 *  a time. A request while a round is out asks for ONE more round after it. */
let _folderSumsInFlight = false;
let _folderSumsAgain = false;
/** The one pending read of the sums after a Refresh in All inboxes. A second
 *  Refresh moves it, so one Refresh is one round (EM-T8f-3 review F2). */
let _folderSumsAfterSync: ReturnType<typeof setTimeout> | undefined;
/** The order of the list reads (EM-T8g-2 review F4). Each `fetchEmails`
 *  takes the next number, and only the newest read lands. `softRefresh` and
 *  `loadMoreEmails` drop their page when a newer read started. */
let _listGen = 0;
/** How long the user has to undo a send. */
const UNDO_SEND_MS = 5000;

/** The set of pooled mailbox ids, as one key. A read that started under
 *  another pool drops its answer (EM-T8g-2 review F4). */
function poolKey(accounts: ReadonlyArray<{ id: string } & PoolFlag>): string {
  return pooledMailboxes(accounts).map((a) => a.id).sort().join(",");
}

/** localStorage key + URL param that persist the selected mailbox so the right
 *  inbox survives a refresh and is deep-linkable (the inbox-zero pattern, minus
 *  a dynamic route segment). */
const ACCOUNT_LS_KEY = "cc.email.selectedAccountId";
const ACCOUNT_URL_PARAM = "account";

/** Read the preferred account id: an explicit ?account= URL param wins (shared
 *  link), else the last selection from localStorage. Null on the server or when
 *  neither is set. */
function readPreferredAccountId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const fromUrl = new URLSearchParams(window.location.search).get(
      ACCOUNT_URL_PARAM,
    );
    if (fromUrl) return fromUrl;
  } catch {
    /* malformed URL — fall through to storage */
  }
  try {
    return window.localStorage.getItem(ACCOUNT_LS_KEY);
  } catch {
    return null;
  }
}

/** Persist the active account to localStorage and reflect it in the URL (without
 *  a navigation) so a refresh or shared link reopens the same mailbox. */
function persistAccountId(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) window.localStorage.setItem(ACCOUNT_LS_KEY, id);
    else window.localStorage.removeItem(ACCOUNT_LS_KEY);
  } catch {
    /* storage disabled (private mode) — URL still carries it */
  }
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set(ACCOUNT_URL_PARAM, id);
    else url.searchParams.delete(ACCOUNT_URL_PARAM);
    window.history.replaceState({}, "", url);
  } catch {
    /* history unavailable — localStorage still carries it */
  }
}

/** Per-account cache of the label→colour map, so a refresh paints chips in
 *  their real (provider) colours on the FIRST render instead of the
 *  deterministic hash colour, then flipping once fetchLabels resolves (the
 *  reported flicker). The network fetch still runs and overwrites this, but the
 *  cached seed makes the initial paint already correct. */
const LABEL_COLORS_LS_PREFIX = "cc.email.labelColors.";

function readCachedLabelColors(accountId: string | null): Record<string, string | null> {
  if (!accountId || typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LABEL_COLORS_LS_PREFIX + accountId);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeCachedLabelColors(
  accountId: string | null, colors: Record<string, string | null>,
): void {
  if (!accountId || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      LABEL_COLORS_LS_PREFIX + accountId, JSON.stringify(colors),
    );
  } catch {
    /* storage disabled (private mode) — flicker returns but nothing breaks */
  }
}

/** The stored scope of All inboxes, in the URL and in local storage. */
export const ALL_INBOXES = "all";

/**
 * Choose the initial view from a fetched list (EM-T8d, §11.4).
 * - A still-valid stored mailbox wins, a separate one too.
 * - "all" opens All inboxes, for two or more pooled mailboxes.
 * - With no stored choice, two or more pooled mailboxes open All inboxes.
 * - Else the default mailbox, else the first one.
 * `accountId` is always a real mailbox. All inboxes keeps `poolHome`, the
 * default when it is pooled, so its folders and labels belong to the view.
 * Outside All inboxes it is the default mailbox. A separate mailbox does not
 * count toward All inboxes (EM-T8g-2, D-EM-30, review F5).
 */
export function pickInitialView(
  accounts: ReadonlyArray<Pick<EmailAccount, "id" | "isDefault"> & PoolFlag>,
  preferred: string | null,
): { accountId: string | null; viewAll: boolean } {
  if (accounts.length === 0) return { accountId: null, viewAll: false };
  const fallback = accounts.find((a) => a.isDefault)?.id ?? accounts[0].id;
  if (preferred && accounts.some((a) => a.id === preferred)) {
    return { accountId: preferred, viewAll: false };
  }
  const home = hasAllInboxes(accounts) ? poolHome(accounts) : null;
  if (preferred === ALL_INBOXES || !preferred) {
    return { accountId: home?.id ?? fallback, viewAll: !!home };
  }
  // A stored mailbox that is gone falls back to All inboxes, or to the only
  // mailbox, with no error (§11.6 case 16).
  return { accountId: home?.id ?? fallback, viewAll: !!home };
}

/**
 * The folders that a view offers to open, to search in and to move mail to.
 * A custom folder belongs to one mailbox, so All inboxes offers only the
 * folders that each mailbox has (§11.4, EM-T8d review). The sidebar, the move
 * menus, the command palette and the search scope all read this.
 */
export function foldersInScope<F extends { type?: string }>(folders: ReadonlyArray<F>, viewAll: boolean): F[] {
  return viewAll ? folders.filter((f) => f.type !== "user") : [...folders];
}

/**
 * The well-known folders whose counts All inboxes sums over each mailbox:
 * Inbox, Drafts, Sent, Archive, Junk and Deleted (§11.4 "Folders", EM-T8f-3
 * item 1). `trash` is the key of Deleted.
 */
export const SUMMED_FOLDERS: readonly string[] = ["inbox", "drafts", "sent", "archive", "junk", "trash"];
const SUMMED_FOLDER_KEYS = new Set(SUMMED_FOLDERS);

/**
 * How many folder reads All inboxes runs at one time. Each read is one live
 * provider call, and Q-MB-1 sets no limit on the mailboxes of a member, so
 * this number bounds the reads, not the count of mailboxes.
 */
export const FOLDER_SUM_CONCURRENCY = 4;

/**
 * How long one folder read of the sums may take. A read that takes longer
 * adds 0, the same as a failed read, and the round goes on. The GET itself
 * aborts only at 120 s, so without this one hung mailbox held every later
 * request of the sums (EM-T8f-3 review F5).
 */
export const FOLDER_SUM_TIMEOUT_MS = 15_000;

/**
 * How long after the last sync of a Refresh the store reads the sums once.
 * It is the end of the catch-up window of `triggerSync`, so the sums see the
 * mail that the rules moved (EM-T8f-3 review F2).
 */
export const FOLDER_SUMS_AFTER_SYNC_MS = 6_000;

/** `promise`, or a rejection after `ms`. The timer clears when the promise
 *  settles first. */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("timeout")), ms);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

/**
 * The count of each well-known folder, summed over each mailbox. Each read is
 * the merged folders of one mailbox, or null when its read failed. A failed
 * read adds nothing (EM-T8f-3 item 1).
 */
export function sumFolderCounts(
  reads: ReadonlyArray<ReadonlyArray<Pick<EmailFolder, "key" | "count">> | null>,
): Record<string, number> {
  const sums: Record<string, number> = {};
  for (const key of SUMMED_FOLDERS) sums[key] = 0;
  for (const folders of reads) {
    if (!folders) continue;
    for (const f of folders) {
      if (SUMMED_FOLDER_KEYS.has(f.key)) sums[f.key] += f.count || 0;
    }
  }
  return sums;
}

/**
 * The folders that the switcher draws in All inboxes. The folders that each
 * mailbox has show (`foldersInScope`). A well-known folder takes its sum over
 * each mailbox. Each other folder takes no count, because its count belongs
 * to one mailbox. Before the sums land, no folder takes a count, so the count
 * of one mailbox never reads as the sum (EM-T8f-3 item 1).
 */
export function allInboxesFolders<F extends { key: string; type?: string; count: number }>(
  folders: ReadonlyArray<F>,
  sums: Readonly<Record<string, number>> | null,
): F[] {
  return foldersInScope(folders, true).map((f) => ({
    ...f,
    count: sums && SUMMED_FOLDER_KEYS.has(f.key) ? sums[f.key] ?? 0 : 0,
  }));
}

/** Run `fn` over `items` with at most `limit` calls out at one time. The
 *  results keep the order of `items`. `fn` must not reject. */
async function mapBounded<T, R>(
  items: ReadonlyArray<T>,
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

/** "syncing" or "processing" when a mailbox of the scope is busy: any pooled
 *  mailbox in All inboxes, else the selected one (EM-T8d review). A separate
 *  mailbox is not in the scope of All inboxes (EM-T8g-2). */
export function scopeBusy(state: {
  viewAll: boolean;
  selectedAccountId: string | null;
  accounts: ReadonlyArray<{ id: string } & PoolFlag>;
  syncStatus: Record<string, string | undefined>;
}): boolean {
  const ids = state.viewAll
    ? pooledMailboxes(state.accounts).map((a) => a.id)
    : state.selectedAccountId ? [state.selectedAccountId] : [];
  return ids.some((id) => state.syncStatus[id] === "syncing" || state.syncStatus[id] === "processing");
}

/**
 * The checked ids that are rows of the list on screen, in the order of the
 * list. Each bulk act and each "N selected" count reads this, never the raw
 * `selectedIds`. A check whose row left the list can then never reach a mail,
 * for example a mail of a mailbox kept separate (EM-T8g-2 review round 2).
 */
export function checkedRows(state: {
  emails: ReadonlyArray<{ id: string }>;
  selectedIds: ReadonlySet<string>;
}): string[] {
  return state.emails.filter((e) => state.selectedIds.has(e.id)).map((e) => e.id);
}

/** The checks that keep a row in `emails`, or null when each one does. A list
 *  read that lands calls it, so a check never outlives its row. */
function prunedChecks(
  selectedIds: ReadonlySet<string>,
  emails: ReadonlyArray<{ id: string }>,
): Set<string> | null {
  const rows = new Set(emails.map((e) => e.id));
  const kept = [...selectedIds].filter((id) => rows.has(id));
  return kept.length === selectedIds.size ? null : new Set(kept);
}

/** The `account_id` of a list, search or facet read: none in All inboxes. */
export function listScope(state: { viewAll: boolean; selectedAccountId: string | null }): string | undefined {
  return state.viewAll ? undefined : state.selectedAccountId || undefined;
}

/**
 * The key of the "load older" state: one mailbox and one folder. Keyed by the
 * folder alone, a switch of mailbox kept the cursor and the "nothing older"
 * flag of the old mailbox (EM-T8c, MB-10).
 */
export function backfillKey(accountId: string | null | undefined, folder: string): string {
  return `${accountId ?? ""}:${folder}`;
}

export const useEmailStore = create<EmailState>((set, get) => ({
  // Data
  accounts: [],
  emails: [],
  emailsTotal: 0,
  emailsPage: 1,
  folders: [],
  availableLabels: [],
  // Seed from the per-account cache so chips paint in their real colours on the
  // first render (no hash-colour → provider-colour flip once fetchLabels lands).
  labelColors: readCachedLabelColors(readPreferredAccountId()),
  quickActions: QUICK_ACTIONS,

  // Loading states
  accountsLoading: false,
  accountsLoaded: false,
  emailsLoading: false,
  loadingMore: false,
  backfilling: false,
  foldersLoading: false,
  backfillToken: {},
  backfillExhausted: {},
  syncStatus: {},
  authErrors: {},
  viewAll: false,
  allFolderCounts: null,

  // Selection
  selectedAccountId: null,
  selectedFolder: "inbox",
  selectedLabel: null,
  selectedEmailId: null,
  selectedEmailOverride: null,
  searchQuery: "",
  searchScope: null,
  searchFilters: [],
  searchIsSemantic: false,
  selectedIds: new Set<string>(),
  viewerCommand: null,

  // UI
  composeOpen: false,
  composeDefaults: null,
  pendingSend: null,
  pendingChatPrompt: null,
  error: null,

  testResults: {},
  testRunningIds: [],
  testBulkRunning: false,
  testApplyMode: false,

  // Actions
  fetchAccounts: async () => {
    set({ accountsLoading: true, error: null });
    try {
      const beforeAccounts = get().accounts;
      const before = beforeAccounts.map((a) => a.id);
      let accounts = await api.listEmailAccounts();
      // Demo fallback: no real accounts connected → show the mock set.
      if (accounts.length === 0 && DEMO) accounts = MOCK_ACCOUNTS;
      set({ accounts, accountsLoading: false, accountsLoaded: true });
      const gone = before.filter((id) => !accounts.some((a) => a.id === id));
      // A mailbox that another tab removed leaves the sums at once. They show
      // no count until a new round lands (EM-T8f-3 review F4).
      if (gone.length > 0) set({ allFolderCounts: null });
      // Pick the initial mailbox when none is selected yet: a persisted/URL
      // choice wins, else the user's default account, else the first one — so a
      // refresh or shared ?account= link reopens the right inbox.
      const { selectedAccountId } = get();
      if (!selectedAccountId && accounts.length > 0) {
        const initial = pickInitialView(accounts, readPreferredAccountId());
        if (initial.accountId) {
          set({ selectedAccountId: initial.accountId, viewAll: initial.viewAll });
          persistAccountId(initial.viewAll ? ALL_INBOXES : initial.accountId);
          get().fetchFolders(initial.accountId);
          get().fetchLabels(initial.accountId);
          void get().fetchAllFolderCounts();
        }
      } else if (!get().viewAll && selectedAccountId && accounts.length > 0 &&
                 !accounts.some((a) => a.id === selectedAccountId)) {
        // Another tab removed the mailbox in view: pick the view again, so the
        // list never reads a mailbox that is gone. Its open mail and its
        // checks go with it (EM-T8g-2 review round 2).
        get().applyPoolChange(beforeAccounts);
        const again = pickInitialView(accounts, null);
        set({
          selectedAccountId: again.accountId, viewAll: again.viewAll,
          selectedEmailId: null, selectedEmailOverride: null, selectedIds: new Set(),
        });
        persistAccountId(again.viewAll ? ALL_INBOXES : again.accountId);
        if (again.accountId) {
          get().fetchFolders(again.accountId);
          get().fetchLabels(again.accountId);
        }
        get().fetchEmails();
        void get().fetchAllFolderCounts();
      } else {
        // Another tab removed a mailbox, kept one separate, or put one back.
        // Its rows leave All inboxes now, not at the next refresh (EM-T8d
        // review F8). The one reconciliation decides, also when the hidden
        // mailbox of All inboxes went (EM-T8g-2 review F7, round 2).
        get().applyPoolChange(beforeAccounts);
        if (get().viewAll) persistAccountId(ALL_INBOXES);
      }
    } catch (err: any) {
      // Demo fallback: backend unreachable → seed mock accounts so the UI works.
      if (DEMO) {
        set({ accounts: MOCK_ACCOUNTS, accountsLoading: false, accountsLoaded: true });
        if (!get().selectedAccountId) {
          set({ selectedAccountId: MOCK_ACCOUNTS[0].id, folders: MOCK_FOLDERS });
          get().fetchEmails();
        }
        return;
      }
      set({ accountsLoading: false, accountsLoaded: true, error: err.message || "Failed to load accounts" });
    }
  },

  refreshAccounts: async () => {
    if (DEMO) return get().accounts;
    try {
      const before = get().accounts;
      const accounts = await api.listEmailAccounts();
      set({ accounts });
      // A quiet re-read keeps the pool true too: a rename, the first-sync
      // poll and a refusal all land here (EM-T8g-2 review F7).
      get().applyPoolChange(before);
      return accounts;
    } catch {
      return null;
    }
  },

  fetchFolders: async (accountId?: string) => {
    const aid = accountId ?? get().selectedAccountId;
    if (!aid) return;
    set({ foldersLoading: true });
    try {
      const rawFolders = await api.listEmailFolders(aid);
      // Merge with current email counts
      const emailCounts: Record<string, number> = {};
      for (const e of get().emails) {
        const key = e.folder.toLowerCase();
        emailCounts[key] = (emailCounts[key] || 0) + 1;
      }
      const folders = mergeFolders(rawFolders, emailCounts);
      // Live provider call succeeded — clear any prior auth flag for this account.
      const cleared = { ...get().authErrors };
      delete cleared[aid];
      set({ folders, foldersLoading: false, authErrors: cleared });
    } catch (err: any) {
      // Demo fallback: no backend → use the mock folder tree.
      if (DEMO) {
        set({ folders: MOCK_FOLDERS, foldersLoading: false });
        return;
      }
      // Fall back to deriving from emails
      const folders = buildFolders(get().emails);
      // A 401/403 from the live folder call means the account's OAuth token is
      // stale — flag it immediately so the reconnect banner shows without
      // waiting for the next background sync to mark sync_status='error'.
      if (err?.status === 401 || err?.status === 403) {
        set({
          folders,
          foldersLoading: false,
          authErrors: {
            ...get().authErrors,
            [aid]: err?.message || "Authentication failed — reconnect the account.",
          },
        });
      } else {
        set({ folders, foldersLoading: false });
      }
    }
  },

  fetchAllFolderCounts: async () => {
    if (_folderSumsInFlight) {
      _folderSumsAgain = true;
      return;
    }
    _folderSumsInFlight = true;
    try {
      do {
        _folderSumsAgain = false;
        const { viewAll, accounts } = get();
        if (!viewAll || !hasAllInboxes(accounts)) break;
        // One live provider call for each pooled mailbox. A separate mailbox
        // adds nothing to the sums (EM-T8g-2, D-EM-30). The counts are the
        // provider `message_count` only, so the merge gets no list counts.
        // A failed read, a read that does not merge, or a read past
        // FOLDER_SUM_TIMEOUT_MS is null and adds 0.
        const pooled = pooledMailboxes(accounts).map((a) => a.id);
        const reads = await mapBounded(pooled, FOLDER_SUM_CONCURRENCY, (id) =>
          withTimeout(api.listEmailFolders(id), FOLDER_SUM_TIMEOUT_MS)
            .then((raw) => mergeFolders(raw, {}))
            .catch(() => null),
        );
        // A newer request reads again, and a view that left All inboxes
        // takes nothing.
        if (!_folderSumsAgain && get().viewAll) set({ allFolderCounts: sumFolderCounts(reads) });
      } while (_folderSumsAgain);
    } finally {
      _folderSumsInFlight = false;
    }
  },

  fetchEmails: async () => {
    const { selectedAccountId, selectedFolder, selectedLabel } = get();
    // Only the newest read lands. A read that started before a toggle must
    // not bring back the rows of a separate mailbox (EM-T8g-2 review F4).
    const gen = ++_listGen;
    set({ emailsLoading: true, error: null });
    try {
      // A search (text and/or pills) goes to the dedicated /email/search
      // endpoint — relevance-ranked and highlighted, scoped by the bar's scope
      // dropdown rather than the open folder. Nothing to search → plain list.
      // hybrid:true asks for semantic re-ranking; the server applies it only
      // when semantic search is enabled and returns whether it did.
      let searchIsSemantic = false;
      const result = searchActive(get())
        ? await (async () => {
            const r = await api.searchEmails({
              ...searchRequest(get()),
              accountId: listScope(get()),
              label: selectedLabel || undefined,
              hybrid: true,
              page: 1,
              pageSize: PAGE_SIZE,
            });
            searchIsSemantic = r.hybrid;
            return r;
          })()
        : await api.listEmails({
            accountId: listScope(get()),
            folder: selectedFolder,
            label: selectedLabel || undefined,
            page: 1,
            pageSize: PAGE_SIZE,
          });
      if (gen !== _listGen) return;
      let emails = result.emails;
      let total = result.total;
      // Demo fallback: backend returned nothing → show mock messages.
      if (emails.length === 0 && DEMO) {
        emails = demoEmailsFor(selectedAccountId, selectedFolder);
        total = emails.length;
      }
      // Don't clobber the provider folder tree (system + user folders) fetched
      // by fetchFolders — only seed a system-folder fallback if we have none yet.
      const existing = get().folders;
      const folders =
        existing.length > 0 ? existing : DEMO ? MOCK_FOLDERS : buildFolders(emails);
      // Seed the label picker from categories actually present on messages —
      // providers like Outlook expose no master categories, so without this the
      // right-click "Label" menu would stay empty even when mail is categorized.
      const labelSet = new Set(get().availableLabels);
      // In All inboxes the rows come from each mailbox, and a label belongs to
      // one mailbox, so the rows seed nothing (EM-T8d review F6).
      if (!get().viewAll) for (const e of emails) for (const c of e.categories || []) labelSet.add(c);
      // A check never outlives its row (EM-T8g-2 review round 2).
      const checks = prunedChecks(get().selectedIds, emails);
      set({
        emails,
        folders,
        availableLabels: [...labelSet].sort(),
        emailsLoading: false,
        emailsTotal: total,
        emailsPage: 1,
        searchIsSemantic,
        ...(checks ? { selectedIds: checks } : {}),
      });
    } catch (err: any) {
      // A newer read owns the list, its spinner and its error.
      if (gen !== _listGen) return;
      // Demo fallback: backend unreachable → show mock messages.
      if (DEMO) {
        const emails = demoEmailsFor(selectedAccountId, selectedFolder);
        const existing = get().folders;
        set({
          emails,
          folders: existing.length > 0 ? existing : MOCK_FOLDERS,
          emailsLoading: false,
          emailsTotal: emails.length,
          emailsPage: 1,
        });
        return;
      }
      set({ emailsLoading: false, error: err.message || "Failed to load emails" });
    }
  },

  softRefresh: async () => {
    const before = get();
    const {
      selectedAccountId, selectedFolder, selectedLabel,
      emailsLoading, emailsPage,
    } = before;
    // Don't disrupt an in-flight load or a user who has paginated/scrolled
    // deeper than the first page — they can pull-to-refresh manually.
    if (!selectedAccountId || emailsLoading || emailsPage > 1) return;
    // A background refresh must re-run the SAME query the user is looking at —
    // during a search that's /email/search with the scope + pills, not a plain
    // folder list (which would silently swap ranked hits for the raw folder).
    const wasSearch = searchActive(before);
    const viewKey = searchViewKey(before);
    // A change of the pool, or a newer read, makes this answer stale
    // (EM-T8g-2 review F4).
    const pool = poolKey(before.accounts);
    const gen = _listGen;
    try {
      const result = wasSearch
        ? await api.searchEmails({
            ...searchRequest(before),
            accountId: listScope(before),
            label: selectedLabel || undefined,
            hybrid: true,
            page: 1,
            pageSize: PAGE_SIZE,
          })
        : await api.listEmails({
            accountId: listScope(before),
            folder: selectedFolder,
            label: selectedLabel || undefined,
            page: 1,
            pageSize: PAGE_SIZE,
          });
      // Bail if the user navigated away mid-fetch (avoid clobbering the new view).
      const now = get();
      if (
        now.selectedAccountId !== selectedAccountId ||
        // All inboxes keeps the same selected mailbox, so the scope is
        // compared too (EM-T8d review).
        listScope(now) !== listScope(before) ||
        now.selectedFolder !== selectedFolder ||
        now.selectedLabel !== selectedLabel ||
        searchViewKey(now) !== viewKey ||
        now.emailsPage > 1 ||
        poolKey(now.accounts) !== pool ||
        _listGen !== gen
      ) {
        return;
      }
      // Demo: don't let an empty background refresh wipe the seeded mock list.
      if (result.emails.length === 0 && DEMO) return;
      const labelSet = new Set(now.availableLabels);
      if (!now.viewAll) for (const e of result.emails) for (const c of e.categories || []) labelSet.add(c);
      // A check never outlives its row (EM-T8g-2 review round 2).
      const checks = prunedChecks(now.selectedIds, result.emails);
      set({
        emails: result.emails,
        emailsTotal: result.total,
        availableLabels: [...labelSet].sort(),
        ...(checks ? { selectedIds: checks } : {}),
      });
    } catch {
      /* silent — a failed background refresh shouldn't surface an error */
    }
  },

  loadMoreEmails: async () => {
    const state = get();
    const {
      selectedAccountId, selectedFolder,
      emailsPage, emails, loadingMore, emailsTotal,
    } = state;
    if (loadingMore || emails.length >= emailsTotal) return;
    set({ loadingMore: true });
    // A newer read or a change of the pool replaced the list this page
    // extends (EM-T8g-2 review F4).
    const gen = _listGen;
    const pool = poolKey(state.accounts);
    try {
      const nextPage = emailsPage + 1;
      // Page 2+ of a search must BE the same search: same endpoint, same scope,
      // same pills. Paging a search through the plain folder list would append
      // rows ranked by recency under rows ranked by relevance — and silently
      // drop the scope, so scrolling a scoped search leaked in other folders.
      const result = searchActive(state)
        ? await api.searchEmails({
            ...searchRequest(state),
            accountId: listScope(state),
            label: state.selectedLabel || undefined,
            hybrid: true,
            page: nextPage,
            pageSize: PAGE_SIZE,
          })
        : await api.listEmails({
            accountId: listScope(state),
            folder: selectedFolder,
            label: state.selectedLabel || undefined,
            page: nextPage,
            pageSize: PAGE_SIZE,
          });
      if (_listGen !== gen || poolKey(get().accounts) !== pool) {
        set({ loadingMore: false });
        return;
      }
      // Append, de-duping by id in case a sync shifted the window mid-scroll.
      const seen = new Set(emails.map((e) => e.id));
      const merged = [...emails, ...result.emails.filter((e) => !seen.has(e.id))];
      set({
        emails: merged,
        emailsPage: nextPage,
        emailsTotal: result.total,
        loadingMore: false,
      });
    } catch {
      // Paging older mail is best-effort and auto-triggers on scroll, so never
      // surface the raw provider error (it leaks the Graph/Gmail request URL).
      set({ loadingMore: false, error: "Couldn't load more messages. Try again." });
    }
  },

  backfillOlder: async () => {
    const {
      selectedAccountId, selectedFolder, backfilling,
      backfillToken, emails, emailsPage,
    } = get();
    if (backfilling || !selectedAccountId || get().viewAll) return;
    set({ backfilling: true, error: null });
    try {
      // 1) Pull older mail from the provider into the DB.
      const key = backfillKey(selectedAccountId, selectedFolder);
      const res = await api.backfillFolder(
        selectedAccountId,
        selectedFolder,
        backfillToken[key] ?? undefined,
      );
      // 2) Surface the freshly-persisted older page from the DB and append it.
      const nextPage = emailsPage + 1;
      const result = await api.listEmails({
        accountId: selectedAccountId,
        folder: selectedFolder,
        page: nextPage,
        pageSize: PAGE_SIZE,
      });
      // The member may have switched mailbox or folder while this ran. Then
      // the page of the old view must not land in the new one (§11.6 case 20).
      const now = get();
      if (backfillKey(now.selectedAccountId, now.selectedFolder) !== key) {
        set({
          backfilling: false,
          backfillToken: { ...now.backfillToken, [key]: res.next_page_token },
          backfillExhausted: { ...now.backfillExhausted, [key]: res.exhausted },
        });
        return;
      }
      const seen = new Set(emails.map((e) => e.id));
      const merged = [...emails, ...result.emails.filter((e) => !seen.has(e.id))];
      set({
        emails: merged,
        emailsPage: nextPage,
        emailsTotal: result.total,
        backfilling: false,
        backfillToken: {
          ...get().backfillToken,
          [key]: res.next_page_token,
        },
        backfillExhausted: {
          ...get().backfillExhausted,
          [key]: res.exhausted,
        },
      });
    } catch {
      // Best-effort provider paging. Do NOT mark the folder exhausted on a
      // transient failure — that would permanently disable "load older" for the
      // session. Just stop this attempt and show a friendly, retryable note.
      set({
        backfilling: false,
        error: "Couldn't load older messages right now. Try again.",
      });
    }
  },

  selectAccount: (id: string) => {
    set({
      selectedAccountId: id, viewAll: false, selectedEmailId: null,
      selectedEmailOverride: null, selectedIds: new Set(),
      // The sums belong to All inboxes. They go when the member leaves it, so
      // a return never shows sums of a set of mailboxes that has changed
      // (EM-T8f-3 review F4).
      allFolderCounts: null,
      // Seed this account's cached label colours so switching accounts doesn't
      // flash the previous account's / hash colours before fetchLabels lands.
      labelColors: readCachedLabelColors(id),
    });
    // Remember the choice (localStorage + URL) so it survives a refresh.
    persistAccountId(id);
    // Fetch folders, labels and emails for the newly selected account
    get().fetchFolders(id);
    get().fetchLabels(id);
    get().fetchEmails();
  },

  selectAll: () => {
    const { selectedFolder, folders, accounts, selectedAccountId } = get();
    // A custom folder belongs to one mailbox, so All inboxes opens the Inbox
    // then. A well-known folder, or "all", "starred" and "snoozed", stays.
    const custom = folders.some((f) => f.key === selectedFolder && f.type === "user");
    // All inboxes keeps a pooled mailbox selected out of view. From the view
    // of a separate mailbox it moves to `poolHome`, so the folders and the
    // labels belong to All inboxes (EM-T8g-2 review F5).
    const inPool = pooledMailboxes(accounts).some((a) => a.id === selectedAccountId);
    const home = inPool ? null : poolHome(accounts);
    set({
      viewAll: true,
      selectedEmailId: null,
      selectedEmailOverride: null,
      selectedIds: new Set(),
      // A label belongs to one mailbox (§11.6 case 21).
      selectedLabel: null,
      ...(custom ? { selectedFolder: "inbox" } : {}),
      ...(home ? { selectedAccountId: home.id, labelColors: readCachedLabelColors(home.id) } : {}),
    });
    persistAccountId(ALL_INBOXES);
    if (home) {
      get().fetchFolders(home.id);
      get().fetchLabels(home.id);
    }
    get().fetchEmails();
    // The summed counts of the folders. Nothing awaits them (EM-T8f-3).
    void get().fetchAllFolderCounts();
  },

  syncScope: () => {
    const { viewAll, accounts, selectedAccountId } = get();
    if (viewAll) {
      // Each pooled mailbox. A separate mailbox syncs from its own view and
      // by its own loop (EM-T8g-2).
      const runs = pooledMailboxes(accounts).map((a) => get().triggerSync(a.id));
      // One read of the sums for the whole Refresh: after the last sync, at
      // the end of its catch-up window. A sync never asks for the sums
      // itself, so N syncs that end apart cost one round, not one round for
      // each catch-up. A second Refresh moves the read (EM-T8f-3 review F2).
      void Promise.allSettled(runs).then(() => {
        clearTimeout(_folderSumsAfterSync);
        _folderSumsAfterSync = setTimeout(() => {
          _folderSumsAfterSync = undefined;
          void get().fetchAllFolderCounts();
        }, FOLDER_SUMS_AFTER_SYNC_MS);
      });
    } else if (selectedAccountId) {
      get().triggerSync(selectedAccountId);
    }
  },

  selectFolder: (folder: string) => {
    // Switching folders clears any active label filter + checkbox selection.
    // It also ENDS any search: picking a folder means "show me this folder", so
    // leaving a query running would show search hits under a folder heading —
    // and the scope reverts to following the folder, so the bar reads
    // "Search Sent" the moment you land in Sent.
    if (_debounceTimer) clearTimeout(_debounceTimer);
    set({
      selectedFolder: folder,
      selectedLabel: null,
      selectedEmailId: null,
      selectedEmailOverride: null,
      selectedIds: new Set(),
      searchQuery: "",
      searchFilters: [],
      searchScope: null,
      searchIsSemantic: false,
    });
    get().fetchEmails();
  },

  selectLabel: (label: string | null) => {
    // A label belongs to one mailbox, so All inboxes has no label filter
    // (§11.6 case 21).
    if (label && get().viewAll) return;
    set({ selectedLabel: label, selectedEmailId: null, selectedEmailOverride: null });
    get().fetchEmails();
  },

  selectEmail: (id: string | null) => {
    // Opening a different message cancels any pending viewer command and any
    // out-of-list override (a normal list selection is in `emails`).
    set({ selectedEmailId: id, selectedEmailOverride: null, viewerCommand: null });
    // Mark as read if unread
    if (id) {
      const email = get().emails.find((e) => e.id === id);
      if (email && !email.isRead) {
        get().updateEmail(id, { isRead: true });
      }
    }
  },

  openEmailById: async (id: string) => {
    set({ selectedEmailId: id, selectedEmailOverride: null, viewerCommand: null });
    // Already loaded in the current folder → behave like a normal selection.
    const inList = get().emails.find((e) => e.id === id);
    if (inList) {
      if (!inList.isRead) get().updateEmail(id, { isRead: true });
      return;
    }
    // Not in the loaded list (different folder / not yet paged in). Fetch the
    // single message so the detail pane can render it without the user first
    // navigating to the folder it lives in.
    try {
      const email = await api.getEmail(id);
      // A late-arriving fetch must not clobber a newer selection.
      if (get().selectedEmailId !== id) return;
      // A mail of ANOTHER mailbox (a chat card's "Open in inbox") opens in its
      // own mailbox. The view switches first, so the sidebar, the folders and
      // every act name the mailbox that holds the mail (EM-T8a, MB-3). In All
      // inboxes, a mail of a separate mailbox opens in that mailbox, never in
      // All inboxes (EM-T8g-2 item 4). `mailboxToOpen` is the rule.
      // `selectAccount` clears the selection, so the mail is set after it.
      const target = mailboxToOpen(get(), email.accountId);
      if (target) {
        get().selectAccount(target);
        set({ selectedEmailId: id, viewerCommand: null });
      }
      set({ selectedEmailOverride: email });
    } catch {
      set({ error: "Couldn't open that email." });
    }
  },

  toggleEmailSelected: (id: string) =>
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { selectedIds: next };
    }),

  setSelectedEmails: (ids: string[]) => set({ selectedIds: new Set(ids) }),

  clearEmailSelection: () => set({ selectedIds: new Set() }),

  bulkUpdateSelected: (updates) => {
    // Only the checked rows of the list on screen (EM-T8g-2 review round 2).
    const ids = checkedRows(get());
    ids.forEach((id) => get().updateEmail(id, updates));
    set({ selectedIds: new Set() });
  },

  bulkDeleteSelected: () => {
    // Only the checked rows of the list on screen. A stale check of a mailbox
    // kept separate must never delete its mail (EM-T8g-2 review round 2).
    const ids = checkedRows(get());
    ids.forEach((id) => get().deleteEmail(id));
    set({ selectedIds: new Set() });
  },

  setViewerCommand: (cmd) => set({ viewerCommand: cmd }),

  setSearchQuery: (q: string) => {
    set({ searchQuery: q });
    // Debounce: wait 300ms since last keystroke before fetching
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => {
      get().fetchEmails();
    }, 300);
  },

  setSearchScope: (scope: string | null) => {
    // Picking a scope IS navigating there. Searching "Sent" while the sidebar
    // still highlights Inbox leaves the two disagreeing about where you are,
    // and the results read as if they came from the folder you're looking at.
    //
    // Moving `selectedFolder` (rather than holding an override in
    // `searchScope`) also means clearing the search leaves you standing in the
    // folder you searched, instead of teleporting back to where you started.
    // Note this deliberately does NOT go through `selectFolder`, which ends the
    // search — here the query is the whole point and must survive.
    if (scope) {
      set({
        selectedFolder: scope,
        searchScope: null,
        selectedLabel: null,
        selectedIds: new Set(),
        selectedEmailId: null,
        selectedEmailOverride: null,
      });
    } else {
      set({ searchScope: null, selectedEmailId: null, selectedEmailOverride: null });
    }
    // With an empty bar the folder list still has to reload — the folder itself
    // changed, not just the search's reach.
    get().fetchEmails();
  },

  setSearchFilters: (filters: SearchFilter[]) => {
    set({ searchFilters: filters, selectedEmailId: null, selectedEmailOverride: null });
    // Pills apply immediately — unlike typing, there's no keystroke to debounce.
    // Removing the last pill with no text falls back to the plain folder list,
    // which fetchEmails routes to on its own.
    get().fetchEmails();
  },

  clearSearch: () => {
    if (_debounceTimer) clearTimeout(_debounceTimer);
    set({
      searchQuery: "",
      searchFilters: [],
      searchScope: null,
      searchIsSemantic: false,
      selectedEmailId: null,
      selectedEmailOverride: null,
    });
    get().fetchEmails();
  },

  openCompose: (defaults) => {
    set({ composeOpen: true, composeDefaults: defaults || null });
  },

  closeCompose: () => {
    set({ composeOpen: false, composeDefaults: null });
  },

  hydrateEmail: (full) => {
    // Merge a fully-fetched message (body + attachments) into the cached list
    // so reply quoting and re-opens use the complete copy without re-fetching.
    set({
      emails: get().emails.map((e) => (e.id === full.id ? { ...e, ...full } : e)),
    });
  },

  taskCaptureNotice: null,
  taskCapturePopupEmailId: null,

  captureEmailToTasks: (emailId) => {
    const email = get().emails.find((e) => e.id === emailId);
    if (!email) {
      // e.g. a brand-new draft not yet saved to the message list — nothing to
      // capture from, so show a hint instead of opening an empty popup.
      set({
        taskCaptureNotice: {
          title: "Save this draft first, then add it to My Tasks",
          created: false,
        },
      });
      setTimeout(() => get().clearTaskCaptureNotice(), 6000);
      return;
    }
    set({ taskCapturePopupEmailId: emailId });
  },

  closeTaskCapturePopup: () => set({ taskCapturePopupEmailId: null }),

  notifyTaskCaptured: (notice) => {
    set({ taskCaptureNotice: notice });
    // Auto-dismiss THIS notice only — an older capture's timer must not
    // clear a newer notice (identity check, not a blanket null).
    setTimeout(() => {
      set((s) => (s.taskCaptureNotice === notice ? { taskCaptureNotice: null } : s));
    }, 6000);
  },

  clearTaskCaptureNotice: () => set({ taskCaptureNotice: null }),

  updateEmail: async (id, updates) => {
    // Optimistic update. When an email is moved to a *different* folder than the
    // one we're viewing (archive / move-to / etc.), drop it from the list so it
    // visibly leaves the current folder — except in the virtual "starred" view.
    const prevEmails = get().emails;
    const movedAway =
      updates.folder !== undefined &&
      updates.folder !== get().selectedFolder &&
      get().selectedFolder !== "starred";
    set({
      emails: movedAway
        ? prevEmails.filter((e) => e.id !== id)
        : prevEmails.map((e) => (e.id === id ? { ...e, ...updates } : e)),
      emailsTotal: movedAway
        ? Math.max(0, get().emailsTotal - 1)
        : get().emailsTotal,
    });
    // Demo: keep the optimistic change; there's no backend to persist to.
    if (DEMO) return;
    try {
      const updated = await api.updateEmail(id, updates);
      if (!movedAway) {
        set({
          emails: get().emails.map((e) =>
            e.id === id ? { ...e, ...updated } : e
          ),
        });
      }
    } catch (err: any) {
      // Revert on failure
      set({ emails: prevEmails, error: err.message || "Failed to update email" });
    }
  },

  fetchLabels: async (accountId?: string) => {
    const aid = accountId ?? get().selectedAccountId;
    if (!aid) return;
    try {
      const labels = await api.listLabels(aid);
      // Always offer the standard categories too, so they're available to apply
      // (applying creates the real Gmail label / Outlook category upstream).
      const names = Array.from(
        new Set([...labels.map((l) => l.name), ...EMAIL_CATEGORIES])
      ).sort();
      // Name → colour map from the provider; uncoloured labels are simply
      // absent (the renderer falls back to a deterministic colour).
      const colors: Record<string, string | null> = {};
      for (const l of labels) if (l.color) colors[l.name] = l.color;
      set({ availableLabels: names, labelColors: colors });
      // Cache for this account so the next refresh's first paint is correct.
      writeCachedLabelColors(aid, colors);
    } catch {
      // Even if the provider list fails, offer the standard categories.
      set({
        availableLabels: Array.from(new Set([...EMAIL_CATEGORIES])).sort(),
        labelColors: {},
      });
    }
  },

  setLabelColor: async (name, color, accountId) => {
    const aid = accountId || get().selectedAccountId;
    if (!aid) return;
    // The chips of the view read the colours of the selected mailbox. A colour
    // for another mailbox goes to the provider only (EM-T8d review).
    const mine = aid === get().selectedAccountId;
    const prev = get().labelColors;
    if (mine) {
      // Optimistically recolour everywhere chips read from labelColors.
      const next = { ...prev, [name]: color };
      set({ labelColors: next });
      writeCachedLabelColors(aid, next);  // persist so the next paint is correct
    }
    if (DEMO) return;
    try {
      await api.setLabelColor(aid, name, color);
    } catch (err) {
      set({
        labelColors: mine ? prev : get().labelColors,
        error: (err as Error)?.message || "Failed to set colour",
      });
    }
  },

  applyLabel: async (id, name, add) => {
    const prevEmails = get().emails;
    // Optimistically update the message's category chips.
    set({
      emails: prevEmails.map((e) => {
        if (e.id !== id) return e;
        const cats = e.categories || [];
        const next = add
          ? cats.includes(name) ? cats : [...cats, name]
          : cats.filter((c) => c !== name);
        return { ...e, categories: next };
      }),
    });
    // Track a brand-new label so it's offered for other messages immediately.
    if (add && !get().availableLabels.includes(name)) {
      set({ availableLabels: [...get().availableLabels, name].sort() });
    }
    // Demo: keep the optimistic chip change; no backend to persist to.
    if (DEMO) return;
    try {
      const updated = await api.updateEmailLabels(
        id,
        add ? [name] : [],
        add ? [] : [name],
      );
      set({
        emails: get().emails.map((e) => (e.id === id ? { ...e, ...updated } : e)),
      });
    } catch (err: any) {
      set({ emails: prevEmails, error: err.message || "Failed to update label" });
    }
  },

  applyLabelBulk: async (ids, name, add) => {
    // Apply (or remove) one category across many messages. Sequential keeps it
    // simple and each call is small; optimistic per-message via applyLabel.
    for (const id of ids) {
      await get().applyLabel(id, name, add);
    }
  },

  clearCategories: async (ids) => {
    for (const id of ids) {
      const email = get().emails.find((e) => e.id === id);
      const cats = email?.categories || [];
      if (cats.length === 0) continue;
      const prev = get().emails;
      // Optimistically drop all category chips for this message.
      set({
        emails: prev.map((e) => (e.id === id ? { ...e, categories: [] } : e)),
      });
      try {
        await api.updateEmailLabels(id, [], cats);
      } catch (err) {
        set({
          emails: prev,
          error: (err as Error)?.message || "Failed to clear categories",
        });
      }
    }
  },

  deleteEmail: async (id) => {
    const prevEmails = get().emails;
    set({ emails: prevEmails.filter((e) => e.id !== id) });
    // Clear selection if the deleted message was selected.
    const clearSelectionIfDeleted = () => {
      if (get().selectedEmailId === id) {
        set({ selectedEmailId: get().emails[0]?.id ?? null });
      }
    };
    // Demo: keep the optimistic removal; no backend to delete from.
    if (DEMO) {
      clearSelectionIfDeleted();
      return;
    }
    try {
      await api.deleteEmail(id);
      clearSelectionIfDeleted();
    } catch (err: any) {
      set({ emails: prevEmails, error: err.message || "Failed to delete email" });
    }
  },

  snoozeEmail: async (id, until) => {
    const prevEmails = get().emails;
    const target = prevEmails.find((e) => e.id === id);
    const tid = target?.threadId;
    // Optimistically drop the whole conversation from the current list — it's
    // leaving the inbox (snooze) or leaving the Snoozed view (un-snooze). The
    // refetch below reconciles thread siblings and folder counts with the server.
    set({
      // A conversation never spans two mailboxes (§11.6 case 13).
      emails: prevEmails.filter((e) =>
        tid ? !(e.threadId === tid && e.accountId === target?.accountId) : e.id !== id),
    });
    if (get().selectedEmailId === id) {
      set({ selectedEmailId: get().emails[0]?.id ?? null });
    }
    if (DEMO) return;
    try {
      await api.snoozeEmail(id, until);
      get().fetchEmails();
    } catch (err: any) {
      set({ emails: prevEmails, error: err.message || "Failed to snooze email" });
    }
  },

  sendEmail: async (params) => {
    // Optimistically close the composer and hold the message for a few seconds
    // so it can be undone before it actually leaves.
    set({
      composeOpen: false,
      composeDefaults: null,
      pendingSend: params,
      error: null,
    });
    if (_sendTimer) clearTimeout(_sendTimer);
    _sendTimer = setTimeout(async () => {
      const p = get().pendingSend;
      if (!p) return;
      set({ pendingSend: null });
      try {
        await api.sendEmail(p);
        get().fetchEmails(); // surface the sent item
      } catch (err: any) {
        set({ error: err.message || "Failed to send email" });
      }
    }, UNDO_SEND_MS);
  },

  undoSend: () => {
    if (_sendTimer) {
      clearTimeout(_sendTimer);
      _sendTimer = undefined;
    }
    const p = get().pendingSend;
    set({ pendingSend: null });
    // Reopen the composer with the FULL message so it can be edited and re-sent.
    // p.bodyText is the combined body (new text + quoted chain); split it back so
    // the editable box holds only what the user wrote and the quote stays out of
    // reach (matching how the composer keeps them separate). Cc, attachments and
    // artifacts are restored too — dropping them was a silent data loss.
    if (p) {
      const { main, quoted } = splitQuotedText(p.bodyText || "");
      set({
        composeOpen: true,
        composeDefaults: {
          accountId: p.accountId,
          to: p.to.join(", "),
          subject: p.subject,
          replyToBody: main,
          quote: quoted || undefined,
          replyToMessageId: p.replyToMessageId,
          cc: p.cc?.length ? p.cc.join(", ") : undefined,
          attachments: p.attachments,
          artifacts: p.artifacts,
        },
      });
    }
  },

  saveDraft: async (params) => {
    // Reverse-sync write: create/update the provider draft + local mirror.
    const draft = await api.saveDraft(params);
    set((s) => {
      const exists = s.emails.some((e) => e.id === draft.id);
      if (exists) {
        // Update the row in place (editing an existing draft).
        return {
          emails: s.emails.map((e) => (e.id === draft.id ? { ...e, ...draft } : e)),
        };
      }
      // Only surface a brand-new draft in the list when the Drafts folder is the
      // active view — otherwise it would wrongly appear in inbox/etc. (it's still
      // persisted, so it shows on the next Drafts open). The in-thread DraftCard
      // is driven by the conversation refetch, not this list.
      if (s.selectedFolder === "drafts") {
        return { emails: [draft, ...s.emails], emailsTotal: s.emailsTotal + 1 };
      }
      return {};
    });
    return draft;
  },

  sendDraft: async (accountId, draftId) => {
    const prev = get().emails;
    // Optimistically remove the draft — it's leaving Drafts for Sent.
    set({
      emails: prev.filter((e) => e.id !== draftId),
      emailsTotal: Math.max(0, get().emailsTotal - 1),
    });
    try {
      await api.sendDraft(accountId, draftId);
      if (get().selectedEmailId === draftId) set({ selectedEmailId: null });
    } catch (err: any) {
      set({ emails: prev, error: err.message || "Failed to send draft" });
      throw err;
    }
  },

  triggerSync: async (accountId: string) => {
    // Cancel any catch-up refetches still pending from a previous sync.
    (_postSyncTimers[accountId] || []).forEach(clearTimeout);
    _postSyncTimers[accountId] = [];
    set({ syncStatus: { ...get().syncStatus, [accountId]: "syncing" } });
    // Demo: no backend to sync against — settle straight back to idle.
    if (DEMO) {
      set({ syncStatus: { ...get().syncStatus, [accountId]: "idle" } });
      return;
    }
    try {
      await api.triggerSync(accountId);
      // New mail is now persisted, but the server runs the rules / categorize /
      // Reply-Zero / auto-archive pipeline as a background task that finishes
      // just AFTER this response (H1 Option C). Show what we have immediately,
      // flip to "processing", then refetch a couple of times so the
      // labels/categories it applies (and any auto-archived mail) appear without
      // a manual refresh or waiting for the 20s background poll.
      set({ syncStatus: { ...get().syncStatus, [accountId]: "processing" } });
      get().fetchEmails();
      get().fetchAccounts();

      const stillProcessing = () =>
        get().syncStatus[accountId] === "processing";
      // softRefresh re-runs the CURRENT folder/search view (page 1) and merges
      // any new labels; fetchFolders picks up counts shifted by auto-archive.
      const catchUp = () => {
        if (!stillProcessing()) return;
        void get().softRefresh();
        void get().fetchFolders();
        // No read of the sums here: `syncScope` reads them once for the whole
        // Refresh (EM-T8f-3 review F2).
      };
      const t1 = setTimeout(catchUp, 2500);
      const t2 = setTimeout(() => {
        catchUp();
        if (stillProcessing()) {
          set({ syncStatus: { ...get().syncStatus, [accountId]: "idle" } });
        }
        _postSyncTimers[accountId] = [];
      }, 6000);
      _postSyncTimers[accountId] = [t1, t2];
    } catch (err: any) {
      // 409: another sync of this mailbox runs now (EM-T4f). The mailbox is
      // not in error, and the detail of the gateway says when new mail appears.
      const busy = err?.status === 409;
      set({
        syncStatus: { ...get().syncStatus, [accountId]: busy ? "idle" : "error" },
        error: err.message || "Sync failed",
      });
    }
  },

  deleteAccount: async (id) => {
    try {
      await api.deleteEmailAccount(id);
      // The gateway makes the oldest mailbox that is left the default
      // (`ORDER BY created_at, id`). Mirror it, so the star does not vanish
      // until the next read. `nextDefaultAfter` is the one rule, and the
      // dialog named the same mailbox. ⚠️ Never the first row: a default set
      // in this session moves the flag and not the row (EM-T8f-2).
      const before = get().accounts;
      const next = nextDefaultAfter(get().accounts, id);
      let accounts = before.filter((a) => a.id !== id);
      if (next) accounts = accounts.map((a) => ({ ...a, isDefault: a.id === next.id }));
      set({ accounts });
      // The one reconciliation of the pool. It clears the sums when the pool
      // changed (EM-T8f-3 review F4). In All inboxes it reads the pool, not
      // the count of mailboxes, so a separate mailbox never keeps All inboxes
      // open, and the hidden mailbox stays pooled (EM-T8g-2 review F1).
      const wasAll = get().viewAll;
      get().applyPoolChange(before);
      if (wasAll) {
        // The rows and the open mail of the removed mailbox left with the
        // reconciliation (EM-T8d review F8). The scope stays stored.
        persistAccountId(get().viewAll ? ALL_INBOXES : get().selectedAccountId);
      } else if (get().selectedAccountId === id) {
        const next = accounts.find((a) => a.isDefault)?.id ?? accounts[0]?.id ?? null;
        // A mail of the removed mailbox goes too, and the phone returns to
        // the inbox list (EM-T8a review).
        set({ selectedAccountId: next, selectedEmailId: null, selectedEmailOverride: null });
        persistAccountId(get().viewAll ? ALL_INBOXES : next);
        if (next) {
          get().fetchFolders(next);
          get().fetchLabels(next);
          get().fetchEmails();
        } else {
          // The last mailbox left. Clear what it drew, or its mail stays on
          // screen behind the empty state.
          set({ emails: [], emailsTotal: 0, folders: [], selectedEmailId: null });
        }
      }
      return { ok: true };
    } catch (err: unknown) {
      const detail = disconnectFailureText(err);
      set({ error: detail });
      // A refusal is about the row that was clicked. Re-read, so a stale row
      // does not read as a success or as a failure that did not happen.
      void get().refreshAccounts();
      return { ok: false, detail };
    }
  },

  setDefaultAccount: async (id) => {
    // Optimistically move the default flag, then persist to the backend.
    const prev = get().accounts;
    set({
      accounts: prev.map((a) => ({ ...a, isDefault: a.id === id })),
    });
    try {
      await api.setDefaultEmailAccount(id);
    } catch (err: any) {
      set({ accounts: prev, error: err.message || "Failed to set default account" });
    }
  },

  replaceAccount: (account) => {
    const before = get().accounts;
    set({ accounts: before.map((a) => (a.id === account.id ? account : a)) });
    // A copy from the server can carry another flag of the pool, so the one
    // reconciliation runs here too (EM-T8g-2 review round 2).
    get().applyPoolChange(before);
  },

  setInAllInboxes: async (id, pooled) => {
    let saved: EmailAccount;
    try {
      saved = await api.setMailboxPooled(id, pooled);
    } catch (err: unknown) {
      // A gateway before EM-T8g-1 refuses the field. Nothing moves, and the
      // list on screen is the server's again.
      const detail = err instanceof Error && err.message ? err.message : "The mailbox could not be changed.";
      set({ error: detail });
      void get().refreshAccounts();
      return false;
    }
    // Only the flag moves: the answer of the PATCH holds no default flag and
    // no unread count. The server's answer wins over the request.
    const before = get().accounts;
    set({ accounts: before.map((a) => (a.id === id ? withPoolFlag(a, !isSeparate(saved)) : a)) });
    // The one reconciliation: the end of All inboxes below two pooled
    // mailboxes, the rows that leave at once, and a re-read (item 3).
    get().applyPoolChange(before);
    return true;
  },

  applyPoolChange: (before) => {
    const st = get();
    const pooledNow = pooledMailboxes(st.accounts).map((a) => a.id);
    const pooledBefore = pooledMailboxes(before).map((a) => a.id);
    // A mailbox leaves the pool when it goes or turns separate. It joins when
    // it comes back, or when it is new.
    const out = pooledBefore.filter((id) => !pooledNow.includes(id));
    const joined = pooledNow.filter((id) => !pooledBefore.includes(id));
    const changed = out.length > 0 || joined.length > 0;
    // The sums belong to the pool before. They show no count until a new
    // round lands (EM-T8f-3 review F4).
    if (changed) set({ allFolderCounts: null });
    if (!st.viewAll) return;
    if (!hasAllInboxes(st.accounts)) {
      // Fewer than two pooled mailboxes: All inboxes ends for the default
      // mailbox (EM-T8g-2 item 3, review F1).
      const home = pickInitialView(st.accounts, ALL_INBOXES).accountId;
      if (home) {
        get().selectAccount(home);
      } else {
        // No mailbox is left. Nothing of the old list may stay, or act.
        set({
          viewAll: false, emails: [], emailsTotal: 0, selectedIds: new Set(),
          selectedEmailId: null, selectedEmailOverride: null,
        });
        persistAccountId(null);
      }
      return;
    }
    const hiddenPooled = pooledNow.includes(st.selectedAccountId ?? "");
    if (!changed && hiddenPooled) return;
    if (out.length > 0) {
      // The rows, the checks and the open mail of a mailbox that left go at
      // once. Only a check of a row that stays survives, so a check whose
      // row left the list before cannot reach a hidden mail either (review
      // F3, round 2).
      const emails = st.emails.filter((e) => !out.includes(e.accountId ?? ""));
      const open = st.selectedEmailOverride ?? st.emails.find((e) => e.id === st.selectedEmailId);
      set({
        emails,
        selectedIds: prunedChecks(st.selectedIds, emails) ?? st.selectedIds,
        ...(open && out.includes(open.accountId ?? "")
          ? { selectedEmailId: null, selectedEmailOverride: null } : {}),
      });
    }
    if (!hiddenPooled) {
      // The hidden mailbox stays pooled, so the folders and the labels of
      // All inboxes never come from a mailbox out of view (review F5).
      const home = poolHome(st.accounts);
      if (home) {
        set({ selectedAccountId: home.id, labelColors: readCachedLabelColors(home.id) });
        get().fetchFolders(home.id);
        get().fetchLabels(home.id);
      }
    }
    if (changed) {
      get().fetchEmails();
      void get().fetchAllFolderCounts();
    }
  },

  setPendingChatPrompt: (prompt) => set({ pendingChatPrompt: prompt }),

  runTestOnMessage: async (accountId, messageId, isTest) => {
    if (get().testRunningIds.includes(messageId)) return null;
    set({ testRunningIds: [...get().testRunningIds, messageId] });
    try {
      const res = await api.runRuleOnMessage({ accountId, messageId, isTest });
      set({ testResults: { ...get().testResults, [messageId]: res } });
      // Classifier down = a BACKEND fault, not a rules gap — say so in the
      // banner so a failed recategorize is never misread as "fix your rules".
      if (res.unavailable) {
        set({ error: res.reason || "The AI classifier is temporarily unavailable." });
      }
      // Apply mode (isTest=false): reflect the freshly-applied category/folder
      // in the loaded list so the "Uncategorized" pill in the inbox row resolves
      // immediately. Before this, the apply wrote em.categories in the DB but the
      // Zustand snapshot went untouched, so the pill stayed "Uncategorized" until
      // a navigation-triggered refetch — the exact "clicking it did nothing" bug.
      // The Test/dry-run path changes nothing, so it never patches.
      if (!isTest && res.applied) {
        set({
          emails: get().emails.map((e) =>
            e.id === messageId
              ? {
                  ...e,
                  categories: res.categories ?? e.categories,
                  folder: res.folder ?? e.folder,
                }
              : e,
          ),
        });
      }
      return res;
    } catch (err: any) {
      set({ error: err?.message || "Rule run failed" });
      return null;
    } finally {
      set({ testRunningIds: get().testRunningIds.filter((id) => id !== messageId) });
    }
  },

  runTestOnAll: async (accountId, messageIds, isTest) => {
    if (get().testBulkRunning) return;
    _stopTestRun = false;
    set({ testBulkRunning: true, testApplyMode: isTest ? get().testApplyMode : true });
    try {
      for (const id of messageIds) {
        if (_stopTestRun) break;
        await get().runTestOnMessage(accountId, id, isTest);
      }
    } finally {
      set({ testBulkRunning: false });
    }
  },

  stopTestRun: () => {
    _stopTestRun = true;
  },

  clearTestResults: () => set({ testResults: {} }),

  clearError: () => set({ error: null }),
}));
