// ── The Integrations console: the connection rows and the rules on them ────
//
// Spec: project-docs/specs/integrations_console.md §3 and §7.2 (IN-2).
//
// The gateway builds one row shape from three sources (`GET
// /integrations/connections`). This file holds everything the console decides
// about a row, as pure functions with tests: the filters, the words, the
// actions a row offers, and the copy of the confirm dialog.
//
// ⚠️ A row never holds a credential (spec §3.3 rule 4). Nothing here reads or
// stores a secret value. The MCP form below is write-only: it builds a body
// and forgets the header value.

import type { Access } from "@/lib/access";
import { hasCapability } from "@/lib/access";
import { relativeTime } from "@/lib/taskCard";

export type ConnectionKind = "oauth" | "mailbox" | "whatsapp" | "mcp" | "api_key";
export type OwningApp = "crm" | "email" | "whatsapp" | "integrations";
export type ConnectionStatus =
  | "connected"
  | "needs_reconnect"
  | "disconnected"
  | "error"
  | "configured";

export interface Connection {
  id: string;
  kind: ConnectionKind;
  provider: string;
  label: string;
  owning_app: OwningApp;
  scope: "org" | "member";
  owner: string | null;
  status: ConnectionStatus;
  last_sync_at: string | null;
  last_error: string | null;
  setup_href: string | null;
  can_disconnect: boolean;
  detail: Record<string, string>;
  /**
   * WS-54 IN-8c (D98): the admin that an OAuth MCP connection acts as. The
   * gateway sends it only while `MCP_OAUTH_ENABLED` is on. It is member data,
   * so it is drawn as text and never as markup.
   */
  acts_as?: string | null;
  /** IN-8c: this member may start an OAuth consent for the row. */
  can_connect?: boolean;
}

// ── Words ─────────────────────────────────────────────────────────────────

export const APP_LABEL: Record<OwningApp, string> = {
  crm: "CRM",
  email: "Email",
  whatsapp: "WhatsApp",
  integrations: "Integrations",
};

export const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connected: "Connected",
  needs_reconnect: "Needs reconnect",
  disconnected: "Disconnected",
  error: "Error",
  configured: "Configured",
};

/** The colour NAME of each status. `statusAccent` turns it into classes. */
export const STATUS_COLOR: Record<ConnectionStatus, string> = {
  connected: "green",
  needs_reconnect: "amber",
  disconnected: "gray",
  error: "red",
  configured: "blue",
};

const ERROR_TEXT: Record<string, string> = {
  token_expired: "The sign-in expired. Connect again.",
  invalid_grant: "The sign-in was revoked. Connect again.",
  auth_failed: "The account refused the sign-in.",
  unauthorized: "The account refused the sign-in.",
  rate_limited: "The provider is slowing requests. It will retry.",
  unreachable: "The server did not answer.",
  timeout: "The server took too long to answer.",
  sync_failed: "The last sync failed.",
};

/** A human line for a stored error code. Unknown codes still get a sentence. */
export function errorMessage(code: string | null | undefined): string {
  if (!code) return "";
  return ERROR_TEXT[code] ?? `The last sync failed (${code}).`;
}

/** "Org" or the member's address. */
export function scopeLabel(row: Pick<Connection, "scope" | "owner">): string {
  if (row.scope === "org") return "Org";
  return row.owner || "Member";
}

/** The last sync, as "4m ago", or "Never". */
export function lastSyncLabel(iso: string | null, nowMs = Date.now()): string {
  return relativeTime(iso, nowMs) || "Never";
}

// ── Filters ───────────────────────────────────────────────────────────────

export type AppFilter = "all" | OwningApp;
export type StatusFilter = "all" | ConnectionStatus;

export const APP_ORDER: OwningApp[] = ["crm", "email", "whatsapp", "integrations"];
export const STATUS_ORDER: ConnectionStatus[] = [
  "connected",
  "configured",
  "needs_reconnect",
  "error",
  "disconnected",
];

