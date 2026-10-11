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

export function useCrmMode(isAdmin: boolean, accessLoading: boolean): CrmModeState {
  const conn = useCachedResource<ConnectionInfo | null>(KEY_CONNECTION, api.getConnection);
  // With the mirror off the connection read is a 404 and holds no mode. An
  // admin can still read the mode from the settings.
  const wantsSettings = !conn.loading && conn.data === null && isAdmin && !accessLoading;
  const settings = useCachedResource<CrmSettings | null>(
    wantsSettings ? KEY_SETTINGS : null,
    () => api.getCrmSettings().catch(() => null),
  );

  const connection = conn.data ?? null;
  const readFailed = conn.data === undefined && conn.error !== null;
  const settingsMode: CrmMode | undefined =
    settings.data === undefined || settings.data === null ? undefined : settings.data.mode;

  const loading =
    accessLoading || (conn.loading && !readFailed) || (wantsSettings && settings.loading);
  // A failed read must not lock the CRM. Show the pipeline, as before.
  const phase: FirstOpen = readFailed
    ? "ready"
    : firstOpen({ loading, connection, settingsMode, isAdmin });

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
