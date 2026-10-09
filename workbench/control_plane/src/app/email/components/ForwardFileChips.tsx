"use client";

/**
 * The files of the email in the forward compose of the reading pane, one
 * chip each, kept by default (follow-up 4 of #766).
 *
 * The tick of a chip keeps the file or takes it out. Outlook forwards every
 * file of an email or none (`OUTLOOK_SUBSET` in `forward.py`), so on an
 * Outlook mailbox a chip taken out shows a notice that says so, with
 * "Keep every file" and "Forward without files".
 *
 * Pure tokens and the shared primitives (`Checkbox`, `Button`, `Icon`), so
 * the chips follow colour mode, density and accent with the rest of the pane.
 */

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import AppIcon from "@/components/Icon";
import { OUTLOOK_ALL_OR_NONE, fileSizeText, visibleFileName, type ForwardFile } from "../lib/forward";

interface ForwardFileChipsProps {
  files: readonly ForwardFile[];
  onToggle: (id: string, checked: boolean) => void;
  /** Show the Outlook notice: some files kept, some taken out. */
  outlookSubset: boolean;
  onKeepAll: () => void;
  onWithoutFiles: () => void;
  disabled?: boolean;
}

export function ForwardFileChips({
  files,
  onToggle,
  outlookSubset,
  onKeepAll,
  onWithoutFiles,
  disabled = false,
}: ForwardFileChipsProps) {
  if (files.length === 0) return null;
  const kept = files.filter((f) => f.checked).length;
  return (
    <div className="px-4 pb-2" data-forward-files="">
      <p className="mb-1 text-[10px] text-muted-foreground">
        {kept === files.length
          ? `Forwards ${files.length === 1 ? "the file" : `all ${files.length} files`} of the email`
          : kept === 0
            ? "Forwards no file of the email"
            : `Forwards ${kept} of ${files.length} files of the email`}
      </p>
      <ul className="flex flex-wrap gap-1.5" aria-label="Files of the email">
        {files.map((f) => {
          const size = fileSizeText(f.sizeBytes);
          // No bidi control reaches the text or the title, and <bdi> keeps
          // the name from turning the words around it (review round 1).
          const name = visibleFileName(f.filename);
          return (
            <li key={f.id}>
              <label
                className={`inline-flex max-w-full cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] ${
                  f.checked
                    ? "border-primary/40 bg-primary/5 text-foreground"
                    : "border-border bg-secondary text-muted-foreground"
                }`}
                title={f.checked ? `Forward ${name}` : `${name} is not forwarded`}
                data-forward-file={f.id}
              >
                <Checkbox
                  size="sm"
                  checked={f.checked}
                  disabled={disabled}
                  onChange={(e) => onToggle(f.id, e.target.checked)}
                  aria-label={`Forward ${name}`}
                />
                <AppIcon name="Paperclip" size={10} />
                <bdi className={`max-w-[160px] truncate ${f.checked ? "" : "line-through"}`}>{name}</bdi>
                {size && <span className="shrink-0 text-muted-foreground">{size}</span>}
              </label>
            </li>
          );
        })}
      </ul>
      {outlookSubset && (
        <div
          role="status"
          data-forward-outlook=""
          className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-[11px] text-foreground"
        >
          <AppIcon name="Info" size={12} className="shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">{OUTLOOK_ALL_OR_NONE}</span>
          <Button variant="secondary" size="sm" onClick={onKeepAll} disabled={disabled}>
            Keep every file
          </Button>
          <Button variant="secondary" size="sm" onClick={onWithoutFiles} disabled={disabled}>
            Forward without files
          </Button>
        </div>
      )}
    </div>
  );
}