export function filterConnections(
  rows: readonly Connection[],
  app: AppFilter,
  status: StatusFilter,
): Connection[] {
  return rows.filter(
    (r) => (app === "all" || r.owning_app === app) && (status === "all" || r.status === status),
  );
}

export interface PillItem {
  id: string;
  label: string;
  count: number;
}

/** App pills with counts. An app with no rows has no pill. */
export function appPills(rows: readonly Connection[]): PillItem[] {
  const pills: PillItem[] = [{ id: "all", label: "All apps", count: rows.length }];
  for (const app of APP_ORDER) {
    const count = rows.filter((r) => r.owning_app === app).length;
    if (count > 0) pills.push({ id: app, label: APP_LABEL[app], count });
  }
  return pills;
}

/** Status pills with counts, for the rows the app filter keeps. */
export function statusPills(rows: readonly Connection[], app: AppFilter): PillItem[] {
  const inApp = filterConnections(rows, app, "all");
  const pills: PillItem[] = [{ id: "all", label: "Any status", count: inApp.length }];
  for (const status of STATUS_ORDER) {
    const count = inApp.filter((r) => r.status === status).length;
    if (count > 0) pills.push({ id: status, label: STATUS_LABEL[status], count });
  }
  return pills;
}

/** Rows that need an admin: the shell's "needs" signal (spec §5). */
export function needsAttention(rows: readonly Connection[]): number {
  return rows.filter((r) => r.status === "needs_reconnect" || r.status === "error").length;
}

// ── Who may do what (spec §3.5) ───────────────────────────────────────────

export const READ_DENIED = "Only admins can see your company's connections.";

export interface ConsoleAccess {
  canRead: boolean;
  canManage: boolean;
}

/** The read floor is the admin test `/auth/me` reports as `is_admin`. */
export function consoleAccess(access: Access): ConsoleAccess {
  return {
    canRead: Boolean(access.is_admin) || hasCapability(access, "admin:members:read"),
    canManage: hasCapability(access, "integrations:manage"),
  };
}

// ── The side panel ────────────────────────────────────────────────────────

export interface PanelPlan {
  /** "Open in CRM". Null when the row has no `setup_href`, or the console owns it. */
  openLabel: string | null;
  href: string | null;
  /** "Connect again" for a row that needs it. The app owns the flow. */
  reconnect: boolean;
  /** Where "Connect again" goes. A mailbox names its address (`login_hint`). */
  reconnectHref: string | null;
  disconnect: boolean;
  test: boolean;
  /**
   * The H-300 switch of an MCP server: `"edit"` for a manager, `"view"` for
   * a reader, `null` on a row that is not an MCP server.
   */
  alwaysConfirm: "edit" | "view" | null;
  /**
   * MCP-C2: the read-only key of an MCP server, with the same rule. A row
   * that acts as an admin through OAuth gets `"view"`, because the gateway
   * refuses the key there (D98).
   */
  readOnlyKey: "edit" | "view" | null;
  /** The extra line under the read-only key on an OAuth row, or null. */
  readOnlyKeyNote: string | null;
  /**
   * IN-8c: the OAuth consent of an MCP server. `"connect"` for a server
   * with no consent yet, `"reconnect"` for one that has one or needs one,
   * `null` when the row offers none.
   */
  connect: "connect" | "reconnect" | null;
  /** IN-8c: "Acts as <admin> at <host>", or null. */
  actsAs: string | null;
  /** For a member row: who manages it, and where. */
  memberNote: string | null;
}

/**
 * "Connect again" opens the owning app. For a mailbox the app gets the
 * address as `login_hint`, so the member sees no account picker (MB-1).
 */
export function reconnectHref(row: Connection): string | null {
  if (row.kind === "mailbox" && row.status === "needs_reconnect") {
    return `/email?connect=1&login_hint=${encodeURIComponent(row.label)}`;
  }
  return row.setup_href;
}

