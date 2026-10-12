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
import ProgressBar from "@/components/ui/ProgressBar";
import { useToast } from "@/components/ui/Toast";
import { useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { syncFailureMessage } from "../lib/connect";
import {
  UNAVAILABLE_SENTENCE,
  agoLabel,
  creditsPercent,
  difference,
  differenceLabel,
  entityLabel,
  isRunning,
  isUnavailable,
  moduleState,
  runLine,
  statusLines,
  type SyncHealth,
} from "../lib/syncHealth";
import { invalidateCrmMode, useSyncHealth } from "../lib/useCrmMode";
import { Card } from "./SettingsCards";

const NOTE = "The Zoho count against ours, skipped records and the last error.";

const th = "px-2 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-muted-foreground";
const td = "px-2 py-1.5 text-xs text-foreground tabular-nums";

/** The card, drawn from a health answer that the caller already holds. */
export function SyncHealthCardView({
  health,
  nowMs = Date.now(),
  syncing = false,
  onSyncNow,
}: {
  health: SyncHealth;
  nowMs?: number;
  syncing?: boolean;
  onSyncNow: () => void;
}) {
  const lines = statusLines(health, nowMs);
  const running = isRunning(health);
  const unavailable = health.entities.filter(isUnavailable);
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

        <div className="overflow-x-auto px-2 py-2" aria-label="Counts by entity">
          <table className="w-full min-w-[32rem]">
            <thead>
              <tr>
                <th className={th}>Entity</th>
                <th className={th}>Zoho</th>
                <th className={th}>Ours</th>
                <th className={th}>Difference</th>
                <th className={th}>Skipped</th>
                <th className={th}>Unlinked</th>
                <th className={th}>Failed</th>
              </tr>
            </thead>
            <tbody>
              {health.entities.map((entity) => {
                const diff = difference(entity);
                return (
                  <tr key={entity.key} aria-label={entityLabel(entity.key)}>
                    <td className="px-2 py-1.5 text-xs font-medium text-foreground">
                      {entityLabel(entity.key)}
                    </td>
                    <td className={td}>{entity.zoho_count ?? "—"}</td>
                    <td className={td}>{entity.ours ?? "—"}</td>
                    <td className={td}>
                      {diff !== null && <Badge tone="warning">{differenceLabel(diff)}</Badge>}
                    </td>
                    <td className={td}>{entity.last_run.skipped ?? 0}</td>
                    <td className={td}>{entity.last_run.unlinked ?? 0}</td>
                    <td className={td}>{entity.last_run.failed ?? 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {unavailable.length > 0 && (
            <ul className="space-y-1 px-2 pt-2">
              {unavailable.map((entity) => (
                <li key={entity.key} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge tone="warning">Unavailable</Badge>
                  <span className="font-medium text-foreground">{entityLabel(entity.key)}</span>
                  <span className="text-muted-foreground">{UNAVAILABLE_SENTENCE}</span>
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

  if (!health) {
    return (
      <Card title="Sync health" note={NOTE}>
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {loading ? "Loading." : "The sync health is not available."}
        </p>
      </Card>
    );
  }
  return <SyncHealthCardView health={health} syncing={syncing} onSyncNow={syncNow} />;
}
