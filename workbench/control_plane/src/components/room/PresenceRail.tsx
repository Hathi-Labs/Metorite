"use client";

/**
 * The presence rail — the room's side of the conversation.
 *
 * Four sections, in the order a person actually asks the questions:
 * who is here, which agents are in the room, what just happened, and what is
 * shared. Only rendered for a room; a solo thread has no rail at all.
 *
 * Deliberately NOT here: floor control, token budgets, the observer lane. They
 * are in the design (docs/multiplayer/README.md §5, §7) and they are not built
 * yet. Rendering a disabled "Request the floor" button would be a promise the
 * system cannot keep, and a rail full of dead controls teaches people to stop
 * reading it.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useState } from "react";
import { AgentAvatar, useAgentAvatars } from "@/components/AgentAvatar";
import {
  addRoomAgent,
  peopleOf,
  removeRoomAgent,
  type PresenceEntry,
  type RoomEvent,
  type RoomState,
} from "@/lib/rooms";
import { PersonAvatar, RoleLabel } from "./Identity";

function relTime(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

/** A room event as one line of prose. Anything unrecognised renders nothing. */
function eventLine(evt: RoomEvent): string | null {
  const who = (e: string) => e.split("@")[0];
  switch (evt.type) {
    case "PARTICIPANT_JOINED":
      return `${who(evt.subject)} was added by ${who(evt.addedBy)}`;
    case "PARTICIPANT_LEFT":
      return evt.self
        ? `${who(evt.subject)} left`
        : `${who(evt.subject)} was removed by ${who(evt.removedBy)}`;
    case "PARTICIPANT_ROLE_CHANGED":
      return `${who(evt.subject)} is now a ${evt.role}`;
    case "AGENT_ADDED":
      return `${evt.agentName} joined the room`;
    case "AGENT_REMOVED":
      return `${evt.agentName} left the room`;
    case "ROOM_SETTINGS_CHANGED":
      return `${who(evt.changedBy)} changed ${Object.keys(evt.settings).join(", ")}`;
    case "USER_MESSAGE":
      return `${who(evt.author)} asked ${evt.agentName}`;
    default:
      return null;
  }
}