export function panelPlan(row: Connection, canManage: boolean): PanelPlan {
  const app = APP_LABEL[row.owning_app];
  const connect = mcpConnect(row, canManage);
  const switches = row.kind === "mcp" && mcpName(row) !== null ? (canManage ? "edit" : "view") : null;
  // MCP-C2 review (P2): the gateway refuses the key on an OAuth row, so the
  // tick there is disabled, with a line that says why.
  const oauth = switches !== null && mcpActsThroughOAuth(row);
  return {
    openLabel: row.setup_href && row.owning_app !== "integrations" ? `Open in ${app}` : null,
    href: row.setup_href,
    // An MCP consent replaces the link: it would only open this page again.
    reconnect: Boolean(row.setup_href) && row.status === "needs_reconnect" && connect === null,
    reconnectHref: reconnectHref(row),
    disconnect: canManage && row.can_disconnect,
    test: canManage && row.kind === "mcp" && mcpName(row) !== null,
    alwaysConfirm: switches,
    readOnlyKey: oauth ? "view" : switches,
    readOnlyKeyNote: oauth ? READ_ONLY_KEY_OAUTH_NOTE : null,
    memberNote:
      row.scope === "member" ? `${row.owner || "The owner"} manages this connection in ${app}.` : null,
    connect,
    actsAs: actsAsLine(row),
  };
}

// ── MCP OAuth: Connect, Reconnect and the acts-as label (IN-8c, D98) ──────
//
// Spec: integrations_console.md §3.6.9. An admin connects an MCP server that
// asks for an OAuth consent. The connection then acts as that admin at the
// provider, for the agents of every member. The gateway decides who may
// connect (`can_connect`) and names the admin (`acts_as`). Both are absent
// while `MCP_OAUTH_ENABLED` is off, so nothing here draws then.

/** Where the consent acts: the host of the server, else its label. */
function actsAtHost(row: Pick<Connection, "detail" | "label">): string {
  return row.detail?.host || row.label;
}

/** "Acts as <admin> at <host>" for an OAuth MCP row, or null. */
export function actsAsLine(
  row: Pick<Connection, "kind" | "acts_as" | "detail" | "label">,
): string | null {
  const admin = (row.acts_as ?? "").trim();
  if (row.kind !== "mcp" || !admin) return null;
  return `Acts as ${admin} at ${actsAtHost(row)}`;
}

/**
 * The consent this row offers to this member. Both the gateway flag and the
 * console's own `integrations:manage` test must say yes, so a reader never
 * sees the button. An OAuth row and a row that needs a new consent read
 * "Reconnect".
 */
export function mcpConnect(row: Connection, canManage: boolean): "connect" | "reconnect" | null {
  if (!canManage || row.kind !== "mcp" || row.can_connect !== true || mcpName(row) === null) {
    return null;
  }
  return row.acts_as || row.status === "needs_reconnect" ? "reconnect" : "connect";
}

/** The button words of each offer. */
export const CONNECT_LABEL: Record<"connect" | "reconnect", string> = {
  connect: "Connect",
  reconnect: "Reconnect",
};

/**
 * The words of the Connect dialog, before the consent (§3.6.9). They say
 * that every member's agents use the connection as this admin, and that a
 * read-only tool runs with no card. `admin` is the member who clicks, who is
 * the admin the connection will act as.
 */
