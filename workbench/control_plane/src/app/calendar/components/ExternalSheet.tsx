"use client";

/**
 * The read-only sheet of an external event (CE-5a, `calendar_events_reminders.md`
 * §6.3). A Google or Outlook event is read only here: no edit, no delete and
 * no RSVP. The provider holds the event, and one link goes to it.
 */
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

import { sheetFacts, type ExternalView } from "../lib/externalEntries";

export function ExternalSheet({
  entry,
  zone,
  onClose,
}: {
  entry: ExternalView;
  zone: string;
  onClose: () => void;
}) {
  const f = sheetFacts(entry, zone);
  return (
    <Modal open onClose={onClose} size="sm" icon="CalendarClock" title={f.title} description={f.source}>
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto p-3 text-sm">
        <p className="text-foreground">{f.when}</p>
        {f.zone && <p className="text-xs text-muted-foreground">Time zone: {f.zone}</p>}
        {f.location && <p className="text-foreground">{f.location}</p>}
        {f.note && <p className="text-xs text-muted-foreground">{f.note}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {f.joinUrl && (
            <Button variant="secondary" size="sm" href={f.joinUrl} target="_blank" rel="noopener noreferrer">
              Join
            </Button>
          )}
          {f.openUrl && (
            <Button
              variant="primary"
              size="sm"
              href={f.openUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              {f.openLabel}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
