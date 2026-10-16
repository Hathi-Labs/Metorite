"use client";

/**
 * The Sync health card of the CRM settings page (WS-53 CRM-Q9, spec section 18.3).
 *
 * It shows what the mirror holds and what it leaves out: the Zoho count against
 * ours, the records the last run skipped, the modules that Zoho has and the
 * mirror does not read, the credits, and the last runs. It is for an admin and
 * for Mirror mode only. In Native mode the page does not draw it.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ProgressBar from "@/components/ui/ProgressBar";
import { useToast } from "@/components/ui/Toast";
import { useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { syncFailureMessage } from "../lib/connect";
import {
  activityLines,
  agoLabel,
  creditsPercent,
  difference,
  differenceLabel,
  entityLabel,
  heldKeys,
  holdFailureMessage,
  holdSentence,
  isHeld,
  isHoldable,
  isRunning,
  isUnavailable,
  largeSentence,
  largeUnheld,
  moduleState,
  recordCount,
  recordsOf,
  runLine,
  shortReadSentence,
  shortReads,
  statusLines,
  tieStallSentence,
  tieStalls,
  unavailableSentence,
  unmappedCount,
  type ActivityLine,
  type SyncHealth,
} from "../lib/syncHealth";
import { invalidateCrmMode, useSyncHealth } from "../lib/useCrmMode";
import { Card } from "./SettingsCards";

const NOTE = "The Zoho count against ours, skipped records and the last error.";

/** CRM-Q24: the badge of each kind of activity line. */
const ACTIVITY_BADGES: Record<ActivityLine["kind"], { tone: "warning" | "neutral" | "success"; label: string }> = {
  parentless: { tone: "neutral", label: "No record" },
  dropped: { tone: "warning", label: "Not synced" },
  waiting: { tone: "neutral", label: "Waiting" },
  recovered: { tone: "success", label: "Back" },
};

const th = "px-2 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-muted-foreground";
const td = "px-2 py-1.5 text-xs text-foreground tabular-nums";