export function connectDialogCopy(
  row: Pick<Connection, "acts_as" | "detail" | "label">,
  admin: string,
  mode: "connect" | "reconnect",
): ConfirmCopy & { note: string } {
  const host = actsAtHost(row);
  const who = admin.trim() || "you";
  const before = (row.acts_as ?? "").trim();
  const change =
    mode === "reconnect" && before && before.toLowerCase() !== who.toLowerCase()
      ? ` It acts as ${before} now. After your sign-in, it acts as you.`
      : "";
  const alwaysConfirm = row.detail?.always_confirm === "true";
  // MCP-C2: the gateway reads the read-only key as off on a connection that
  // acts as an admin (D98), so the words say that the switch stops.
  const keyStops =
    row.detail?.read_only_key === "true"
      ? `The switch "${READ_ONLY_KEY_LABEL}" does not apply to a connection that acts as ${who}, ` +
        "so it stops after your sign-in. "
      : "";
  return {
    title: `${CONNECT_LABEL[mode]} ${row.label}?`,
    body:
      // The provider's sign-in can sit on another host (GitHub, Slack), so
      // the words do not name a host for it.
      `Metorite opens the provider's sign-in page for ${host}. Sign in there with your own account.\n\n` +
      `Then this connection acts as ${who} at ${host}. ` +
      `The agents of every member in your organization use it as ${who}.${change}`,
    note: alwaysConfirm
      ? "This server is set to always confirm, so each tool waits for a member to approve it. " +
        "If you turn that switch off, a tool that the server marks as read-only runs with no approval."
      : keyStops +
        "A tool that the server marks as read-only runs with no approval. So every member can " +
        `read, through an agent, what ${who} can read at ${host}.`,
    confirmLabel: "Open the sign-in",
  };
}

const START_ERROR_TEXT: Record<string, string> = {
  no_registration: "This server does not let Metorite register as a client.",
  pkce_unsupported: "This server does not support a safe sign-in (PKCE S256).",
  discovery_failed: "Metorite cannot find the sign-in of this server.",
  no_auth_server: "This server names no sign-in server.",
  issuer_mismatch: "The sign-in server of this server does not give its own name.",
  resource_mismatch: "The sign-in data of this server names another server.",
  registration_failed: "This server refused the registration of Metorite.",
  refused: "Metorite cannot reach this address. Use a public https URL.",
  insecure_endpoint: "The sign-in of this server is not on https.",
  unreachable: "The server did not answer.",
  no_server: "This server is gone. Refresh the list.",
  signing_unavailable: "Metorite cannot start a sign-in now. Try again later.",
  pending_unavailable: "Metorite cannot start a sign-in now. Try again later.",
  not_available: "MCP sign-in is not on for your organization.",
};

/**
 * The text of `code` in `table`, from an OWN key only, else null.
 *
 * ⚠️ `table[code]` reads the object prototype. A code from a URL or a server
 * such as `__proto__` gives `Object.prototype`, and React throws on an
 * object child, so one crafted link crashed the page (review of #1023).
 * `constructor` and `toString` gave a function and a blank toast.
 */
function ownText(table: Record<string, string>, code: string | null | undefined): string | null {
  if (typeof code !== "string" || !Object.prototype.hasOwnProperty.call(table, code)) return null;
  return table[code];
}

/** A line for a refused start. An unknown code still gets a sentence. */
export function startErrorMessage(code: string | null | undefined): string {
  return ownText(START_ERROR_TEXT, code) ?? "Metorite cannot start the sign-in.";
}

const RETURN_ERROR_TEXT: Record<string, string> = {
  access_denied: "The sign-in was cancelled at the provider.",
  invalid_state: "The sign-in expired or came from another session. Try again.",
  invalid_issuer: "The sign-in came back from the wrong server, so Metorite refused it.",
  server_gone: "The server changed during the sign-in. Nothing was saved.",
  exchange_failed: "The provider refused the sign-in code.",
};

/** The gateway's MCP server name rule (`connections/mcp.py` NAME_RE). */
const MCP_NAME = /^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$/;

/** True when the gateway serves MCP OAuth: a row carries its keys. */
export function mcpOAuthOn(rows: readonly Connection[] | null | undefined): boolean {
  return (rows ?? []).some(
    (r) => r.kind === "mcp" && (r.can_connect === true || Boolean((r.acts_as ?? "").trim())),
  );
}

/**
 * The toast after the consent: the gateway callback sends the browser to
 * `/integrations?mcp_connected=<name>` or `?mcp_connect_error=<code>` (the
 * BFF callback allows only these two shapes). `null` when neither is here.
 *
 * Review of #1023: anybody can send a link with these params. So the toast
 * shows only while the gateway serves MCP OAuth (`mcpOAuthOn`, which reads
 * the loaded rows). A success names a server of the loaded list by its row
 * label, and never echoes the name from the URL. An unknown name is the
 * generic failure. The caller waits until the list has loaded.
 */
