"use client";

import { useTaskStore } from "../lib/taskStore";
import { PromoteDialog } from "./PromoteDialog";

/**
 * The ONE promote dialog in My Tasks (my_tasks_cutover.md §5 S6g).
 *
 * Mounted once, beside the page's other global surfaces. The Inbox card, the
 * table, the `m` key, the Inbox capture box and the mobile capture sheet all
 * open it through `openPromote`. Rendered outside every row: a dialog inside a
 * row's clickable root opened the row on every click in it (S6c repair).
 */
export function PromoteHost() {
  const request = useTaskStore((s) => s.promoteDialog);
  const close = useTaskStore((s) => s.closePromote);
  const item = useTaskStore((s) =>
    request ? s.items.find((i) => i.id === request.id) : undefined,
  );
  if (!request || !item) return null;
  return (
    <PromoteDialog
      key={`${request.id}:${request.destination ?? ""}`}
      item={item}
      initialDestination={request.destination ?? null}
      onClose={close}
    />
  );
}
