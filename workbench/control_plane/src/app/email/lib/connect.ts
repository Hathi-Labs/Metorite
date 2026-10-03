/**
 * The connect flow of the Email app, as pure decisions (WS-17 EM-T3b).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.3 and §10.4.3.
 *
 * Every choice the connect UI makes lives here, because vitest in this tree
 * runs in the node environment and cannot render a component. The pages
 * (`app/email/page.tsx`, `app/email/oauth/callback/page.tsx`) and
 * `components/ConnectChoices.tsx` only draw what these functions return.
 *
 * Fence: `connect.test.ts`.
 */

import type { EmailAccount } from "./types";

// ── The providers a member may connect ──────────────────────────────────────

export type ConnectProviderId = "microsoft" | "gmail";

export interface ConnectProvider {
  id: ConnectProviderId;
  label: string;
  detail: string;
  /** False draws the choice disabled, with `note` beside it. */
  available: boolean;
  note?: string;
  /** A Lucide name. Never a brand colour (DESIGN_SYSTEM.md §1). */
  icon: string;
}

/**
 * The choices of the empty state and of the add-account dialog, in order.
 *
 * Microsoft is the one live path (D-EM-1). Google waits for its own app
 * verification, so it shows and cannot be clicked. IMAP is not offered: the
 * old button went to a URL that nothing read.
 */
export const CONNECT_PROVIDERS: readonly ConnectProvider[] = [
  {
    id: "microsoft",
    label: "Microsoft 365 / Outlook",
    detail: "Sign in with your work or personal Microsoft account",
    available: true,
    icon: "Building2",
  },
  {
    id: "gmail",
    label: "Google / Gmail",
    detail: "Google Workspace and Gmail",
    available: false,
    note: "Coming soon",
    icon: "Mail",
  },
];

/**
 * The query of the BFF authorize route.
 *
 * `login_hint` is a hint for the provider's sign-in page, never an identity:
 * the gateway drops a value that does not parse as an address, and the
 * callback still binds the member of the session (EM-T3a item 3).
 *
 * `importMonths` is the range of the first import (EM-T6d item 3). Only a
 * first connect sends it. A reconnect keeps the range of the mailbox
 * (D-EM-13), so the reconnect banner passes none. A value that is not a
 * whole number from 0 to 6 is not sent, and the gateway then uses 1.
 */
export function connectQuery(
  redirectAfter: string,
  loginHint?: string | null,
  importMonths?: number | null,
): string {
  const params = new URLSearchParams({ redirect_after: redirectAfter });
  const hint = (loginHint ?? "").trim();
  if (hint) params.set("login_hint", hint);
  if (isImportMonths(importMonths)) params.set("import_months", String(importMonths));
  return params.toString();
}

// ── The range of the first import (EM-T6d item 2, D-EM-10) ─────────────────

/** The longest range a member may choose. Metorite imports nothing older. */
export const MAX_IMPORT_MONTHS = 6;

/** The range the step selects when it opens. The gateway default is also 1. */
export const DEFAULT_IMPORT_MONTHS = 1;

/** True for a whole number from 0 to `MAX_IMPORT_MONTHS`. */
export function isImportMonths(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= MAX_IMPORT_MONTHS;
}

export interface ImportRangeChoice {
  months: number;
  label: string;
}

/** The seven choices of the range step, 0 to 6 months, in order. */
export const IMPORT_RANGE_CHOICES: readonly ImportRangeChoice[] = Array.from(
  { length: MAX_IMPORT_MONTHS + 1 },
  (_, months) => ({
    months,
    label: months === 0 ? "Only new mail" : months === 1 ? "1 month" : `${months} months`,
  }),
);