export function consentReturn(
  params: { get(name: string): string | null },
  rows: readonly Connection[] | null | undefined,
): { ok: boolean; title: string } | null {
  const name = params.get("mcp_connected");
  const code = params.get("mcp_connect_error");
  if (name === null && code === null) return null;
  if (!mcpOAuthOn(rows)) return null;
  const row =
    name !== null && MCP_NAME.test(name)
      ? (rows ?? []).find((r) => r.kind === "mcp" && mcpName(r) === name)
      : undefined;
  if (row) return { ok: true, title: `${rowLabel(row)} is connected.` };
  return {
    ok: false,
    title: ownText(RETURN_ERROR_TEXT, code) ?? "The sign-in did not finish. Nothing was saved.",
  };
}

/** The `detail` fields as label and value pairs. Keys read as words. */
export function detailRows(row: Connection): { label: string; value: string }[] {
  // `key_names` shows in the Remove confirm only (spec: names show nowhere else).
  // `always_confirm` (H-300) and `read_only_key` (MCP-C2) are drawn as
  // switches, not as lines of text.
  return Object.entries(row.detail ?? {})
    .filter(([key]) => key !== "key_names" && key !== "always_confirm" && key !== "read_only_key")
    .map(([key, value]) => ({
    label: key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
    value: String(value),
  }));
}

export interface ConfirmCopy {
  title: string;
  body: string;
  confirmLabel: string;
}

/** What Disconnect does, as it really happens. The caller shows these words. */
export function disconnectCopy(row: Connection): ConfirmCopy {
  if (row.kind === "mcp") {
    return {
      title: "Remove this MCP server?",
      body: "Agents stop using it.",
      confirmLabel: "Remove",
    };
  }
  if (row.kind === "api_key") {
    const names = (row.detail?.key_names ?? "").trim();
    return {
      title: "Remove this key?",
      body: names ? `These settings go: ${names}.` : "Agents lose this key.",
      confirmLabel: "Remove",
    };
  }
  if (row.provider === "zoho") {
    return {
      title: "Disconnect Zoho?",
      body: "The synced records stay, read-only.",
      confirmLabel: "Disconnect",
    };
  }
  if (row.provider === "github") {
    // WS-54 GH-3 (GH-E10): Metorite unlinks and does not uninstall at GitHub.
    return {
      title: "Disconnect GitHub?",
      body:
        "Metorite stops reading pull requests and checks. The links on tasks stay. " +
        "If the Metorite App is installed, it stays installed at GitHub until you uninstall it there.",
      confirmLabel: "Disconnect",
    };
  }
  const name = row.label || row.provider;
  return {
    title: `Disconnect ${name}?`,
    body: `The sign-in is removed. Connect it again in ${APP_LABEL[row.owning_app]}.`,
    confirmLabel: "Disconnect",
  };
}

/**
 * The MCP server name that the test route takes: `detail.name`, else the part
 * of an id `mcp:<name>`. Never the label, which can differ from the name.
 * `null` when the row names no server, and then Test is hidden.
 */
export function mcpName(row: Connection): string | null {
  if (row.detail?.name) return row.detail.name;
  if (row.id.startsWith("mcp:") && row.id.length > 4) return row.id.slice(4);
  return null;
}

// ── The "always confirm" switch of an MCP server (H-300) ──────────────────
//
// A server says which of its tools only read, and a hostile server can lie.
// With the switch on, every tool of the server waits for a member to approve
// it. The gateway sends it as `detail.always_confirm`, "true" or "false". A
// row with no key reads as off, which is what the gateway reads too.

export const ALWAYS_CONFIRM_LABEL = "Always ask before any tool runs";
export const ALWAYS_CONFIRM_HELP =
  "A server says which of its tools only read data. Metorite does not trust that claim. " +
  "With this on, every tool of this server waits for a member to approve it.";

