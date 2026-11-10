"use client";

/**
 * The list of connections: one row per connection, filtered by the page. It
 * draws three states: loading (a skeleton), empty (an `EmptyState`) and rows.
 * A row opens the side panel.
 */

import Icon from "@/components/Icon";
import { EmptyState } from "@/components/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { statusAccent } from "@/lib/statusAccent";

import {
  APP_LABEL,
  STATUS_COLOR,
  STATUS_LABEL,
  actsAsLine,
  errorMessage,
  lastSyncLabel,
  rowLabel,
  scopeLabel,
  type Connection,
  type ConnectionKind,
} from "../lib/connections";

const KIND_ICON: Record<ConnectionKind, string> = {
  oauth: "Plug",
  mailbox: "Mail",
  whatsapp: "MessageCircle",
  mcp: "Server",
  api_key: "KeyRound",
};

export function ConnectionState({ status }: { status: Connection["status"] }) {
  const accent = statusAccent({ color: STATUS_COLOR[status] });
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${accent.chip}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${accent.dot}`} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

export default function ConnectionList({
  rows,
  total,
  loading,
  selectedId,
  now,
  onOpen,
  onClearFilters,
}: {
  rows: Connection[];
  /** Rows before the filters. Zero means there are none at all. */
  total: number;
  loading: boolean;
  selectedId?: string | null;
  now?: number;
  onOpen: (id: string) => void;
  onClearFilters?: () => void;
}) {
  if (loading) {
    return (
      <div className="p-4" role="status" aria-label="Loading connections">
        <SkeletonRows count={6} />
      </div>
    );
  }
  if (total === 0) {
    return (
      <EmptyState
        icon="Plug"
        message="No connections yet."
        hint="Connect an account in its app, or add an MCP server here."
      />
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="Filter"
        message="No connections match these filters."
        action={onClearFilters ? { label: "Clear filters", onClick: onClearFilters } : undefined}
      />
    );
  }
  return (
    <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto" aria-label="Connections">
      {rows.map((row) => {
        const error = errorMessage(row.last_error);
        // IN-8c (D98): who an OAuth MCP connection acts as. React draws it
        // as text, so a name with markup in it stays text.
        const actsAs = actsAsLine(row);
        return (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onOpen(row.id)}
              aria-current={selectedId === row.id ? "true" : undefined}
              className={`flex w-full items-center gap-3 px-4 py-2.5 text-left tech-transition hover:bg-secondary ${selectedId === row.id ? "bg-secondary" : ""}`}
            >
              <Icon name={KIND_ICON[row.kind]} className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">{rowLabel(row)}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {row.provider} · {APP_LABEL[row.owning_app]} · {scopeLabel(row)}
                </span>
                {actsAs ? (
                  <span className="block truncate text-xs text-muted-foreground" data-acts-as="">
                    {actsAs}
                  </span>
                ) : null}
                {error ? <span className="block truncate text-xs text-destructive">{error}</span> : null}
              </span>
              <ConnectionState status={row.status} />
              <span className="hidden w-20 shrink-0 text-right text-xs text-muted-foreground sm:block">
                {lastSyncLabel(row.last_sync_at, now)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
