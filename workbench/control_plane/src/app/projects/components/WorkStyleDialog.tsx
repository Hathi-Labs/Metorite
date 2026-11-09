"use client";

/**
 * Projects · "How this team works", the work-style picker of a node (WS-39
 * SP-3a, `project_management_app.md` §13.1, D195).
 *
 * Any node has a style: a space, a folder or a project, set by hand, with no
 * inheritance. Flow is the default. The picker lists the five styles of the
 * catalogue. Only the live ones can be picked, and the others show disabled
 * with their reason (`lib/workStyle.ts`).
 *
 * A save PATCHes the node with the existing write right. A change of style
 * deletes nothing: it hides the cycle UI, and a change back shows it again.
 */

import { useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";

import { type ProjectRow, projectsApi } from "../lib/api";
import { WORK_STYLES, type WorkStyleKey, workStyleOf } from "../lib/workStyle";
import ManagerFrame from "./ManagerFrame";

interface Props {
  node: ProjectRow;
  onClose: () => void;
  /** The fresh row after a save, so the tree and the tab row read it. */
  onSaved: (fresh: ProjectRow) => void;
}

export function WorkStyleDialog({ node, onClose, onSaved }: Props) {
  const current = workStyleOf(node);
  const [picked, setPicked] = useState<WorkStyleKey>(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (picked === current) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const fresh = await projectsApi.patchProject(node.id, { work_style: picked });
      onSaved(fresh);
      onClose();
    } catch (err) {
      // The gateway's own sentence: an unknown style, or a node that is gone.
      setError((err as Error).message || "Could not save the work style. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ManagerFrame
      onClose={onClose}
      title="How this team works"
      description={`${node.name}. A change hides or shows parts of the project and deletes nothing.`}
      icon="Workflow"
      size="md"
    >
      {error ? (
        <p role="alert" className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      <div role="radiogroup" aria-label="Work style" className="flex flex-col gap-1 p-2" data-work-style-picker="">
        {WORK_STYLES.map((style) => {
          const on = picked === style.key;
          return (
            <button
              key={style.key}
              type="button"
              role="radio"
              aria-checked={on}
              aria-disabled={!style.live || undefined}
              disabled={!style.live || busy}
              data-work-style={style.key}
              title={style.live ? undefined : style.reason}
              onClick={() => setPicked(style.key)}
              className={[
                "cc-link tech-transition flex w-full items-start gap-2 rounded-md px-3 py-2 text-left",
                on ? "bg-primary/10 text-primary" : "text-foreground hover:bg-muted",
                style.live ? "" : "cursor-not-allowed opacity-60 hover:bg-transparent",
              ].join(" ")}
            >
              <span
                aria-hidden="true"
                className={`mt-1 size-3 shrink-0 rounded-full border ${
                  on ? "border-primary bg-primary" : "border-muted-foreground"
                }`}
              />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                  <span className="truncate">{style.label}</span>
                  {style.key === "flow" ? (
                    <span className="text-[11px] font-normal text-muted-foreground">Default</span>
                  ) : null}
                  {style.live ? null : (
                    <Badge tone="neutral" size="xs">
                      {style.reason}
                    </Badge>
                  )}
                </span>
                <span className="text-xs text-muted-foreground">{style.hint}</span>
              </span>
            </button>
          );
        })}
      </div>
      <footer className="flex justify-end gap-2 border-t border-border px-3 py-2">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button size="sm" onClick={() => void save()} loading={busy} disabled={busy} data-work-style-save="">
          Save work style
        </Button>
      </footer>
    </ManagerFrame>
  );
}

export default WorkStyleDialog;
