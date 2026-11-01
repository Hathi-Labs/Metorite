"use client";

/**
 * Going shared — the sheet that turns a private thread into a room.
 *
 * The common case is not "start a shared session". It is realising, forty turns
 * deep, that this shouldn't be yours alone. So this sheet makes three entangled
 * decisions in one place, because deciding them apart is how people share more
 * than they meant to:
 *
 *   **who** — a colleague, a group, or the whole org
 *   **how much history** they get — from here on, or everything
 *   **what it costs the room** — the capability cap, stated before and after
 *
 * The third is the one nobody expects. A shared run acts at the intersection of
 * every participant's access, so adding somebody can take an integration away
 * from an agent that had it a minute ago. This sheet says so in words, in the
 * same breath as the invite, rather than leaving it to be discovered as a
 * mysterious failure three turns later.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addParticipant,
  capabilityLabel,
  fetchDirectory,
  removeParticipant,
  setParticipantRole,
  updateRoomSettings,
  type DirectoryEntry,
  type RoomRole,
  type RoomState,
} from "@/lib/rooms";
import { PersonAvatar } from "./Identity";

const ROLE_COPY: { value: RoomRole; label: string; blurb: string }[] = [
  { value: "member", label: "Contributor", blurb: "Can send turns and stop runs" },
  { value: "viewer", label: "Watcher", blurb: "Reads the room, cannot send" },
  { value: "owner", label: "Owner", blurb: "Can invite, change settings, delete" },
];

export function ShareSheet({
  room,
  onClose,
  onChanged,
}: {
  room: RoomState;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [directory, setDirectory] = useState<DirectoryEntry[]>([]);
  const [role, setRole] = useState<RoomRole>("member");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      void fetchDirectory(query)
        .then((d) => live && setDirectory(d))
        .catch(() => live && setDirectory([]));
    }, 180);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query]);

  const alreadyIn = useMemo(
    () => new Set(room.participants.map((p) => p.subject)),
    [room.participants]
  );

  const candidates = useMemo(
    () =>
      directory
        .filter((d) => !alreadyIn.has(d.subject) && d.subject !== room.you.email)
        .slice(0, 8),
    [directory, alreadyIn, room.you.email]
  );

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

  const cappedCount = room.cap.capped.length;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <div
        className="tech-glass my-auto w-full max-w-lg rounded-xl border border-border bg-card shadow-2xl glass:rounded-2xl glass:border-transparent glass:glass-menu-flat"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-border px-5 py-4 glass:border-transparent">
          <Icon name="Users" className="h-4 w-4 text-primary" />
          <div className="flex-1">
            <h2 className="text-sm font-semibold text-foreground">
              Share this conversation
            </h2>
            <p className="text-xs text-muted-foreground">
              {room.title || "Untitled"} · everyone you add reads the transcript
            </p>
          </div>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </header>

        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">
          {/* ── Who ──────────────────────────────────────────────────────── */}
          <div className="relative">
            <Icon name="Search" className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people and groups…"
              className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-faint focus:border-primary focus:outline-none glass:h-8 glass:rounded-full glass:border-input glass:bg-field glass:py-0 glass:text-label"
            />
          </div>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {ROLE_COPY.map((r) => (
              <button
                key={r.value}
                onClick={() => setRole(r.value)}
                title={r.blurb}
                className={[
                  "rounded-full border px-2.5 py-1 text-xs tech-transition glass:border-transparent glass:px-3 glass:text-footnote",
                  role === r.value
                    ? "border-primary bg-primary/10 text-primary glass:bg-primary/12"
                    : "border-border text-muted-foreground hover:text-foreground glass:bg-secondary",
                ].join(" ")}
              >
                {r.label}
              </button>
            ))}
            <span className="ml-auto self-center text-[11px] text-faint">
              added as {ROLE_COPY.find((r) => r.value === role)?.blurb.toLowerCase()}
            </span>
          </div>

          {candidates.length > 0 && (
            <ul className="mt-3 space-y-0.5">
              {candidates.map((d) => (
                <li key={d.subject}>
                  <button
                    disabled={busy !== null}
                    onClick={() =>
                      act(d.subject, () =>
                        addParticipant(room.sessionId, d.subject, role)
                      )
                    }
                    className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-secondary disabled:opacity-50 glass:rounded-[10px]"
                  >
                    {d.kind === "group" ? (
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-secondary text-muted-foreground">
                        <Icon name="Users" className="h-3 w-3" />
                      </span>
                    ) : (
                      <PersonAvatar
                        email={d.subject}
                        displayName={d.displayName}
                        avatarUrl={d.avatarUrl}
                      />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-foreground">
                        {d.displayName}
                      </span>
                      <span className="block truncate text-[11px] text-faint">
                        {d.kind === "group"
                          ? `${d.memberCount ?? 0} members · everyone in the group caps the room`
                          : d.subject}
                      </span>
                    </span>
                    {busy === d.subject && (
                      <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}

          {error && (
            <p className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive glass:rounded-[10px] glass:border-transparent">
              {error}
            </p>
          )}

          {/* ── Who is already here ──────────────────────────────────────── */}
          <h3 className="mt-5 mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
            In this room
          </h3>
          <ul className="space-y-0.5">
            {room.participants.map((p) => {
              const isYou = p.subject === room.you.email;
              return (
                <li
                  key={p.subject}
                  className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 glass:rounded-[10px]"
                >
                  {p.kind === "person" ? (
                    <PersonAvatar
                      email={p.subject}
                      displayName={p.displayName}
                      avatarUrl={p.avatarUrl}
                    />
                  ) : (
                    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-secondary text-muted-foreground">
                      {p.kind === "org" ? (
                        <Icon name="Globe" className="h-3 w-3" />
                      ) : (
                        <Icon name="Users" className="h-3 w-3" />
                      )}
                    </span>
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-foreground">
                      {p.displayName}
                      {isYou && <span className="text-faint"> · you</span>}
                    </span>
                    {p.status !== "active" && (
                      <span className="block text-[11px] text-warning">
                        {p.status} — this caps the whole room until they are removed
                      </span>
                    )}
                  </span>

                  {room.you.canInvite ? (
                    <select
                      value={p.role}
                      disabled={busy !== null}
                      onChange={(e) =>
                        act(p.subject, () =>
                          setParticipantRole(
                            room.sessionId,
                            p.subject,
                            e.target.value as RoomRole
                          )
                        )
                      }
                      className="rounded-md border border-border bg-background px-1.5 py-0.5 text-xs text-muted-foreground focus:outline-none glass:rounded-full glass:border-transparent glass:bg-secondary glass:px-2.5 glass:text-footnote"
                    >
                      {ROLE_COPY.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {ROLE_COPY.find((r) => r.value === p.role)?.label ?? p.role}
                    </span>
                  )}

                  {(room.you.canInvite || isYou) && (
                    <button
                      disabled={busy !== null}
                      onClick={() =>
                        act(`rm-${p.subject}`, () =>
                          removeParticipant(room.sessionId, p.subject)
                        )
                      }
                      title={isYou ? "Leave this room" : "Remove"}
                      className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-destructive"
                    >
                      <Icon name="X" className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>

          {/* ── How much history ─────────────────────────────────────────── */}
          {room.you.canManage && (
            <>
              <h3 className="mt-5 mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
                What new people can read
              </h3>
              <div className="flex gap-1.5">
                {(
                  [
                    ["since_join", "From when they joined"],
                    ["full", "The whole conversation"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    disabled={busy !== null}
                    onClick={() =>
                      act("history", () =>
                        updateRoomSettings(room.sessionId, {
                          history_visibility: value,
                        })
                      )
                    }
                    className={[
                      "flex-1 rounded-lg border px-3 py-2 text-left text-xs tech-transition glass:rounded-[10px] glass:border-transparent glass:text-footnote",
                      room.settings.historyVisibility === value
                        ? "border-primary bg-primary/10 text-foreground glass:bg-primary/12"
                        : "border-border text-muted-foreground hover:text-foreground glass:bg-secondary",
                    ].join(" ")}
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      {room.settings.historyVisibility === value && (
                        <Icon name="Check" className="h-3 w-3 text-primary" />
                      )}
                      {label}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          {/* ── What it costs the room ───────────────────────────────────── */}
          <h3 className="mt-5 mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
            What the agents can reach here
          </h3>
          <div className="rounded-lg border border-border bg-background/60 p-3 glass:rounded-[10px] glass:border-transparent glass:bg-secondary">
            {room.cap.degraded ? (
              <p className="flex items-start gap-2 text-xs text-muted-foreground">
                <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                Could not work out what this room can reach right now. Treat the
                list as unknown rather than as unrestricted.
              </p>
            ) : cappedCount === 0 ? (
              <p className="flex items-start gap-2 text-xs text-muted-foreground">
                <Icon name="Shield" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
                Everyone here holds the same access, so the agents lose nothing
                by this conversation being shared.
              </p>
            ) : (
              <>
                <p className="flex items-start gap-2 text-xs text-foreground">
                  <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                  A shared run acts with what <em>everyone</em> here holds, so
                  these are switched off in this conversation:
                </p>
                <ul className="mt-2 space-y-1">
                  {room.cap.capped.map((c) => (
                    <li key={c.capability} className="text-xs text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {capabilityLabel(c.capability)}
                      </span>{" "}
                      — {c.missingFor.map((m) => m.split("@")[0]).join(", ")}{" "}
                      {c.missingFor.length === 1 ? "does not" : "do not"} have it
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-faint">
                  An admin can grant them access, or you can remove them from the
                  room. Both are visible acts — nothing here changes quietly.
                </p>
              </>
            )}
            {room.cap.inactive.length > 0 && (
              <p className="mt-2 text-xs text-destructive">
                {room.cap.inactive.join(", ")} — suspended. While they are in the
                room the agents can reach nothing at all.
              </p>
            )}
          </div>

          <p className="mt-4 flex items-start gap-2 text-[11px] text-faint">
            <Icon name="Shield" className="mt-0.5 h-3 w-3 shrink-0" />
            Your private memory is never used in a shared conversation, and no
            credential or key is ever visible to anyone here.
          </p>
        </div>
      </div>
    </div>
  );
}
