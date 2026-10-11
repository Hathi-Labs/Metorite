"use client";

// ── The React face of the CRM's mode, connection and sync (CRM-U5/Z7) ──────
//
// Every read goes through `useCachedResource`, so a revisit paints at once
// and `invalidate("crm:")` after a write wakes each watcher. The decisions
// live in `connect.ts`, where they have tests.

import { useEffect, useRef } from "react";

import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import * as api from "./api";
import { denialOf, isNotFound, type Denial } from "./access";
import { startPoller } from "./poller";
import {
  POLL_MS,
  WATCH_LIMIT_POLLS,
  effectiveMode,
  firstOpen,
  shouldPoll,
  type ConnectionInfo,
  type CrmMode,
  type CrmSettings,
  type FirstOpen,
  type SyncStatus,
} from "./connect";

const KEY_CONNECTION = "crm:connection";
const KEY_SETTINGS = "crm:settings";
const KEY_SYNC = "crm:sync-status";

/** Re-read the mode, the connection and the sync after a write. */
export function invalidateCrmMode(): void {
  invalidate("crm:");
}

export type CrmModeState = {
  phase: FirstOpen;
  mode: CrmMode;
  /** `null` when the mirror is off. */
  connection: ConnectionInfo | null;
  refresh: () => void;
};

/** What the settings read holds when every CRM route answers 404. */
export const CRM_OFF = "off" as const;

/** The settings read. A 404 means the CRM is off. Any other refusal reads as unknown. */
export function readSettings(): Promise<CrmSettings | null | typeof CRM_OFF> {
  return api.getCrmSettings().catch((err: unknown) => (isNotFound(err) ? CRM_OFF : null));
}

const DENIED_ACCESS = "denied-access" as const;
const DENIED_FEATURE = "denied-feature" as const;

export function useCrmMode(isAdmin: boolean, accessLoading: boolean): CrmModeState {
  // The first read of the page. A 403 here is the access rule (CRM-T5): the
  // member is not admitted yet, so it is a state to draw, not an error.
  const conn = useCachedResource<
    ConnectionInfo | null | typeof DENIED_ACCESS | typeof DENIED_FEATURE
  >(KEY_CONNECTION, () =>
    api.getConnection().catch((err: unknown) => {
      const why = denialOf(err);
      if (why === "access") return DENIED_ACCESS;
      if (why === "feature") return DENIED_FEATURE;
      throw err;
    }),
  );
  const denied: Denial | null =
    conn.data === DENIED_ACCESS ? "access" : conn.data === DENIED_FEATURE ? "feature" : null;
  // With the mirror off the connection read is a 404 and holds no mode. An
  // admin can still read the mode from the settings.
  // The 404 of the connection is ambiguous: the mirror is off, or the whole
  // CRM is. The settings read tells them apart, for a member too: a member
  // gets 403 there when the CRM is on (the admin floor), and 404 when it is off.
  const wantsSettings = !conn.loading && conn.data === null && !accessLoading;
  const settings = useCachedResource<CrmSettings | null | typeof CRM_OFF>(
    wantsSettings ? KEY_SETTINGS : null,
    readSettings,
  );
  const off = settings.data === CRM_OFF;

  const connection =
    conn.data === DENIED_ACCESS || conn.data === DENIED_FEATURE ? null : (conn.data ?? null);
  const readFailed = conn.data === undefined && conn.error !== null;
  const settingsMode: CrmMode | undefined =
    settings.data === undefined || settings.data === null || settings.data === CRM_OFF
      ? undefined
      : settings.data.mode;

  const loading =
    accessLoading || (conn.loading && !readFailed) || (wantsSettings && settings.loading);
  // A failed read must not lock the CRM. Show the pipeline, as before.
  const phase: FirstOpen = readFailed
    ? "ready"
    : firstOpen({ loading, connection, settingsMode, isAdmin, denied, off });

  return {
    phase,
    mode: effectiveMode(connection, settingsMode),
    connection,
    refresh: conn.refresh,
  };
}

/**
 * The sync status, polled every 3 s while a sync runs. `watchToken` counts
 * the starts of a watch (the return from Zoho, "Sync now"). Each new value
 * re-arms the poll, also after an earlier watch ended. A hidden tab is not
 * polled. `enabled` is false when the mirror is off.
 */
export function useSyncStatus(enabled: boolean, watchToken: number): SyncStatus | null {
  const res = useCachedResource<SyncStatus | null>(enabled ? KEY_SYNC : null, api.getSyncStatus);
  const status = res.data ?? null;
  const refresh = res.refresh;
  const running = status?.state === "running";
  const live = enabled && shouldPoll(status, watchToken > 0);

  // The tick reads the latest state through a ref, set in an effect.
  const runningRef = useRef(false);
  useEffect(() => {
    runningRef.current = running;
  });

  useEffect(() => {
    if (!live) return;
    const poller = startPoller({
      refresh,
      isRunning: () => runningRef.current,
      isHidden: () => document.hidden,
      ms: POLL_MS,
      limit: WATCH_LIMIT_POLLS,
    });
    document.addEventListener("visibilitychange", poller.onVisible);
    return () => {
      poller.stop();
      document.removeEventListener("visibilitychange", poller.onVisible);
    };
  }, [live, watchToken, refresh]);

  return status;
}
