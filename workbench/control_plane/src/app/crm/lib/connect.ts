// ── The CRM's mode, connection and mirror marks (WS-53 CRM-U5, CRM-Z7) ─────
//
// Pure. The decisions of the first open, the title bar line, the return from
// Zoho, the import progress and the mirror marks all live here, because the
// vitest setup has no DOM. The components draw what this file decides.
//
// Spec: project-docs/specs/crm_platform.md §3.1, §3.3, §8.1 and §8.3.
//
// ⚠️ "Mirror off" is a 404 from `GET /crm/connection`. That is not an error.
// It means the Zoho mirror is dark for this deployment, so the mirror UI
// stays hidden and the CRM behaves as it did before.

import { relativeTime } from "@/lib/taskCard";

export type CrmMode = "native" | "mirror" | null;

/** `GET /crm/connection`. No token and no secret are in it. */
export type ConnectionInfo = {
  connected: boolean;
  provider: string;
  owning_app: string;
  status: "connected" | "needs_reconnect" | "disconnected" | null;
  location: string | null;
  scopes: string[];
  connected_by: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  mode: CrmMode;
};

/** `GET /crm/settings` (admin). */
export type CrmSettings = {
  mode: CrmMode;
  access?: string;
  access_groups?: string[];
};

export const SYNC_ENTITIES = [
  "organizations",
  "contacts",
  "leads",
  "deals",
  "activities",
] as const;
export type SyncEntity = (typeof SYNC_ENTITIES)[number];
export type SyncCounts = Partial<Record<SyncEntity, number>>;

/** `GET /crm/sync/status`. */
export type SyncStatus = {
  state: "never" | "running" | "idle";
  kind: "import" | "incremental" | "reconcile" | null;
  started_at: string | null;
  finished_at: string | null;
  last_success_at: string | null;
  counts: SyncCounts;
  estimate: SyncCounts | null;
  error: string | null;
  next_run_at: string | null;
};

/** The capability that makes a member an admin of the CRM. */
export const ADMIN_CAPABILITY = "admin:access:manage";

// ── The first open (U5) ───────────────────────────────────────────────────

export type FirstOpen =
  /** A read is still running. Draw a skeleton. */
  | "loading"
  /** A mode exists, or this member cannot find out. Draw the pipeline. */
  | "ready"
  /** An admin, mirror on, no mode. Two cards. */
  | "choose"
  /** An admin, mirror off, no mode. The Native card only. */
  | "native-only"
  /** A member, mirror on, no mode. Tell them to ask an admin. */
  | "wait-admin"
  /** The gateway refused the first read (403): the access rule has not admitted this member. */
  | "no-access"
  /** The 403 was the missing `feature:crm` grant. */
  | "no-feature"
  /** Each CRM route answers 404: the CRM is off for this organization. */
  | "off";

/**
 * What the CRM shows before the pipeline.
 *
 * `connection` is `null` when the mirror is off (a 404). In that case only an
 * admin can read the mode (`GET /crm/settings`), so `settingsMode` carries it:
 * `undefined` while it loads or when it cannot be read.
 *
 * A member with no way to read the mode gets the pipeline, as before. The
 * alternative blocks a CRM that already has data behind a gate the member
 * cannot open.
 */
export function firstOpen(input: {
  loading: boolean;
  connection: ConnectionInfo | null;
  settingsMode: CrmMode | undefined;
  isAdmin: boolean;
  /** The first read answered 403: the access rule, or the missing feature (CRM-L2). */
  denied?: "access" | "feature" | null;
  /** The CRM answers 404 on every route (CRM-L2). */
  off?: boolean;
}): FirstOpen {
  if (input.loading) return "loading";
  if (input.off) return "off";
  if (input.denied === "access") return "no-access";
  if (input.denied === "feature") return "no-feature";
  const mirrorOn = input.connection !== null;
  const mode = mirrorOn ? input.connection!.mode : input.settingsMode;
  if (mode === "native" || mode === "mirror") return "ready";
  if (!mirrorOn && mode === undefined) return "ready";
  // The mode is not chosen.
  if (input.isAdmin) return mirrorOn ? "choose" : "native-only";
  return mirrorOn ? "wait-admin" : "ready";
}

