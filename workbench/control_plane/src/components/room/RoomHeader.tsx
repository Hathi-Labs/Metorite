"use client";

/**
 * The room header — who is here, what the agents can reach, and the way in.
 *
 * Quiet while you are alone: a solo conversation renders one Share button and
 * nothing else, because a person working by themselves should not be told
 * repeatedly that they are working by themselves.
 *
 * Loud about exactly one thing: the capability cap. When a room's agents have
 * lost an integration because of who is in the room, that has to be stated
 * where the work happens, not filed in a settings page. Privacy and permission
 * that live only in a settings page are neither.
 */

import Icon from "@/components/Icon";
import { useState } from "react";
import {
  capabilityLabel,
  isShared as roomIsShared,
  peopleOf,
  type PresenceEntry,
  type RoomState,
} from "@/lib/rooms";
import { AvatarStack } from "./Identity";
import { ShareSheet } from "./ShareSheet";

export function RoomHeader({
  room,
  presence,
  onChanged,
  compact = false,
}: {
  room: RoomState | null;
  presence: PresenceEntry[];
  onChanged: () => void | Promise<void>;
  /** Mobile / rail-embedded surfaces drop the sub-line. */
  compact?: boolean;
}) {
  const [sharing, setSharing] = useState(false);
  if (!room) return null;

  const shared = roomIsShared(room);
  const people = peopleOf(room);
  const here = new Set(presence.map((p) => p.email));
  const capped = room.cap.capped;
  const suspended = room.cap.inactive;
  const watching = room.you.role === "viewer";

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          {shared && (
            <>
              <AvatarStack people={people} here={here} />
              <span className="text-xs text-muted-foreground">
                {here.size > 0
                  ? `${here.size} here now · ${people.length} in the room`
                  : `${people.length} in the room`}
              </span>
            </>
          )}

          {shared && room.agents.length > 1 && (
            <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground glass:border-transparent glass:bg-secondary glass:px-2.5 glass:text-caption">
              {room.agents.length} agents
            </span>
          )}

          {watching && (
            <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground glass:border-transparent glass:bg-secondary glass:px-2.5 glass:text-caption">
              <Icon name="Eye" className="h-3 w-3" />
              Watching
            </span>
          )}

          <button
            onClick={() => setSharing(true)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground tech-transition hover:border-primary hover:text-foreground glass:glass-press glass:h-7 glass:rounded-full glass:border-transparent glass:bg-secondary glass:px-3 glass:py-0 glass:text-footnote glass:font-medium glass:text-foreground glass:hover:border-transparent glass:hover:bg-muted"
            title={shared ? "Manage who is in this room" : "Share this conversation"}
          >
            {shared ? <Icon name="Users" className="h-3.5 w-3.5" /> : <Icon name="Share2" className="h-3.5 w-3.5" />}
            {shared ? "Room" : "Share"}
          </button>
        </div>

        {/* The cap, stated. This is the one thing the header exists to say. */}
        {shared && suspended.length > 0 && (
          <p className="flex items-start gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive glass:rounded-[14px] glass:border-transparent glass:px-3 glass:py-2 glass:text-caption">
            <Icon name="AlertTriangle" className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              {suspended.map((e) => e.split("@")[0]).join(", ")} is suspended, so
              the agents here can reach nothing until they are removed from the
              room.
            </span>
          </p>
        )}

        {shared && suspended.length === 0 && capped.length > 0 && !compact && (
          <p className="flex items-start gap-1.5 rounded-lg border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-[11px] text-muted-foreground glass:rounded-[14px] glass:border-transparent glass:px-3 glass:py-2 glass:text-caption">
            <Icon name="AlertTriangle" className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
            <span>
              Unavailable in this room:{" "}
              <span className="text-foreground">
                {capped.slice(0, 3).map((c) => capabilityLabel(c.capability)).join(", ")}
                {capped.length > 3 ? ` +${capped.length - 3} more` : ""}
              </span>{" "}
              — not everyone here has access.{" "}
              <button
                onClick={() => setSharing(true)}
                className="underline underline-offset-2 hover:text-foreground"
              >
                Why
              </button>
            </span>
          </p>
        )}
      </div>

      {sharing && (
        <ShareSheet
          room={room}
          onClose={() => setSharing(false)}
          onChanged={onChanged}
        />
      )}
    </>
  );
}
