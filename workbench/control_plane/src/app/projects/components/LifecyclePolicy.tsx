"use client";

/**
 * Projects · the lifecycle policy dialog (WS-27z).
 *
 * Three root-project settings and nothing else: archive-after months,
 * close-after months, and the timezone whose midnight "a month untouched" is
 * measured against. The sweep itself is a `/workflows` scheduled workflow
 * (D6) — this dialog edits the POLICY the sweep reads, and an empty months
 * box means that policy is off, which is the default for every project.
 */

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import ManagerFrame from "./ManagerFrame";
import { useState } from "react";

import { type ProjectRow, projectsApi } from "../lib/api";
import { parseMonths } from "../lib/lifecycle";

interface Props {
  project: ProjectRow;
  onClose: () => void;
  /** Fired with the fresh row after a save, so the tree's copy stays honest. */
  onSaved: (fresh: ProjectRow) => void;
  /** Drawn as a section of Projects settings, not a dialog (WS-42). */
  inline?: boolean;
}

export function LifecyclePolicy({ project, onClose, onSaved, inline = false }: Props) {
  // The values last saved inline, so "Saved." shows only while the form
  // still holds them (an edit after a save is not saved).
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [archiveMonths, setArchiveMonths] = useState(
    project.archive_after_months == null ? "" : String(project.archive_after_months)
  );
  const [closeMonths, setCloseMonths] = useState(
    project.close_after_months == null ? "" : String(project.close_after_months)
  );
  const [timezone, setTimezone] = useState(project.timezone ?? "UTC");
  const current = `${archiveMonths}|${closeMonths}|${timezone}`;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const archive = parseMonths(archiveMonths);
    const close = parseMonths(closeMonths);
    if (!archive.ok) return setError(`Archive after: ${archive.reason}`);
    if (!close.ok) return setError(`Close after: ${close.reason}`);
    setBusy(true);
    setError(null);
    try {
      const fresh = await projectsApi.patchProject(project.id, {
        archive_after_months: archive.value,
        close_after_months: close.value,
        // The gateway validates the IANA name; its 422 names the bad zone.
        timezone: timezone.trim() || "UTC",
      });
      onSaved(fresh);
      onClose();
      // Inline, nothing closes, so the save must say itself (WS-42).
      if (inline) setSavedAs(current);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    // WS-27ak — same as FieldManager: no Escape, no outside press and no focus
    // trap before the primitive.
    <ManagerFrame
      inline={inline}
      onClose={onClose}
      title="Lifecycle"
      description={`${project.name} and everything under it — swept on a schedule`}
      size="md"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      <div className="space-y-3 p-3 text-sm">
        <label className="block">
          <span className="text-xs text-muted-foreground">
            Archive done/cancelled tasks untouched for (months)
          </span>
          <Input
            value={archiveMonths}
            disabled={busy}
            onChange={(e) => setArchiveMonths(e.target.value)}
            placeholder="off"
            aria-label="Archive after months"
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground">
            Close stale open tasks untouched for (months)
          </span>
          <Input
            value={closeMonths}
            disabled={busy}
            onChange={(e) => setCloseMonths(e.target.value)}
            placeholder="off"
            aria-label="Close after months"
            className="mt-1"
          />
          <span className="mt-0.5 block text-[10px] text-muted-foreground">
            Tasks waiting in triage are never touched. Leave a box empty to
            turn that policy off.
          </span>
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground">
            Timezone (IANA name — sets the midnight the windows count from)
          </span>
          <Input
            value={timezone}
            disabled={busy}
            onChange={(e) => setTimezone(e.target.value)}
            placeholder="UTC"
            aria-label="Project timezone"
            className="mt-1"
          />
        </label>
      </div>

      <footer className="flex justify-end gap-2 border-t border-border px-3 py-2">
        {savedAs === current ? (
          <p role="status" className="mr-auto self-center text-xs text-muted-foreground">
            Saved.
          </p>
        ) : null}
        {inline ? null : (
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        )}
        <Button size="sm" onClick={() => void save()} disabled={busy}>
          Save
        </Button>
      </footer>
    </ManagerFrame>
  );
}