/** The mode the page works in, whichever read answered. */
export function effectiveMode(
  connection: ConnectionInfo | null,
  settingsMode: CrmMode | undefined,
): CrmMode {
  if (connection) return connection.mode;
  return settingsMode ?? null;
}

/** May a member create a record by hand? Not in Mirror mode (§3.3). */
export function canCreate(mode: CrmMode): boolean {
  return mode !== "mirror";
}

// ── The title bar line (§8.1) ─────────────────────────────────────────────

/**
 * The mode, as the title bar says it. `null` when there is nothing to say.
 *
 * "Synced from Zoho · 4m ago" reads the last good sync. While a sync runs it
 * says "Importing from Zoho…".
 */
export function modeLine(input: {
  mode: CrmMode;
  connection: ConnectionInfo | null;
  sync: SyncStatus | null;
  nowMs?: number;
}): string | null {
  if (input.mode === "native") return "Native";
  if (input.mode !== "mirror") return null;
  const last = input.sync?.last_success_at ?? input.connection?.last_sync_at ?? null;
  const when = last ? relativeTime(last, input.nowMs) : "";
  const status = input.connection?.status;
  if (status === "needs_reconnect" || status === "disconnected") {
    return when ? `Zoho disconnected · last synced ${when}` : "Zoho disconnected";
  }
  if (input.sync?.state === "running") return "Importing from Zoho…";
  return when ? `Synced from Zoho · ${when}` : "Synced from Zoho";
}

/** The connection needs a new consent. */
export function needsReconnect(connection: ConnectionInfo | null): boolean {
  return connection?.status === "needs_reconnect";
}

// ── The return from Zoho (§8.3) ───────────────────────────────────────────

export const CONNECT_ERROR_MESSAGES: Record<string, string> = {
  access_denied: "Zoho did not allow the connection. Connect again and choose Accept.",
  invalid_state: "The connection link has expired. Connect again.",
  untrusted_host: "Metorite does not trust that Zoho address. Ask support.",
  mode_is_native: "This CRM already uses Metorite as its CRM. It cannot sync from Zoho.",
  zoho_not_configured: "Zoho CRM is not set up for this workspace yet. Ask support.",
};

export const CONNECT_ERROR_FALLBACK = "Zoho CRM did not connect. Try again.";

export function connectErrorMessage(code: string | null | undefined): string {
  return (code && CONNECT_ERROR_MESSAGES[code]) || CONNECT_ERROR_FALLBACK;
}

export type ReturnNotice =
  | { kind: "connected" }
  | { kind: "error"; code: string; message: string };

/** What the query of `/crm` says about a trip to Zoho. `null` is no trip. */
export function returnNotice(search: string): ReturnNotice | null {
  const params = new URLSearchParams(search);
  const error = params.get("connect_error");
  if (error !== null) {
    return { kind: "error", code: error, message: connectErrorMessage(error) };
  }
  if (params.get("connected") === "zoho") return { kind: "connected" };
  return null;
}

/** The query with the two return parameters removed. Others stay. */
export function clearReturnParams(search: string): string {
  const params = new URLSearchParams(search);
  params.delete("connected");
  params.delete("connect_error");
  return params.toString();
}

/** The words for a refused Connect click. */
export function startFailureMessage(status: number, code: string | undefined): string {
  if (status === 409 || code === "mode_is_native") return CONNECT_ERROR_MESSAGES.mode_is_native;
  if (status === 503 || code === "zoho_not_configured") {
    return CONNECT_ERROR_MESSAGES.zoho_not_configured;
  }
  if (status === 403) return "Only an admin can connect Zoho CRM.";
  return CONNECT_ERROR_FALLBACK;
}

/** The words for a refused "Sync now". */
export function syncFailureMessage(status: number, code: string | undefined): string {
  if (code === "sync_running") return "A sync is already running.";
  if (code === "not_connected") return "Zoho CRM is not connected. Connect it first.";
  if (status === 403) return "Only an admin can start a sync.";
  return "The sync did not start. Try again.";
}