/** The words of the range step. */
export const IMPORT_RANGE_COPY = {
  title: "How much of your mail should Metorite import?",
  body:
    "Metorite imports the mail that you received in this range. After that, it gets each new message as it arrives. " +
    `Metorite never imports mail older than ${MAX_IMPORT_MONTHS} months.`,
  back: "Back",
  /** The button that starts the sign-in, for each provider. */
  continueTo: (provider: ConnectProviderId) =>
    provider === "microsoft" ? "Continue to Microsoft" : "Continue to Google",
} as const;

/**
 * Where the range step keeps the last range the member chose (fix round 1).
 *
 * A convenience only. A failed connect sends the member back to the range
 * step (`retryTarget`), and the step then shows the range they chose before.
 * It is `sessionStorage`, so it ends with the tab. It holds one digit and no
 * tenant data. The gateway never reads it: the range goes in the query.
 */
export const IMPORT_MONTHS_STORAGE_KEY = "metorite.email.importMonths";

type MonthsStore = Pick<Storage, "getItem" | "setItem">;

/** The session storage of this tab, or null where it is absent or refused. */
function sessionStore(): MonthsStore | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    // A browser can refuse storage with a SecurityError.
    return null;
  }
}

/**
 * The range the step selects when it opens: the stored one, or 1.
 *
 * Only one digit from 0 to 6 counts, the same rule as the BFF. Any other
 * value, no value, no storage or a storage that throws gives the default.
 */
export function storedImportMonths(store: MonthsStore | null = sessionStore()): number {
  try {
    const raw = store?.getItem(IMPORT_MONTHS_STORAGE_KEY) ?? null;
    return raw !== null && /^[0-6]$/.test(raw) ? Number(raw) : DEFAULT_IMPORT_MONTHS;
  } catch {
    return DEFAULT_IMPORT_MONTHS;
  }
}

/** Keeps the chosen range for the next time the step opens. Never throws. */
export function rememberImportMonths(months: number, store: MonthsStore | null = sessionStore()): void {
  if (!isImportMonths(months)) return;
  try {
    store?.setItem(IMPORT_MONTHS_STORAGE_KEY, String(months));
  } catch {
    // A full or refused storage only costs the convenience.
  }
}

/** Which provider the reconnect banner may send the member back through. */
export function reconnectProvider(account: Pick<EmailAccount, "provider">): ConnectProviderId | null {
  return account.provider === "microsoft" || account.provider === "gmail"
    ? account.provider
    : null;
}

// ── Which surface the page draws ───────────────────────────────────────────

export type EmailSurface = "loading" | "empty" | "mailbox";

/**
 * What `/email` draws: the loading shell, "Connect your email", or the panes.
 *
 * ⚠️ "empty" needs the FIRST account read to have settled. Before it, the
 * store holds `[]` because nothing was read yet, not because the member has
 * no mailbox, and drawing the empty state then flashes it on every hard load.
 */
export function emailSurface(state: { loaded: boolean; loading: boolean; count: number }): EmailSurface {
  if (state.count > 0) return "mailbox";
  if (!state.loaded || state.loading) return "loading";
  return "empty";
}

/** True for a provider that a member can connect today. */
function isLiveProvider(id: string | null): id is ConnectProviderId {
  return CONNECT_PROVIDERS.some((p) => p.id === id && p.available);
}

/**
 * Where "Try again" and "Approved? Connect again" on the callback page go.
 *
 * ⚠️ Never straight into the OAuth leg (fix round 1). The callback page shows
 * those buttons before any mailbox row exists, so each one starts a FIRST
 * connect. A direct authorize call carries no `import_months`, and the
 * gateway default of 1 month then replaces the range the member chose. So a
 * live provider goes back to the range step, which opens with the stored
 * range (`storedImportMonths`). A provider that is not available (Gmail,
 * "Coming soon") goes to the connect choices only.
 *
 * `/email?connect=1` opens the add-account dialog, or the empty state shows
 * the same choices. `provider` opens the range step of that provider.
 */
export function retryTarget(provider: ConnectProviderId): string {
  if (!isLiveProvider(provider)) return "/email?connect=1";
  return `/email?${new URLSearchParams({ connect: "1", provider }).toString()}`;
}

