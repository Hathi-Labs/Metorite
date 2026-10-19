"use client";

/**
 * The versions of a page, newest first, in the history modal (WS-61 KB-2g).
 *
 * Each row shows the date and time, the author with an initials avatar, and
 * the reason in words. A click shows that version. Two acts sit on each row,
 * shown on hover and on keyboard focus: Compare starts a compare from that
 * version, and Restore asks to write it again as a new version.
 *
 * In compare mode each row holds a checkbox, and the member picks two.
 */

import { Checkbox } from "@/components/ui/Checkbox";
import Button from "@/components/ui/Button";

import { authorName, versionDate } from "../../lib/history";
import { reasonLabel } from "../../lib/labels";
import type { PageVersion } from "../../lib/types";
import AuthorAvatar from "./AuthorAvatar";

export default function HistoryList({
  versions,
  active,
  comparing,
  picked,
  canRestore,
  onSelect,
  onTogglePick,
  onStartCompare,
  onRestore,
}: {
  versions: readonly PageVersion[];
  /** The version on show. Ignored in compare mode. */
  active: number | null;
  /** True while the member picks two versions. */
  comparing: boolean;
  picked: readonly number[];
  canRestore: boolean;
  onSelect: (n: number) => void;
  onTogglePick: (n: number) => void;
  onStartCompare: (n: number) => void;
  onRestore: (n: number) => void;
}) {
  const newest = versions[0]?.version ?? null;
  return (
    <ol className="space-y-0.5 p-1.5" aria-label="Versions">
      {versions.map((v) => {
        const date = versionDate(v.created_at) || `Version ${v.version}`;
        const on = !comparing && v.version === active;
        const isPicked = picked.includes(v.version);
        return (
          <li
            key={v.version}
            data-version={v.version}
            className={`group flex items-center gap-1 rounded-md ${on ? "bg-primary/10" : "hover:bg-muted/40"}`}
          >
            {comparing ? (
              <Checkbox
                size="sm"
                className="ml-2"
                checked={isPicked}
                disabled={!isPicked && picked.length >= 2}
                onChange={() => onTogglePick(v.version)}
                aria-label={`Pick version ${v.version}, ${date}`}
              />
            ) : null}
            <button
              type="button"
              aria-current={on ? "true" : undefined}
              onClick={() => (comparing ? onTogglePick(v.version) : onSelect(v.version))}
              className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className={`block truncate text-sm ${on ? "text-primary" : "text-foreground"}`}>{date}</span>
              <span className="mt-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                <AuthorAvatar email={v.author_email} />
                <span className="min-w-0 truncate" title={v.author_email}>
                  {authorName(v.author_email)}
                </span>
                <span aria-hidden="true">·</span>
                <span className="shrink-0">
                  {reasonLabel(v.reason)}, v{v.version}
                </span>
              </span>
            </button>
            {comparing ? null : (
              <span
                className={`mr-1 flex shrink-0 items-center gap-0.5 ${
                  on ? "" : "md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
                }`}
              >
                <Button
                  variant="ghost"
                  size="icon-xs"
                  icon="GitCompareArrows"
                  aria-label={`Compare version ${v.version} with another`}
                  title="Compare"
                  disabled={versions.length < 2}
                  onClick={() => onStartCompare(v.version)}
                />
                {canRestore ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    icon="RotateCcw"
                    aria-label={`Restore version ${v.version}`}
                    title="Restore"
                    disabled={v.version === newest}
                    onClick={() => onRestore(v.version)}
                  />
                ) : null}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