/** The switch of an MCP row. `null` on a row that is not an MCP server. */
export function mcpAlwaysConfirm(row: Connection): boolean | null {
  if (row.kind !== "mcp") return null;
  return row.detail?.always_confirm === "true";
}

// ── The read-only key of an MCP server (MCP-C2, item 32) ─────────────────
//
// The admin says that the key of the server can only read, so the server
// refuses each write at the source. With the switch on, no tool of the
// server waits for a member to approve it. The gateway sends it as
// `detail.read_only_key`, "true" or "false", and a row with no key reads as
// off. The two switches are never both on: a tick of one clears the other,
// and the console sends both keys in one PATCH.

export const READ_ONLY_KEY_LABEL = "This key can only read";
export const READ_ONLY_KEY_HELP =
  "Every tool of this server runs without asking. " +
  "Turn this on only when the key cannot change data at the source.";
/** The extra line under the tick on a row that acts as an admin through OAuth. */
export const READ_ONLY_KEY_OAUTH_NOTE =
  "This switch does not apply to a connection that acts as an admin through OAuth.";

/**
 * True for an MCP row that acts as an admin through OAuth (D98). The gateway
 * names that admin in `acts_as`, and it refuses the read-only key there.
 */
export function mcpActsThroughOAuth(row: Pick<Connection, "kind" | "acts_as">): boolean {
  return row.kind === "mcp" && Boolean((row.acts_as ?? "").trim());
}

/** The read-only key of an MCP row. `null` on a row that is not an MCP server. */
export function mcpReadOnlyKey(row: Connection): boolean | null {
  if (row.kind !== "mcp") return null;
  return row.detail?.read_only_key === "true";
}

/** The two switches of an MCP server, with the names the gateway uses. */
export interface McpSwitches {
  always_confirm: boolean;
  read_only_key: boolean;
}

export type McpSwitch = keyof McpSwitches;

/** The switches of an MCP row. `null` on a row that is not an MCP server. */
export function mcpSwitches(row: Connection): McpSwitches | null {
  if (row.kind !== "mcp") return null;
  return { always_confirm: mcpAlwaysConfirm(row) === true, read_only_key: mcpReadOnlyKey(row) === true };
}

/**
 * One tick. A tick of one switch ON clears the other, because the gateway
 * refuses a server with both on. A tick OFF leaves the other as it is.
 */
export function tickSwitch(current: McpSwitches, which: McpSwitch, next: boolean): McpSwitches {
  const out: McpSwitches = { ...current, [which]: next };
  if (next) out[which === "always_confirm" ? "read_only_key" : "always_confirm"] = false;
  return out;
}

/**
 * What new chats do after a change. A live chat keeps the switches that it
 * read when it started, so the words speak of new chats only.
 */
export function switchesSavedText(s: McpSwitches): string {
  if (s.always_confirm) return "New chats will ask before any tool runs.";
  if (s.read_only_key) return "New chats will run every tool of this server without asking.";
  return "New chats will ask only before tools that can change data.";
}

/**
 * A save of the switches that the list may not show yet (review of MCP-C2).
 *
 * The cached list keeps its old rows while it reads again, so a tick right
 * after a save would build the pair from the OLD row and could turn a
 * switch back. `pair` is what the gateway saved, or null when the save
 * failed and the stored pair is not known. `list` is the list object that
 * was on screen when the save ended.
 */
export interface HeldSwitches {
  id: string;
  pair: McpSwitches | null;
  list: unknown;
}

/**
 * What the panel draws for *row* while *held* is open, given the list that
 * is on screen now.
 *
 * - After a save, the panel draws the saved pair until the row shows it.
 *   A tick builds from that pair. Then the hold is `done`.
 * - After a failed save, the panel draws the row, and the ticks `wait`
 *   until a newer list arrives. Then the hold is `done`.
 * - A hold of another row is `done` at once.
 */