/** True when the URL asks the page to open the connect choices. */
export function wantsConnectChoices(search: string): boolean {
  return new URLSearchParams(search).get("connect") === "1";
}

/**
 * The provider whose range step the URL asks to open, or null.
 *
 * Only with `connect=1`, and only for a live provider. Any other value is
 * request input that names nothing, so it opens the list.
 */
export function rangeStepProviderFrom(search: string): ConnectProviderId | null {
  if (!wantsConnectChoices(search)) return null;
  const provider = new URLSearchParams(search).get("provider");
  return isLiveProvider(provider) ? provider : null;
}

// ── First sync ─────────────────────────────────────────────────────────────

/** How often the page re-reads the accounts while a first sync runs. */
export const FIRST_SYNC_POLL_MS = 5000;

// `syncEnabled` is optional here: a caller that does not know it is not paused.
type SyncFlags = Pick<EmailAccount, "initialSyncDone" | "syncStatus"> &
  Partial<Pick<EmailAccount, "syncEnabled">>;

/**
 * An account whose first sync is still running.
 *
 * Only an explicit `false` counts. A gateway that predates EM-T3a sends no
 * flag, and an account with no flag must not spin for ever.
 *
 * ⚠️ An account in `syncStatus === "error"` is NOT pending. A first sync that
 * failed leaves `initial_sync_done` false for good, so counting it would poll
 * every few seconds for ever and show "bringing in your mail" over a mailbox
 * that brings in nothing. The reconnect banner owns that state.
 *
 * ⚠️ A mailbox with sync OFF is not pending either (EM-T6b review). The
 * scheduler skips it, so a first sync paused part way would freeze the
 * banner and keep the poll running.
 */
export function isFirstSyncPending(account: SyncFlags): boolean {
  return (
    account.initialSyncDone === false &&
    account.syncStatus !== "error" &&
    account.syncEnabled !== false
  );
}

/** True while any account still runs its first sync, so the page polls. */
export function shouldPollFirstSync(accounts: ReadonlyArray<SyncFlags>): boolean {
  return accounts.some(isFirstSyncPending);
}

/** Ids whose first sync finished between two reads of the account list. */
export function finishedFirstSync(
  before: ReadonlyArray<Pick<EmailAccount, "id"> & SyncFlags>,
  after: ReadonlyArray<Pick<EmailAccount, "id" | "initialSyncDone">>,
): string[] {
  const done = new Set(after.filter((a) => a.initialSyncDone === true).map((a) => a.id));
  return before.filter((a) => isFirstSyncPending(a) && done.has(a.id)).map((a) => a.id);
}

export type FirstSyncTickResult = "hidden" | "failed" | "finished" | "pending";

/**
 * One tick of the first-sync poll. The page calls it from its interval and
 * again when the tab becomes visible.
 *
 * A hidden tab makes no request: a member who leaves the tab open overnight
 * must not re-read the accounts every five seconds. When the selected account
 * finishes, `onFinished` loads its folders and its mail. The scheduler commits
 * a sync in one transaction, so there is nothing to show before that.
 */
export async function firstSyncTick(deps: {
  hidden: () => boolean;
  before: () => ReadonlyArray<Pick<EmailAccount, "id"> & SyncFlags>;
  refresh: () => Promise<ReadonlyArray<Pick<EmailAccount, "id" | "initialSyncDone">> | null>;
  selected: () => string | null;
  onFinished: (id: string) => void;
}): Promise<FirstSyncTickResult> {
  if (deps.hidden()) return "hidden";
  const before = deps.before();
  const after = await deps.refresh();
  if (!after) return "failed";
  const selected = deps.selected();
  if (selected && finishedFirstSync(before, after).includes(selected)) {
    deps.onFinished(selected);
    return "finished";
  }
  return "pending";
}

