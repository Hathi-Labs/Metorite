"use client";

/**
 * The provider choices of the connect flow (WS-17 EM-T3b).
 *
 * The empty state and the add-account dialog draw the same list, from
 * `CONNECT_PROVIDERS` in `lib/connect.ts`. One list, so the two places
 * cannot offer different providers.
 *
 * A provider that is not available draws as a disabled row with its note
 * as text beside it. A tooltip alone is unreachable from a keyboard and
 * absent on touch (the `EmptyState` rule).
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CONNECT_PROVIDERS, type ConnectProviderId } from "../lib/connect";

export function ConnectChoices({
  onConnect,
}: {
  onConnect: (provider: ConnectProviderId) => void;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {CONNECT_PROVIDERS.map((p) => (
        <li key={p.id}>
          <Button
            variant={p.available ? "secondary" : "ghost"}
            size="none"
            layout="flex w-full items-center text-left"
            disabled={!p.available}
            onClick={() => p.available && onConnect(p.id)}
            aria-describedby={p.note ? `connect-note-${p.id}` : undefined}
            className={`gap-3 px-3 py-2.5 ${p.available ? "bg-card hover:bg-primary/5" : "border border-dashed border-border"}`}
          >
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
                p.available ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
              }`}
            >
              <Icon name={p.icon} size={18} />
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm font-medium text-foreground">{p.label}</span>
              <span className="text-xs text-muted-foreground">{p.detail}</span>
            </span>
            {p.available ? (
              <Icon name="ArrowRight" size={14} className="shrink-0 text-muted-foreground" />
            ) : (
              <span
                id={`connect-note-${p.id}`}
                className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
              >
                {p.note}
              </span>
            )}
          </Button>
        </li>
      ))}
    </ul>
  );
}