/** The card, drawn from a health answer that the caller already holds. */
export function SyncHealthCardView({
  health,
  nowMs = Date.now(),
  syncing = false,
  onSyncNow,
  busyKey = null,
  onSetSync = () => undefined,
  onSyncOnly = () => undefined,
}: {
  health: SyncHealth;
  nowMs?: number;
  syncing?: boolean;
  onSyncNow: () => void;
  /** CRM-Q16: the key of a hold write or a one-module run that is in flight. */
  busyKey?: string | null;
  /** CRM-Q16: `false` holds the key, `true` releases it. */
  onSetSync?: (key: string, sync: boolean) => void;
  /** CRM-Q16: sync this one key on its own. */
  onSyncOnly?: (key: string) => void;
}) {
  const lines = statusLines(health, nowMs);
  const running = isRunning(health);
  const unavailable = health.entities.filter(isUnavailable);
  const label = (key: string) => entityLabel(key, health.modules);
  const held = heldKeys(health);
  const large = health.entities.filter((entity) => largeUnheld(health, entity));
  const short = shortReads(health);
  const stalled = tieStalls(health);
  const activities = activityLines(health);
  return (
    <Card title="Sync health" note={NOTE}>
      <div className="divide-y divide-border/60">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
          <span className="text-sm font-medium text-foreground">{lines.synced}</span>
          {lines.next && <span className="text-xs text-muted-foreground">{lines.next}</span>}
          <Button
            variant="secondary"
            size="sm"
            className="ml-auto"
            loading={syncing || running}
            disabled={running}
            onClick={onSyncNow}
          >
            Sync now
          </Button>
          {lines.error && (
            <p role="alert" className="basis-full text-xs text-destructive">
              {lines.error}
            </p>
          )}
        </div>

        {/* The table scrolls sideways on a phone. The lists below it do not, so
            they stay in a sibling block (visual review, CRM-Q16). */}
        <div className="px-2 pt-2" aria-label="Counts by entity">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[32rem]">
              <thead>
                <tr>
                  <th className={th}>Entity</th>
                  {/* The one control sits beside the name, so a phone shows it. */}
                  <th className={th}>Sync</th>
                  <th className={th}>Zoho</th>
                  <th className={th}>Ours</th>
                  <th className={th}>Difference</th>
                  <th className={th}>Skipped</th>
                  <th className={th}>Unlinked</th>
                  <th className={th}>Failed</th>
                  {/* CRM-Q19: values that a Field map rule could not put into its column. */}
                  <th className={th}>Not mapped</th>
                </tr>
              </thead>
              <tbody>
                {health.entities.map((entity) => {
                  const heldRow = isHeld(health, entity.key);
                  // A held row is behind on purpose, so its gap is not a warning.
                  const diff = heldRow ? null : difference(entity);
                  return (
                    <tr key={entity.key} aria-label={label(entity.key)}>
                      <td className="px-2 py-1.5 text-xs font-medium text-foreground">
                        {label(entity.key)}
                      </td>
                      <td className={td}>
                        {isHoldable(entity.key) ? (
                          <Checkbox
                            size="sm"
                            className="align-middle"
                            checked={!heldRow}
                            disabled={busyKey === entity.key}
                            aria-label={`Sync ${label(entity.key)}`}
                            onChange={(e) => onSetSync(entity.key, e.currentTarget.checked)}
                          />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className={td}>
                        {entity.zoho_count === null ? "—" : recordCount(entity.zoho_count)}
                      </td>
                      <td className={td}>{entity.ours === null ? "—" : recordCount(entity.ours)}</td>
                      <td className={td}>
                        {diff !== null && <Badge tone="warning">{differenceLabel(diff)}</Badge>}
                      </td>
                      <td className={td}>{entity.last_run.skipped ?? 0}</td>
                      <td className={td}>{entity.last_run.unlinked ?? 0}</td>
                      <td className={td}>{entity.last_run.failed ?? 0}</td>
                      <td className={td}>{unmappedCount(entity)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="px-2 pb-2">
          {unavailable.length > 0 && (
            <ul className="space-y-1 px-2 pt-2">
              {unavailable.map((entity) => (
                <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="warning">Unavailable</Badge>
                  <span className="font-medium text-foreground">{entityLabel(entity.key)}</span>
                  <span className="text-muted-foreground">{unavailableSentence(entity)}</span>
                </li>
              ))}
            </ul>
          )}
          {short.length > 0 && (
            <ul className="space-y-1 px-2 pt-2" aria-label="Short reads">
              {short.map((entity) => (
                <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="warning">Short read</Badge>
                  <span className="font-medium text-foreground">{label(entity.key)}</span>
                  <span className="min-w-0 flex-1 text-muted-foreground">{shortReadSentence(entity)}</span>
                </li>
              ))}
            </ul>
          )}
          {stalled.length > 0 && (
            <ul className="space-y-1 px-2 pt-2" aria-label="Paused syncs">
              {stalled.map((entity) => (
                <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="warning">Paused</Badge>
                  <span className="min-w-0 flex-1 text-muted-foreground">
                    {tieStallSentence(label(entity.key))}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {activities.length > 0 && (
            <ul className="space-y-1 px-2 pt-2" aria-label="Activities left out">
              {activities.map((line) => (
                <li key={line.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone={ACTIVITY_BADGES[line.kind].tone}>{ACTIVITY_BADGES[line.kind].label}</Badge>
                  <span className="min-w-0 flex-1 text-muted-foreground">{line.text}</span>
                </li>
              ))}
            </ul>
          )}
          {held.length > 0 && (
            <ul className="space-y-1 px-2 pt-2" aria-label="Held modules">
              {held.map((key) => {
                const count = recordsOf(health, key);
                return (
                  <li key={key} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone="neutral">Held</Badge>
                    {/* The sentence starts with the name, so the name span goes only without one. */}
                    {count === null ? (
                      <span className="min-w-0 flex-1 font-medium text-foreground">{label(key)}</span>
                    ) : (
                      <span className="min-w-0 flex-1 text-muted-foreground">
                        {holdSentence(label(key), count)}
                      </span>
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      className="ml-auto shrink-0"
                      loading={busyKey === key}
                      disabled={running || (busyKey !== null && busyKey !== key)}
                      onClick={() => onSyncOnly(key)}
                    >
                      Sync it on its own
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          {large.length > 0 && (
            <ul className="space-y-1 px-2 pt-2" aria-label="Large modules">
              {large.map((entity) => (
                <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="neutral">Large</Badge>
                  <span className="min-w-0 flex-1 text-muted-foreground">
                    {largeSentence(label(entity.key), recordsOf(health, entity.key, entity) ?? 0)}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="ml-auto shrink-0"
                    loading={busyKey === entity.key}
                    disabled={busyKey !== null && busyKey !== entity.key}
                    onClick={() => onSetSync(entity.key, false)}
                  >
                    Hold it
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {health.modules.length > 0 && (
          <div className="px-4 py-3" aria-label="Modules in your Zoho">
            <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Modules in your Zoho
            </h3>
            <ul className="space-y-1 pt-2">
              {health.modules.map((module) => (
                <li key={module.api_name} className="flex items-center gap-2 text-xs">
                  <span className="text-foreground">{module.label}</span>
                  {module.custom && <span className="text-muted-foreground">custom</span>}
                  <span className="ml-auto text-muted-foreground">{moduleState(module)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="px-4 py-3" aria-label="Credits">
          <ProgressBar
            percent={creditsPercent(health.credits)}
            label="Credits used in 24 hours"
            detail={`${health.credits.used_24h} of ${health.credits.budget_24h} credits in 24 hours`}
          />
        </div>

        <div className="px-4 py-3" aria-label="Recent runs">
          <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Recent runs
          </h3>
          {health.history.length === 0 ? (
            <p className="pt-2 text-xs text-muted-foreground">No run yet.</p>
          ) : (
            <ul className="space-y-0.5 pt-2">
              {health.history.map((run, i) => (
                <li key={`${run.started_at}-${i}`} className="flex gap-2 text-xs text-foreground">
                  <span>{runLine(run)}</span>
                  <span className="ml-auto text-muted-foreground">
                    {agoLabel(run.started_at, nowMs)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

/** The card with its data. The page draws it for an admin in Mirror mode. */
export default function SyncHealthCard() {
  const toast = useToast();
  const { health, loading } = useSyncHealth(true);
  const [syncing, setSyncing] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  async function syncNow() {
    setSyncing(true);
    try {
      await api.syncZohoNow();
      toast.show({ key: "crm-sync", variant: "success", title: "Sync started" });
    } catch (err) {
      const e = err instanceof CrmError ? err : null;
      toast.show({
        key: "crm-sync",
        variant: "error",
        title: syncFailureMessage(e?.status ?? 0, e?.code),
      });
    } finally {
      setSyncing(false);
      invalidateCrmMode();
    }
  }

  /** CRM-Q16: hold a key (`sync: false`) or release it (`sync: true`). */
  async function setSync(key: string, sync: boolean) {
    const name = entityLabel(key, health?.modules);
    setBusyKey(key);
    try {
      await api.setModuleSync(key, sync);
      toast.show({
        key: "crm-sync-hold",
        variant: "success",
        title: sync ? `${name} syncs with the rest` : `${name} is held`,
      });
    } catch (err) {
      const e = err instanceof CrmError ? err : null;
      toast.show({
        key: "crm-sync-hold",
        variant: "error",
        title: holdFailureMessage(e?.status ?? 0, e?.code),
      });
    } finally {
      setBusyKey(null);
      invalidateCrmMode();
    }
  }

  /** CRM-Q16: one key on its own, also when it is held. */
  async function syncOnly(key: string) {
    setBusyKey(key);
    try {
      await api.syncZohoNow({ only: key });
      toast.show({ key: "crm-sync", variant: "success", title: "Sync started" });
    } catch (err) {
      const e = err instanceof CrmError ? err : null;
      toast.show({
        key: "crm-sync",
        variant: "error",
        title: syncFailureMessage(e?.status ?? 0, e?.code),
      });
    } finally {
      setBusyKey(null);
      invalidateCrmMode();
    }
  }

  if (!health) {
    return (
      <Card title="Sync health" note={NOTE}>
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {loading ? "Loading." : "The sync health is not available."}
        </p>
      </Card>
    );
  }
  return (
    <SyncHealthCardView
      health={health}
      syncing={syncing}
      onSyncNow={syncNow}
      busyKey={busyKey}
      onSetSync={setSync}
      onSyncOnly={syncOnly}
    />
  );
}
