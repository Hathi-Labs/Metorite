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
import { isRunning, type SyncHealth } from "./syncHealth";
import type { FieldDef } from "./fields";
import type { FieldLayout } from "./fieldLayout";
import type { CrmOwner } from "./filterBar";
import type { RecordCounts } from "./setup";
import type { DealPipeline, EntitySlug } from "./types";
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
import type { CrmModule } from "./modules";
import type { SourceLabels } from "./views";

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
  /**
   * The connection read failed, so the page opened on a guess and `mode` may
   * be wrong (CRM-C1 review). A rewrite of the URL waits for a real answer.
   */
  readFailed: boolean;
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
    readFailed,
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

const KEY_SYNC_HEALTH = "crm:sync-health";

export type SyncHealthState = {
  health: SyncHealth | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

/**
 * The sync health (CRM-Q9). It polls every 3 s while the last run is
 * `running`, with the poller of the status banner. A hidden tab is not polled.
 */
export function useSyncHealth(enabled: boolean): SyncHealthState {
  const res = useCachedResource<SyncHealth | null>(
    enabled ? KEY_SYNC_HEALTH : null,
    api.getSyncHealth,
  );
  const health = res.data ?? null;
  const refresh = res.refresh;
  const running = isRunning(health);

  const runningRef = useRef(false);
  useEffect(() => {
    runningRef.current = running;
  });

  useEffect(() => {
    if (!enabled || !running) return;
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
  }, [enabled, running, refresh]);

  return { health, loading: enabled && res.loading, error: res.error, refresh };
}

/**
 * The source's names of the core entities (CRM-Q17a-2 item 7), from
 * `GET /crm/mode`. Only Mirror mode reads it. A failure, a Native CRM and an
 * org off `CRM_FILL_RANK_ORGS` each give `null`, and the CRM keeps its own
 * names. A label that fails to load must not stop the CRM.
 */
export function useSourceLabels(enabled: boolean): SourceLabels | null {
  const res = useCachedResource<SourceLabels | null>(
    enabled ? "crm:mode" : null,
    () =>
      api
        .getCrmMode()
        .then((body) => (body.labels && typeof body.labels === "object" ? body.labels : null))
        .catch(() => null),
  );
  return enabled ? (res.data ?? null) : null;
}

export type ModulesState = { modules: CrmModule[] | null; loading: boolean };

/**
 * The mirrored generic modules (WS-53 CRM-Q6b), for the rail. Every failure
 * reads as "no modules": the rail then shows the known views only. A rail
 * that fails to draw because one read failed is worse.
 */
export function useModules(enabled: boolean): ModulesState {
  const res = useCachedResource<CrmModule[] | null>(
    enabled ? "crm:modules" : null,
    () => api.listModules().catch(() => null),
  );
  return { modules: res.data ?? null, loading: enabled && res.loading };
}

const KEY_PIPELINES = "crm:pipelines";

export type PipelinesState = {
  /** `null` while unread, and when the gateway has no pipelines route (404). */
  pipelines: DealPipeline[] | null;
  /** True until the first answer. A board waits for it, so it loads once. */
  loading: boolean;
};

/**
 * The deal pipelines (CRM-Q3). Every failure reads as "no pipelines": the CRM
 * then has one implicit pipeline, no picker, and no `pipeline_id` on a request.
 * A board that fails to open because the picker's list failed is worse.
 */
export function usePipelines(enabled: boolean): PipelinesState {
  const res = useCachedResource<DealPipeline[] | null>(
    enabled ? KEY_PIPELINES : null,
    () => api.listPipelines().catch(() => null),
  );
  return { pipelines: res.data ?? null, loading: enabled && res.loading };
}

/**
 * The owners the owner filter offers (CRM-X1). A failure reads as "no list":
 * the filter then offers Anyone and Me, and the list still loads.
 */
export function useOwners(enabled: boolean): CrmOwner[] | null {
  const res = useCachedResource<CrmOwner[] | null>(
    enabled ? "crm:owners" : null,
    () => api.listOwners().catch(() => null),
  );
  return res.data ?? null;
}

export type FieldDefsState = { defs: FieldDef[] | null; loading: boolean };

/**
 * The field definitions of one entity (CRM-Q4). Every failure reads as "no
 * definitions", and the record then shows the humanised keys, as before. A
 * record that fails to open because its labels failed is worse.
 */
export function useFieldDefs(entity: EntitySlug | null): FieldDefsState {
  const res = useCachedResource<FieldDef[] | null>(
    entity ? `crm:fields:${entity}` : null,
    () => api.listFields(entity!).catch(() => null),
  );
  return { defs: res.data ?? null, loading: entity !== null && res.loading };
}

export type FieldLayoutState = {
  /**
   * `null` while unread, and when no read has given a layout. The caller then
   * draws the view of today. A failed re-read keeps the last good layout.
   */
  layout: FieldLayout | null;
  /** The message of the last failed read, or null. */
  error: string | null;
  loading: boolean;
  refresh: () => void;
};

/** The cache key of the layout of one entity. */
export function fieldLayoutKey(entity: EntitySlug): string {
  return `crm:field-layout:${entity}`;
}

/**
 * The layout read of one entity (CRM-C4p item 3).
 *
 * ⚠️ It THROWS on a failure, and it must not turn one into `null` (review
 * P2). `useCachedResource` keeps `prev.data` over a failed read, so a re-read
 * that fails in the middle of an edit keeps the good layout. A `null` from
 * here would be a good answer, and it would replace the layout.
 */
export function readFieldLayout(entity: EntitySlug): Promise<FieldLayout> {
  return api.getFieldLayout(entity);
}

/**
 * The one layout of an entity (CRM-C4a item 12), with `studio` and
 * `studio_admin`. C4b and C4c read it. `invalidateCrmMode` wakes it, as it
 * wakes every other CRM read.
 */
export function useFieldLayout(entity: EntitySlug | null): FieldLayoutState {
  const res = useCachedResource<FieldLayout>(
    entity ? fieldLayoutKey(entity) : null,
    () => readFieldLayout(entity!),
  );
  return {
    layout: res.data ?? null,
    error: res.error,
    loading: entity !== null && res.loading,
    refresh: res.refresh,
  };
}

const KEY_RECORD_COUNTS = "crm:record-counts";

export type RecordCountsState = {
  counts: RecordCounts | null;
  loading: boolean;
  refresh: () => void;
};

/**
 * The record counts of the setup hub (CRM-X0). Admin only. `invalidateCrmMode`
 * wakes it with every other CRM read, so a start over shows 0 at once.
 */
export function useRecordCounts(enabled: boolean): RecordCountsState {
  const res = useCachedResource<RecordCounts>(
    enabled ? KEY_RECORD_COUNTS : null,
    api.getRecordCounts,
  );
  return { counts: res.data ?? null, loading: enabled && res.loading, refresh: res.refresh };
}
