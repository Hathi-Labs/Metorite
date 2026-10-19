"use client";

/**
 * The history of one page (WS-61 KB-2a, `knowledge_base.md` §4.2).
 *
 * Each save is a version, newest first, with its reason in words, its author
 * and its time. A member who can write may revert to any older version, which
 * writes that version's title and body as a NEW version, so nothing in the
 * list is lost. A click on a version opens it in the page history (KB-2g,
 * `history/PageHistory.tsx`), which shows its body and compares it.
 */

import Button from "@/components/ui/Button";
import { relativeTime } from "@/lib/taskCard";

import { reasonLabel } from "../lib/labels";
import type { PageVersion } from "../lib/types";

export default function VersionList({
  versions,
  canRevert,
  onRevert,
  onOpen,
}: {
  versions: readonly PageVersion[];
  canRevert: boolean;
  onRevert: (version: PageVersion) => void;
  /** KB-2g: open this version in the page history. */
  onOpen?: (version: PageVersion) => void;
}) {
  if (!versions.length) {
    return <p className="text-xs text-muted-foreground">No versions to show.</p>;
  }
  const newest = versions[0]?.version;
  return (
    <ol className="space-y-1" aria-label="Versions">
      {versions.map((v) => (
        <li
          key={v.version}
          data-version={v.version}
          className="group flex items-start gap-2 rounded-md px-1.5 py-1 hover:bg-muted/40"
        >
          <span className="mt-0.5 w-7 shrink-0 text-[11px] tabular-nums text-muted-foreground">
            v{v.version}
          </span>
          {onOpen ? (
            <button
              type="button"
              aria-label={`Open version ${v.version} in the page history`}
              onClick={() => onOpen(v)}
              className="min-w-0 flex-1 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <VersionText version={v} />
            </button>
          ) : (
            <span className="min-w-0 flex-1">
              <VersionText version={v} />
            </span>
          )}
          {canRevert && v.version !== newest ? (
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Revert to version ${v.version}`}
              onClick={() => onRevert(v)}
            >
              Revert
            </Button>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function VersionText({ version: v }: { version: PageVersion }) {
  return (
    <>
      <span className="block text-xs text-foreground">{reasonLabel(v.reason)}</span>
      <span className="block truncate text-[11px] text-muted-foreground">
        {v.author_email}
        {v.created_at ? ` · ${relativeTime(v.created_at) || "now"}` : ""}
      </span>
    </>
  );
}
