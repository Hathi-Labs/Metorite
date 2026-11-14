"use client";

/**
 * Connect in place (WS-STU I8a, `ai_studio.md` §8.1 and §14 I8a): the lines of the connections of a
 * draft, and the Connect sheet that one of them opens.
 *
 * Interaction: the owner of an app sees one line for each connection that the draft names. A met one
 * reads "<provider> is connected." An unmet one has Connect. Connect opens a sheet over Studio that
 * says where Metorite goes, and its button opens the connect page of the owning app with the way
 * back to this app. A member who may not connect sees one sentence and no button. A name that the
 * gateway cannot check yet (a mailbox, a WhatsApp number, until I9) says so, with no button. A read that
 * failed shows one line with Retry, so it never looks like a draft with no connection.
 *
 * The sheet is the AF-6 pattern (`StudioFormDialog.tsx`): the shared `Modal`, a sheet on a phone. The
 * button is a link to the server's `setup_href` (`lib/studio/connect.ts`), so the leave guard of the
 * IDE asks before an unsaved draft goes. Studio copies no connect flow and no link of its own.
 */
import { useState } from "react";

import Icon from "@/components/Icon";
import { useBelowSm } from "@/components/studio/StudioFormDialog";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { sheetCopy, subject, type ConnectLine } from "@/lib/studio/connect";

const LINE_ICON: Record<ConnectLine["kind"], { name: string; className: string }> = {
  met: { name: "CircleCheck", className: "text-success" },
  connect: { name: "Plug", className: "text-warning" },
  ask: { name: "Lock", className: "text-muted-foreground" },
  unchecked: { name: "CircleHelp", className: "text-muted-foreground" },
};

export interface ConnectSheetProps {
  /** The line whose sheet is open, or null when it is closed. */
  line: Extract<ConnectLine, { kind: "connect" }> | null;
  onClose: () => void;
}

/** The sheet over Studio. Its one action is a link to the connect page of the owning app. */
export default function ConnectSheet({ line, onClose }: ConnectSheetProps) {
  const phone = useBelowSm();
  const copy = line ? sheetCopy(line.row) : null;
  return (
    <Modal open={line !== null} onClose={onClose} title={copy?.title} icon="Plug"
      placement={phone ? "sheet" : "center"} size="md">
      {line && copy ? (
        <div className="flex flex-col gap-4 p-4">
          <p className="text-sm text-foreground">{copy.body}</p>
          {line.href === null ? (
            <p role="alert" className="text-xs text-destructive">
              Metorite has no connect page for {line.row.label}. Ask an admin to connect it in Integrations.
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" icon="ArrowRight" href={line.href ?? undefined}
              disabled={line.href === null}>
              {copy.action}
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

export interface ConnectLinesProps {
  lines: readonly ConnectLine[];
  /** The words of a read that failed (`CONNECTIONS_READ_FAILED`), or null. */
  failure?: string | null;
  /** Read the connections again. */
  onRetry?: () => void;
}

/** The lines of the connections, with the one sheet that a Connect opens. */
export function ConnectLines({ lines, failure = null, onRetry }: ConnectLinesProps) {
  const [open, setOpen] = useState<Extract<ConnectLine, { kind: "connect" }> | null>(null);
  if (lines.length === 0 && failure === null) return null;
  return (
    <>
      <ul aria-label="Connections" className="mb-2 space-y-1">
        {failure !== null ? (
          <li className="flex min-h-7 items-center gap-2 text-xs">
            <Icon name="CircleAlert" size={14} className="shrink-0 text-warning" />
            {/* Retry sits beside the words: the shell's outage notice can cover the far right during a restart. */}
            <span className="min-w-0 break-words text-foreground">{failure}</span>
            {onRetry ? (
              <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}
                aria-label="Check the connections again">
                Retry
              </Button>
            ) : null}
          </li>
        ) : null}
        {lines.map((line) => {
          const icon = LINE_ICON[line.kind];
          return (
            <li key={line.row.name} className="flex min-h-7 items-center gap-2 text-xs">
              <Icon name={icon.name} size={14} className={`shrink-0 ${icon.className}`} />
              <span className="min-w-0 flex-1 break-words text-foreground">{line.text}</span>
              {line.kind === "connect" ? (
                <Button variant="secondary" size="sm" icon="Plug" onClick={() => setOpen(line)}
                  aria-label={`Connect ${subject(line.row)}`}>
                  Connect
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
      <ConnectSheet line={open} onClose={() => setOpen(null)} />
    </>
  );
}