export function heldSwitches(
  row: Connection,
  held: HeldSwitches | null,
  list: unknown,
): { switches: McpSwitches | null; wait: boolean; done: boolean } {
  const shown = mcpSwitches(row);
  if (!held || held.id !== row.id || shown === null) {
    return { switches: shown, wait: false, done: held !== null };
  }
  if (held.pair === null) {
    const newer = list !== held.list;
    return { switches: shown, wait: !newer, done: newer };
  }
  const same =
    shown.always_confirm === held.pair.always_confirm && shown.read_only_key === held.pair.read_only_key;
  return { switches: same ? shown : held.pair, wait: false, done: same };
}

// ── The MCP add form (spec §3.4) ──────────────────────────────────────────

export interface McpForm {
  name: string;
  label: string;
  description: string;
  url: string;
  headerName: string;
  /** Write-only. It goes out in the POST and is never drawn again. */
  headerValue: string;
  agentScope: string;
  /** H-300. ON for a new server. */
  alwaysConfirm: boolean;
  /** MCP-C2. OFF for a new server. Never on with `alwaysConfirm`. */
  readOnlyKey: boolean;
}

export const EMPTY_MCP_FORM: McpForm = {
  name: "",
  label: "",
  description: "",
  url: "",
  headerName: "",
  headerValue: "",
  agentScope: "*",
  alwaysConfirm: true,
  readOnlyKey: false,
};

/** One tick in the Add form. It follows `tickSwitch`, so a tick of one clears the other. */
export function tickFormSwitch(f: McpForm, which: McpSwitch, next: boolean): McpForm {
  const s = tickSwitch({ always_confirm: f.alwaysConfirm, read_only_key: f.readOnlyKey }, which, next);
  return { ...f, alwaysConfirm: s.always_confirm, readOnlyKey: s.read_only_key };
}

export function mcpFormReady(f: McpForm): boolean {
  return Boolean(f.name.trim() && f.url.trim());
}

/** The body of `POST /api/integrations/mcp`. */
export function mcpBody(f: McpForm): Record<string, unknown> {
  const headers: Record<string, string> = {};
  if (f.headerName.trim() && f.headerValue.trim()) {
    headers[f.headerName.trim()] = f.headerValue.trim();
  }
  const scope = f.agentScope.split(",").map((s) => s.trim()).filter(Boolean);
  return {
    name: f.name.trim(),
    label: f.label.trim() || f.name.trim(),
    description: f.description.trim(),
    transport: "http-sse",
    url: f.url.trim(),
    headers,
    agent_scope: scope.length ? scope : ["*"],
    enabled: true,
    always_confirm: f.alwaysConfirm,
    // MCP-C2: always sent. A register is a full register, so a body with no
    // key turns it off.
    read_only_key: f.readOnlyKey,
  };
}

/** The server's own words for a refusal, or a status line. */
export function refusalText(body: unknown, status: number): string {
  const b = (body ?? {}) as { detail?: unknown; error?: unknown };
  const d = b.detail;
  if (typeof d === "string" && d) return d;
  if (Array.isArray(d) && d.length) {
    const first = d[0] as { msg?: unknown };
    if (typeof first?.msg === "string") return first.msg;
  }
  if (d && typeof d === "object") {
    const o = d as { message?: unknown; code?: unknown };
    if (typeof o.message === "string" && o.message) return o.message;
    if (typeof o.code === "string" && o.code) return o.code.replace(/_/g, " ");
  }
  if (typeof b.error === "string" && b.error) return b.error;
  return `The request failed (${status}).`;
}

/** A refused disconnect, as a toast line and an optional link to the app. */
export function disconnectFailure(
  err: { code?: string; setupHref?: string; message?: string } | null,
  row: Pick<Connection, "owner" | "owning_app" | "setup_href">,
): { title: string; href: string | null } {
  if (err?.code === "member_connection") {
    return {
      title: `${row.owner || "The owner"} manages this connection in ${APP_LABEL[row.owning_app]}.`,
      href: err.setupHref || row.setup_href,
    };
  }
  // `key_locked` and every other refusal: the server's own message.
  return { title: err?.message || "The request failed.", href: null };
}

