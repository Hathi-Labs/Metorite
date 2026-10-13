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
  return {
    openLabel: row.setup_href && row.owning_app !== "integrations" ? `Open in ${app}` : null,
    href: row.setup_href,
    reconnect: Boolean(row.setup_href) && row.status === "needs_reconnect",
    reconnectHref: reconnectHref(row),
    disconnect: canManage && row.can_disconnect,
    test: canManage && row.kind === "mcp" && mcpName(row) !== null,
    alwaysConfirm: row.kind === "mcp" && mcpName(row) !== null ? (canManage ? "edit" : "view") : null,
    memberNote:
      row.scope === "member" ? `${row.owner || "The owner"} manages this connection in ${app}.` : null,
  };
}

/** The `detail` fields as label and value pairs. Keys read as words. */
export function detailRows(row: Connection): { label: string; value: string }[] {
  // `key_names` shows in the Remove confirm only (spec: names show nowhere else).
  // `always_confirm` is drawn as a switch (H-300), not as a line of text.
  return Object.entries(row.detail ?? {})
    .filter(([key]) => key !== "key_names" && key !== "always_confirm")
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
};

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
 * Whether the org holds a key for `service`. `rows` is `null` when the read
 * model answered 404, and then the deployment-wide status stands.
 */
export function orgKeyConfigured(
  rows: readonly Connection[] | null,
  service: string,
  fallback: boolean,
): boolean {
  if (rows === null) return fallback;
  return rows.some(
    (r) =>
      r.kind === "api_key" &&
      r.provider === service &&
      (r.status === "configured" || r.status === "connected"),
  );
}