export function firstSyncCopy(address: string): { title: string; body: string } {
  return {
    title: `Connected as ${address}`,
    body: "Metorite is bringing in your mail. This can take a few minutes for a large mailbox, and you can keep working.",
  };
}

// ── The callback page ─────────────────────────────────────────────────────

export type CallbackKind =
  | "loading"
  | "connected"
  | "admin_consent_required"
  | "consent_declined"
  | "duplicate"
  | "retry"
  | "unknown";

export interface CallbackView {
  kind: CallbackKind;
  title: string;
  body: string;
  /** A short code the member can give to support. Never provider text. */
  reference?: string;
}

/** A code that is safe to print: the gateway's own vocabulary. */
const PLAIN_CODE = /^[a-z0-9_]{1,64}$/;

/** Codes that a second try usually fixes. */
const RETRY_CODES = new Set([
  "invalid_state",
  "token_exchange_failed",
  "email_fetch_failed",
  "account_save_failed",
  "gateway_unreachable",
]);

/**
 * What the callback page says, for each result the gateway can send.
 *
 * ⚠️ An unknown code never reaches the page as text. The BFF authorize route
 * passes a refusal's `detail` as `error`, and that can be a whole sentence
 * that names a screen the member cannot open. Only a plain code shows, and
 * only as a reference.
 */
export function callbackView(params: {
  error: string | null;
  accountId: string | null;
  email: string | null;
}): CallbackView {
  const { error, accountId, email } = params;
  if (!error) {
    if (!accountId) {
      return { kind: "loading", title: "Finishing the connection", body: "One moment." };
    }
    return {
      kind: "connected",
      title: email ? `Connected as ${email}` : "Mailbox connected",
      body: "Metorite starts to bring in your mail now. Recent messages come first.",
    };
  }
  if (error === "admin_consent_required") {
    return {
      kind: "admin_consent_required",
      title: "Your organization needs to approve Metorite",
      body:
        "Your Microsoft 365 organization lets only an IT admin approve new apps. " +
        "Send your admin the approval link below. One approval covers everyone in your company. " +
        "When it is done, connect again.",
    };
  }
  if (error === "consent_declined") {
    return {
      kind: "consent_declined",
      title: "You cancelled the connection",
      body:
        "Microsoft did not give Metorite access to your mailbox, so nothing was connected. " +
        "Try again when you are ready. Metorite reads and sends mail only as you tell it to.",
    };
  }
  if (error === "duplicate") {
    return {
      kind: "duplicate",
      title: "This mailbox is already connected",
      body: email
        ? `${email} is already in your Email app.`
        : "This mailbox is already in your Email app.",
    };
  }
  const reference = PLAIN_CODE.test(error) ? error : undefined;
  if (RETRY_CODES.has(error)) {
    return {
      kind: "retry",
      title: "The connection did not finish",
      body:
        "Something interrupted the sign-in. This is usually temporary. " +
        "Try again. If it happens again, tell your Metorite admin.",
      reference,
    };
  }
  return {
    kind: "unknown",
    title: "We could not connect your mailbox",
    body:
      "Microsoft or Metorite stopped the connection. Try again. " +
      "If it happens again, give your Metorite admin the reference below.",
    reference,
  };
}

/** The line above the admin-approval help after a Microsoft decline. */
export const ADMIN_APPROVAL_AFTER_DECLINE =
  'Did Microsoft say "Need admin approval"? Your IT admin approves Metorite once for your company.';

/**
 * Whether the callback page shows the admin-approval help, and its lead line.
 *
 * `admin_consent_required` shows the help with no lead, as before. A
 * Microsoft `consent_declined` shows it too, after the declined words. The
 * reason: on Microsoft's "Need admin approval" screen, "Return to the
 * application without granting consent" sends a bare `access_denied`. That
 * carries no AADSTS code, so the gateway reads it as `consent_declined`
 * (`_consent_error_reason`). In practice the company needs its admin. A
 * decline from any other provider shows no Microsoft text.
 */