// ── Labels, test words and the key cards ──────────────────────────────────

/** The row's name. The old Zoho key row says so, so it is not a second Zoho. */
export function rowLabel(row: Pick<Connection, "id" | "kind" | "provider" | "label">): string {
  if (row.kind === "api_key" && (row.provider === "zoho-crm" || row.id === "key:zoho-crm")) {
    return "Zoho CRM (old settings)";
  }
  return row.label;
}

const TEST_TEXT: Record<string, string> = {
  url_refused: "Metorite cannot reach this address. Use a public https URL.",
  timeout: "The server did not answer in time.",
  connect_failed: "Metorite cannot connect to the server.",
  unauthorized: "The server refused the stored header.",
  server_error: "The server returned an error.",
  no_url: "This server has no URL.",
  stdio_unsupported: "Metorite cannot test a stdio server.",
  // WS-54 IN-8a: the stored OAuth token of the server has ended.
  token_expired: "The sign-in to this server has expired. Connect it again.",
};

/** The toast line of an MCP test. A code never reaches the screen. */
export function testMessage(r: { ok: boolean; error: string | null }): string {
  if (r.ok) return "The server answered.";
  return TEST_TEXT[r.error ?? ""] ?? "The test failed.";
}

/**
 * The tiles whose connection an app owns (D96.1), and the registry row that
 * stands for each one. Such a tile counts that row, of any kind, and never a
 * key row. Its panel links to the app and offers no key form. The gateway
 * mirror is `APP_OWNED_SERVICES` in `routes/integrations.py`.
 *
 * WhatsApp stays out on purpose. Its tile counts the numbers that
 * `/api/whatsapp/accounts` returns, and that read works for a member. The
 * registry read needs `admin:members:read`, so a member would see "Not set up".
 */
export const APP_OWNED_TILES: Record<
  string,
  { provider: string; owningApp: OwningApp; settingsHref: string }
> = {
  "zoho-crm": { provider: "zoho", owningApp: "crm", settingsHref: "/crm?tab=settings&section=setup" },
};

/** The entry of an app-owned tile, or undefined. An own key only, never "constructor". */
export function appOwnedTile(service: string): (typeof APP_OWNED_TILES)[string] | undefined {
  return Object.prototype.hasOwnProperty.call(APP_OWNED_TILES, service)
    ? APP_OWNED_TILES[service]
    : undefined;
}

const LIVE: ReadonlySet<ConnectionStatus> = new Set(["connected", "configured"]);

/**
 * The rows that a tile may read. An app-owned tile reads the `registry`
 * source, so when the read model names that source in `unavailable`, the
 * tile gets `null` and the server's status stands. A key tile keeps the rows.
 */
export function rowsForTile(
  rows: readonly Connection[] | null,
  unavailable: readonly string[],
  service: string,
): readonly Connection[] | null {
  if (rows !== null && appOwnedTile(service) && unavailable.includes("registry")) return null;
  return rows;
}

/** The app's registry row for an app-owned tile, or null. */
export function appConnection(
  rows: readonly Connection[] | null,
  service: string,
): Connection | null {
  const owned = appOwnedTile(service);
  if (!owned || rows === null) return null;
  return rows.find((r) => r.provider === owned.provider && r.owning_app === owned.owningApp) ?? null;
}

/**
 * Whether the org holds a live connection for `service`: the app's row for an
 * app-owned tile, else a key row. `rows` is `null` when the read model could
 * not be read, and then the server's status stands.
 */
export function orgKeyConfigured(
  rows: readonly Connection[] | null,
  service: string,
  fallback: boolean,
): boolean {
  if (rows === null) return fallback;
  const owned = appOwnedTile(service);
  if (owned) {
    return rows.some(
      (r) => r.provider === owned.provider && r.owning_app === owned.owningApp && LIVE.has(r.status),
    );
  }
  return rows.some((r) => r.kind === "api_key" && r.provider === service && LIVE.has(r.status));
}
