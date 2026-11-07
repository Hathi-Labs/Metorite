"use client";

/**
 * The History list of one guidance scope, and one version opened read-only
 * (WS-MCP SW-31b).
 *
 * History is append only. "Use this text" copies an old version into the
 * editor, and a save of it makes a NEW version. Nothing here writes.
 */
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";

import { type GuidanceVersion, versionSummary, writtenWhen } from "./guidance";

export interface GuidanceHistoryProps {
  rows: GuidanceVersion[];
  openId: string | null;
  onOpen: (id: string | null) => void;
  /** Absent when the member cannot write this scope. */
  onUse?: (body: string) => void;
}

export function GuidanceHistory({ rows, openId, onOpen, onUse }: GuidanceHistoryProps) {
  const open = rows.find((r) => r.id === openId) ?? null;
  return (
    <section aria-labelledby="guidance-history" className="flex min-w-0 flex-col gap-2">
      <h3 id="guidance-history" className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        <Icon name="History" size={14} className="text-muted-foreground" />
        History
      </h3>
      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-4 text-xs text-muted-foreground">
          No versions yet. Each save adds a version here, and the old versions stay.
        </p>
      ) : (
        <ol className="flex flex-col gap-1">
          {rows.map((r) => {
            const isOpen = r.id === openId;
            return (
              <li key={r.id}>
                <Button
                  variant="ghost"
                  size="none"
                  layout="flex w-full flex-col items-start gap-0.5 text-left"
                  selected={isOpen}
                  aria-expanded={isOpen}
                  onClick={() => onOpen(isOpen ? null : r.id)}
                  className="px-2.5 py-1.5"
                >
                  <span className="flex w-full items-center gap-2 text-xs">
                    <span className="font-medium text-foreground">Version {r.version}</span>
                    <span className="ml-auto shrink-0 text-muted-foreground">{writtenWhen(r.written_at)}</span>
                  </span>
                  <span className="w-full truncate text-xs text-muted-foreground">{r.written_by}</span>
                  <span className="w-full truncate text-xs text-foreground/80">{versionSummary(r.body)}</span>
                </Button>
              </li>
            );
          })}
        </ol>
      )}
      {open && (
        <div
          role="region"
          aria-label={`Version ${open.version}, read-only`}
          className="flex flex-col gap-2 rounded-lg border border-border bg-secondary/30 p-3"
        >
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Version {open.version}</span>
            <span>read-only</span>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label="Close this version"
              title="Close"
              onClick={() => onOpen(null)}
              className="ml-auto"
            />
          </div>
          <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words font-mono text-xs text-foreground">
            {open.body || "This version cleared the guidance."}
          </div>
          {onUse && (
            <Button size="sm" variant="secondary" icon="Copy" onClick={() => onUse(open.body)}>
              Use this text
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

export default GuidanceHistory;
