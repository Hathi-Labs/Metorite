"use client";

/**
 * The "Calendar" row group of the Connections view (WS-CAL CE-10c, D96.1).
 *
 * A calendar connection is made and managed in Calendar settings. This group is READ-ONLY: it
 * lists the live rows and links back to `/calendar?settings=1`, where Calendar opens its
 * settings sheet. The rows come from the calendar BFF (`GET /api/calendar/connections`), so no
 * gateway change is needed. It draws nothing while that list answers "not enabled", while the
 * member has no calendar connection, and while the read fails.
 *
 * Fence: `CalendarConnectionsGroup.test.tsx`.
 */

import Link from "next/link";

import Badge from "@/components/ui/Badge";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  CONNECTIONS_KEY,
  PROVIDER_NAME,
  getConnections,
  isEnabled,
  rowState,
  type CalendarConnection,
  type ConnectionsList,
} from "@/app/calendar/lib/calendarConnections";

export const CALENDAR_SETTINGS_HREF = "/calendar?settings=1";

export function CalendarConnectionsGroupView({ rows }: { rows: CalendarConnection[] }) {
  if (rows.length === 0) return null;
  return (
    <section aria-label="Calendar connections" className="shrink-0 border-b border-border px-4 py-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-foreground">Calendar</h3>
        <Link href={CALENDAR_SETTINGS_HREF} className="text-xs font-medium text-primary hover:underline">
          Manage in Calendar settings
        </Link>
      </div>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-foreground">{PROVIDER_NAME[r.provider]}</span>
            <span className="min-w-0 break-all text-muted-foreground">{r.account_email}</span>
            <Badge tone={rowState(r) === "needs_reconnect" ? "warning" : "success"}>
              {rowState(r) === "needs_reconnect" ? "Sign in again" : "Connected"}
            </Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function CalendarConnectionsGroup() {
  const res = useCachedResource<ConnectionsList | null>(CONNECTIONS_KEY, () => getConnections());
  if (!isEnabled(res.data)) return null;
  return <CalendarConnectionsGroupView rows={res.data.connections} />;
}