export function adminApprovalHelp(
  kind: CallbackKind,
  provider: ConnectProviderId,
): { lead: string | null } | null {
  if (kind === "admin_consent_required") return { lead: null };
  if (kind === "consent_declined" && provider === "microsoft") {
    return { lead: ADMIN_APPROVAL_AFTER_DECLINE };
  }
  return null;
}

// ── Admin consent ──────────────────────────────────────────────────────────

/** The public facts of the mail app, from `GET /email/oauth/microsoft/app`. */
export interface MailAppInfo {
  clientId: string;
  redirectUri: string;
}

export function mapMailAppInfo(raw: unknown): MailAppInfo | null {
  const r = (raw ?? {}) as Record<string, unknown>;
  const clientId = typeof r.client_id === "string" ? r.client_id.trim() : "";
  const redirectUri = typeof r.redirect_uri === "string" ? r.redirect_uri.trim() : "";
  if (!clientId || !redirectUri) return null;
  return { clientId, redirectUri };
}

/**
 * The Microsoft admin-consent link for the mail app.
 *
 * `organizations`, not a tenant id: the admin signs in to their own
 * directory, and Microsoft resolves it. `.default` asks for every permission
 * the app registration lists, so the admin approves one complete set.
 */
export function adminConsentUrl(app: MailAppInfo): string {
  const params = new URLSearchParams({
    client_id: app.clientId,
    scope: "https://graph.microsoft.com/.default",
    redirect_uri: app.redirectUri,
  });
  return `https://login.microsoftonline.com/organizations/v2.0/adminconsent?${params.toString()}`;
}

/** The prefilled request to an IT admin, as a `mailto:` link. */
export function adminConsentMailto(link: string): string {
  const subject = "Please approve Metorite for Microsoft 365 mail";
  const body = [
    "Hello,",
    "",
    "I want to connect my Microsoft 365 mailbox to Metorite. Our organization lets only an admin approve new apps.",
    "",
    "Please open this link and sign in with an admin account to approve it:",
    link,
    "",
    "One approval covers everyone in our organization. Metorite asks for permission to read, send and organize the mail of each member who connects.",
    "",
    "Thank you.",
  ].join("\n");
  // encodeURIComponent, not URLSearchParams: a mail client reads `+` as a
  // plus sign, not as a space.
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

// ── Disconnect ─────────────────────────────────────────────────────────────

export function disconnectCopy(address: string): { title: string; body: string; note: string; confirm: string } {
  return {
    title: "Disconnect this mailbox?",
    body:
      `Metorite stops syncing ${address} and deletes its sign-in tokens. ` +
      "The synced mail, rules and AI settings of this mailbox are deleted from Metorite.",
    note: "Your mail stays in your Microsoft or Google mailbox. To use it here again, connect it again.",
    confirm: "Disconnect",
  };
}

/** What a disconnect gives back. On a refusal, `detail` is the text to show. */
export type DisconnectOutcome = { ok: true } | { ok: false; detail: string };

/** The text for a refusal that carries no reason of its own. */
export const DISCONNECT_FALLBACK = "Metorite could not disconnect the mailbox. Try again.";

/**
 * The text a member reads when a disconnect fails (WS-17 EM-T4f).
 *
 * Only an answer of the gateway carries a `status`, and `gatewayFetch` puts
 * its `detail` in the message. A 409 then says that a sync is still writing
 * mail. A gateway answer with no detail ("Gateway error 500") and an error
 * with no status (the network) get the fallback text.
 */
export function disconnectFailureText(err: unknown): string {
  const e = (err ?? {}) as { status?: unknown; message?: unknown };
  const detail = typeof e.message === "string" ? e.message.trim() : "";
  if (typeof e.status !== "number" || !detail || /^Gateway error \d+$/.test(detail)) {
    return DISCONNECT_FALLBACK;
  }
  return detail;
}
