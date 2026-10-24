"use client";

/**
 * Projects · "Convert to project" (WS-64 OP-10, D-OP-16).
 *
 * A folder becomes a project, and the projects inside it become its
 * subprojects. The row menu offers this only where the server says
 * `can_convert`. The dialog says what happens before the click, and it
 * shows the server's refusal in place: a folder whose projects have
 * subprojects of their own gets a 422 that names them.
 *
 * Drawn from the house primitives (`DESIGN_SYSTEM.md`): `Modal` and `Button`.
 */

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

import type { ProjectRow } from "../lib/api";
import { convertCopy } from "../lib/projectConvert";

interface Props {
  /** The folder to convert. `null` closes the dialog. */
  project: ProjectRow | null;
  /** The names of the projects directly inside the folder. */
  childNames: readonly string[];
  /** The server's refusal, shown IN the dialog. */
  error?: string | null;
  /** The page is mid-convert. */
  busy?: boolean;
  onClose: () => void;
  /** Confirmed. The page owns the call, the toast and the refetch. */
  onConfirm: (project: ProjectRow) => void;
}

export function ConvertFolderDialog({
  project,
  childNames,
  error,
  busy,
  onClose,
  onConfirm,
}: Props) {
  if (!project) return null;
  const copy = convertCopy(project.name, childNames);
  return (
    <Modal
      open
      onClose={onClose}
      title="Convert to project"
      icon="FolderKanban"
      size="sm"
      description={copy.description}
    >
      <div className="space-y-3 p-3 text-xs">
        <p className="text-foreground">{copy.children}</p>
        <p className="text-muted-foreground">{copy.note}</p>
        {error ? (
          <p
            role="alert"
            className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive"
          >
            {error}
          </p>
        ) : null}
      </div>
      <div className="flex justify-end gap-2 px-3 pb-3">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          size="sm"
          icon="FolderKanban"
          loading={busy}
          onClick={() => onConfirm(project)}
        >
          Convert to project
        </Button>
      </div>
    </Modal>
  );
}

export default ConvertFolderDialog;