export function PresenceRail({
  room,
  presence,
  timeline,
  availableAgents,
  onChanged,
  onClose,
}: {
  room: RoomState;
  presence: PresenceEntry[];
  timeline: RoomEvent[];
  /** Agent names this member may run — the picker is filtered upstream. */
  availableAgents: string[];
  onChanged: () => void | Promise<void>;
  onClose?: () => void;
}) {
  const avatars = useAgentAvatars();
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const here = new Set(presence.map((p) => p.email));
  const people = peopleOf(room);
  const inRoom = new Set(room.agents.map((a) => a.agentName));
  const addable = availableAgents.filter((a) => !inRoom.has(a));

  const act = useCallback(
    async (key: string, fn: () => Promise<unknown>) => {
      setBusy(key);
      setError(null);
      try {
        await fn();
        await onChanged();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [onChanged]
  );

  return (
    <aside className="flex w-full flex-col gap-5 overflow-y-auto border-l border-border bg-sidebar px-3 py-4 text-sm lg:w-72 glass:border-l-0 glass:bg-transparent">
      {onClose && (
        <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} aria-label="Close room panel" className="self-end rounded-md lg:hidden">
          <Icon name="X" className="h-4 w-4" />
        </Button>
      )}

      {/* ── People ───────────────────────────────────────────────────────── */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
          In the room
          <span className="text-muted-foreground">
            · {here.size} of {people.length}
          </span>
        </h3>
        <ul className="space-y-1">
          {people.map((p) => {
            const isHere = here.has(p.subject);
            return (
              <li key={p.subject} className="flex items-center gap-2 px-1 py-1">
                <PersonAvatar
                  email={p.subject}
                  displayName={p.displayName}
                  avatarUrl={p.avatarUrl}
                  ring={isHere}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-foreground">
                    {p.displayName}
                    {p.subject === room.you.email && (
                      <span className="text-faint"> · you</span>
                    )}
                  </span>
                  <span className="block text-[11px]">
                    <RoleLabel role={p.role} />
                    {p.status !== "active" && (
                      <span className="text-warning"> · {p.status}</span>
                    )}
                  </span>
                </span>
                {isHere && (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-success"
                    title="Here now"
                  />
                )}
              </li>
            );
          })}
        </ul>
        {room.participants.some((p) => p.kind !== "person") && (
          <p className="mt-1.5 px-1 text-[11px] text-faint">
            Plus{" "}
            {room.participants
              .filter((p) => p.kind !== "person")
              .map((p) => p.displayName)
              .join(", ")}
            {" — everyone in there reads this room and caps what its agents can reach."}
          </p>
        )}
        {people.length > here.size && (
          <p className="mt-1.5 px-1 text-[11px] text-faint">
            {people.length - here.size} not here now. They replay the room when
            they open it.
          </p>
        )}
      </section>

      {/* ── Agents ───────────────────────────────────────────────────────── */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
          Agents here
          {room.you.canManage && addable.length > 0 && (
            <Button variant="ghost" size="none" radius="keep" layout="" onClick={() => setAdding((v) => !v)} title="Add an agent to this room" className="ml-auto rounded-md p-0.5">
              <Icon name="Plus" className="h-3.5 w-3.5" />
            </Button>
          )}
        </h3>

        <ul className="space-y-1">
          {room.agents.map((a) => (
            <li key={a.agentName} className="flex items-center gap-2 px-1 py-1">
              <AgentAvatar
                libraryId={avatars[a.agentName] ?? null}
                size={24}
                fallback={<Icon name="Bot" className="h-3.5 w-3.5 text-muted-foreground" />}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-foreground">
                  {a.agentName}
                </span>
                <span className="block text-[11px] text-muted-foreground">
                  {a.role === "primary"
                    ? "Answers by default"
                    : `Answers when you type @${a.agentName.replace(/^agent-/, "")}`}
                </span>
              </span>
              {room.you.canManage && a.role !== "primary" && (
                <button
                  disabled={busy !== null}
                  onClick={() =>
                    act(a.agentName, () =>
                      removeRoomAgent(room.sessionId, a.agentName)
                    )
                  }
                  className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-destructive"
                  title="Remove from this room"
                >
                  {busy === a.agentName ? (
                    <Icon name="Loader2" className="h-3 w-3 animate-spin" />
                  ) : (
                    <Icon name="Trash2" className="h-3 w-3" />
                  )}
                </button>
              )}
            </li>
          ))}
        </ul>

        {adding && (
          <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto rounded-lg border border-border bg-background p-1 glass:rounded-[10px] glass:border-transparent glass:bg-secondary">
            {addable.map((name) => (
              <li key={name}>
                <Button variant="ghost" size="none" radius="keep" layout="flex items-center" disabled={busy !== null} onClick={() =>
                    act(name, async () => {
                      await addRoomAgent(room.sessionId, name, "mentioned");
                      setAdding(false);
                    })
                  } className="w-full gap-2 rounded-md px-2 py-1.5 text-left text-[13px]">
                  <Icon name="UserPlus" className="h-3 w-3" />
                  {name}
                </Button>
              </li>
            ))}
          </ul>
        )}

        {error && <p className="mt-1.5 px-1 text-[11px] text-destructive">{error}</p>}

        {room.agents.length > 1 && (
          <p className="mt-1.5 px-1 text-[11px] text-faint">
            Start a message with @name to address one of them. Anything else goes
            to the default agent.
          </p>
        )}
      </section>

      {/* ── What just happened ───────────────────────────────────────────── */}
      {timeline.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-faint">
            Room activity
          </h3>
          <ul className="space-y-1.5">
            {timeline
              .slice(-8)
              .reverse()
              .map((evt, i) => {
                const line = eventLine(evt);
                if (!line) return null;
                return (
                  <li key={i} className="px-1 text-[11px] leading-snug">
                    <span className="text-muted-foreground">{line}</span>
                    {"ts" in evt && evt.ts ? (
                      <span className="text-faint"> · {relTime(evt.ts)}</span>
                    ) : null}
                  </li>
                );
              })}
          </ul>
        </section>
      )}

      {/* ── What is shared ───────────────────────────────────────────────── */}
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-faint">
          What is shared here
        </h3>
        <ul className="space-y-1.5 text-[11px]">
          <li className="flex items-center gap-2 px-1">
            <Icon name="Eye" className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">Transcript, tools, files</span>
            <span className="ml-auto text-foreground">Everyone here</span>
          </li>
          <li className="flex items-center gap-2 px-1">
            <Icon name="Lock" className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">Your personal memory</span>
            <span className="ml-auto text-foreground">Private</span>
          </li>
          <li className="flex items-center gap-2 px-1">
            <Icon name="KeyRound" className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">Credentials and keys</span>
            <span className="ml-auto text-foreground">Never</span>
          </li>
        </ul>
        <p className="mt-2 px-1 text-[11px] text-faint">
          {room.settings.historyVisibility === "since_join"
            ? "New people read from the moment they join."
            : "New people read the whole conversation."}
        </p>
      </section>
    </aside>
  );
}
