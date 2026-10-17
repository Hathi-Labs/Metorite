"use client";

/**
 * The Sync health card of the CRM settings page (WS-53 CRM-Q9, spec section 18.3).
 *
 * It shows what the mirror holds and what it leaves out: the Zoho count against
 * ours, the records the last run skipped, the modules that Zoho has and the
 * mirror does not read, the credits, and the last runs. It is for an admin and
 * for Mirror mode only. In Native mode the page does not draw it.
 *
 * Since WS-53 CRM-Q17b item 8 the card opens with "Needs you" (`needsYou`):
 * the modules that Zoho refuses, the held modules and the large ones, each
 * with one action. The table of counts and the list of Zoho modules fold
 * under "Details". The Sync switch of a turned-off module is disabled.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ProgressBar from "@/components/ui/ProgressBar";
import { useToast } from "@/components/ui/Toast";
import { useId, useState } from "react";
import { pushView } from "../lib/history";
import { DEFAULT_VIEW, selectSection, viewHref } from "../lib/urlState";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { syncFailureMessage } from "../lib/connect";
import {
  NEEDS_YOU_ACTIONS,
  activityLines,
  agoLabel,
  creditsPercent,
  difference,
  differenceLabel,
  entityLabel,
  holdFailureMessage,
  isHeld,
  isHoldable,
  isRunning,
  isTurnedOff,
  moduleState,
  needsYou,
  recordCount,
  runLine,
  shortReadSentence,
  shortReads,
  statusLines,
  tieStallSentence,
  tieStalls,
  unmappedCount,
  type ActivityLine,
  type NeedsYouItem,
  type SyncHealth,
} from "../lib/syncHealth";
import { invalidateCrmMode, useSyncHealth } from "../lib/useCrmMode";
import { Card } from "./SettingsCards";

const NOTE = "The Zoho count against ours, skipped records and the last error.";

/** CRM-Q17b: the badge of each kind of "Needs you" item. */
const NEEDS_YOU_BADGES: Record<NeedsYouItem["kind"], { tone: "warning" | "neutral"; label: string }> = {
  unavailable: { tone: "warning", label: "Unavailable" },
  held: { tone: "neutral", label: "Held" },
  large: { tone: "neutral", label: "Large" },
};

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
  detailsOpen = false,
  onReconnect = () => pushView(viewHref(selectSection(DEFAULT_VIEW, "setup"))),
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
  /** CRM-Q17b item 8: the table of counts starts open. A test opens it. */
  detailsOpen?: boolean;
  /** "Connect again" of a missing scope: Setup and sync, where the consent is. */
  onReconnect?: () => void;
}) {
  const lines = statusLines(health, nowMs);
  const running = isRunning(health);
  const label = (key: string) => entityLabel(key, health.modules);
  const needs = needsYou(health);
  const [open, setOpen] = useState(detailsOpen);
  const detailsId = useId();
  const short = shortReads(health);
  const stalled = tieStalls(health);
  const activities = activityLines(health);
  /** The one button of a "Needs you" item. */
  const act = (item: NeedsYouItem) => {
    if (item.action === "hold") onSetSync(item.key, false);
    else if (item.action === "reconnect") onReconnect();
    else onSyncOnly(item.key);
  };

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

        {/* CRM-Q17b item 8: what needs the admin, each with one action. */}
        {needs.length > 0 && (
          <section className="px-4 py-3" aria-label="Needs you">
            <h3 className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Needs you
            </h3>
            <ul className="space-y-1.5 pt-2">
              {needs.map((item) => {
                const badge = NEEDS_YOU_BADGES[item.kind];
                // "Hold it" writes while a run goes on, as before. A run of
                // one key waits for the run that holds the lock.
                const blocked =
                  (busyKey !== null && busyKey !== item.key) || (item.kind !== "large" && running);
                return (
                  <li key={item.key} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone={badge.tone}>{badge.label}</Badge>
                    {item.name && <span className="font-medium text-foreground">{item.name}</span>}
                    {item.text && (
                      // A whole line of its own on a phone, so the words never
                      // squeeze between the badge and the button.
                      <span className="min-w-0 basis-full text-muted-foreground sm:flex-1 sm:basis-auto">
                        {item.text}
                      </span>
                    )}
                    {item.action ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="ml-auto shrink-0"
                        loading={busyKey === item.key}
                        disabled={item.action === "reconnect" ? false : blocked}
                        onClick={() => act(item)}
                      >
                        {NEEDS_YOU_ACTIONS[item.action]}
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {(short.length > 0 || stalled.length > 0 || activities.length > 0) && (
          <div className="px-2 pb-2">
            {short.length > 0 && (
              <ul className="space-y-1 px-2 pt-2" aria-label="Short reads">
                {short.map((entity) => (
                  <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge tone="warning">Short read</Badge>
                    <span className="font-medium text-foreground">{label(entity.key)}</span>
                    <span className="min-w-0 basis-full text-muted-foreground sm:flex-1 sm:basis-auto">
                      {shortReadSentence(entity)}
                    </span>
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
          </div>
        )}

        {/* CRM-Q17b item 8: the detail table folds. */}
        <div className="px-4 py-2">
          <Button
            variant="ghost"
            size="sm"
            icon={open ? "ChevronDown" : "ChevronRight"}
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((was) => !was)}
          >
            {open ? "Hide the details" : "Show the details"}
          </Button>
        </div>
        {open && (
          <div id={detailsId} className="divide-y divide-border/60">
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
                      // CRM-Q17b item 8: Zoho refuses a turned-off module, so a
                      // switch would change nothing. It is disabled.
                      const off = isTurnedOff(entity);
                      return (
                        <tr key={entity.key} aria-label={label(entity.key)}>
                          <td className="px-2 py-1.5 text-xs font-medium text-foreground">
                            {label(entity.key)}
                          </td>
                          <td className={td}>
                            {isHoldable(entity.key) ? (
                              <Checkbox
                                size="sm"
                                className="align-middle disabled:bg-muted"
                                checked={!heldRow}
                                disabled={off || busyKey === entity.key}
                                title={off ? "Turned off in your Zoho" : undefined}
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