// ── The import progress (§8.3) ────────────────────────────────────────────

export const SYNC_LABELS: Record<SyncEntity, string> = {
  organizations: "Companies",
  contacts: "Contacts",
  leads: "Leads",
  deals: "Deals",
  activities: "Activities",
};

export type ProgressLine = { entity: SyncEntity; label: string; done: number; total: number | null };

export function progressLines(status: SyncStatus | null): ProgressLine[] {
  if (!status) return [];
  return SYNC_ENTITIES.flatMap((entity) => {
    const done = status.counts?.[entity];
    const total = status.estimate?.[entity];
    if (done === undefined && total === undefined) return [];
    return [
      {
        entity,
        label: SYNC_LABELS[entity],
        done: done ?? 0,
        total: typeof total === "number" ? total : null,
      },
    ];
  });
}

/** The whole import as a percent, or `null` when no estimate exists. */
export function progressPercent(status: SyncStatus | null): number | null {
  const lines = progressLines(status);
  if (lines.length === 0 || lines.some((l) => l.total === null)) return null;
  const total = lines.reduce((sum, l) => sum + (l.total ?? 0), 0);
  if (total <= 0) return null;
  const done = lines.reduce((sum, l) => sum + Math.min(l.done, l.total ?? l.done), 0);
  return (done / total) * 100;
}

/** "Contacts 120 of 400 · Deals 30 of 90". */
export function progressDetail(status: SyncStatus | null): string {
  return progressLines(status)
    .map((l) =>
      l.total === null
        ? `${l.label} ${l.done.toLocaleString("en-US")}`
        : `${l.label} ${l.done.toLocaleString("en-US")} of ${l.total.toLocaleString("en-US")}`,
    )
    .join(" · ");
}

/** The longest a watch after a return waits for a sync to appear. */
export const WATCH_LIMIT_POLLS = 20;
export const POLL_MS = 3000;

/**
 * Poll while a sync runs. After the return from Zoho the sync may not have
 * started yet, so `watching` keeps the poll alive. The hook stops a watch
 * after `WATCH_LIMIT_POLLS` polls when no sync has started.
 */
export function shouldPoll(status: SyncStatus | null, watching: boolean): boolean {
  return status?.state === "running" || watching;
}

// ── The mirror marks (§3.3) ───────────────────────────────────────────────

type Row = Record<string, unknown>;

export const LOCK_TIP = "Synced from Zoho. Change it in Zoho.";
export const ONLY_IN_METORITE = "Only in Metorite";

/** The source a mirrored record came from, or `null` for a native record. */
export function extSource(record: Row | null | undefined): string | null {
  const value = record?.ext_source;
  return typeof value === "string" && value ? value : null;
}

export function isMirrored(record: Row | null | undefined): boolean {
  return extSource(record) !== null;
}

/** An activity that exists only in Metorite, under a record that Zoho owns. */
export function onlyInMetorite(
  activity: { ext_source?: string | null } | null | undefined,
  parentMirrored: boolean,
): boolean {
  return parentMirrored && !!activity && !activity.ext_source;
}

/** Zoho keys are `Lead_Source`. Labels are "Lead Source". */
export function humanizeKey(key: string): string {
  const spaced = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : key;
}

function customValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(customValue).join(", ");
  if (typeof value === "object") {
    const named = (value as Row).name ?? (value as Row).display_value;
    return typeof named === "string" ? named : JSON.stringify(value);
  }
  return String(value);
}

/** The `custom` object of a record, as label and value rows. */
export function customRows(record: Row | null | undefined): { key: string; label: string; value: string }[] {
  const custom = record?.custom;
  if (!custom || typeof custom !== "object" || Array.isArray(custom)) return [];
  return Object.entries(custom as Row).map(([key, value]) => ({
    key,
    label: humanizeKey(key),
    value: customValue(value),
  }));
}

/** The refusal codes of the write guard (CRM-Z6). */
export const MIRROR_REFUSALS = new Set(["mirrored_record", "mirror_mode_create"]);
