"use client";

/**
 * LiveDock — "● Live now" presence for meetings being captured SERVER-side
 * (spec: live_meeting_copilot.md §7). Mounted once in AppShell, it tells you a
 * call is being transcribed right now and takes you to the copilot console.
 *
 * Deliberately complements RecordingDock rather than duplicating it: that dock
 * is driven by the *local* recorder store, so it only knows about a recording
 * started in this tab. This one reads the server's live-session registry, which
 * is what covers the cases the local store can't see — a headless notetaker bot
 * in a Meet call, or a recording you started on another device. Sessions already
 * shown by RecordingDock are filtered out so the two never stack up.
 */

import Icon from "@/components/Icon";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { listLiveSessions } from "../lib/api";
import { useRecordingStore } from "../lib/recordingStore";
import type { LiveSession } from "../lib/types";

export function LiveDock() {
  const router = useRouter();
  const pathname = usePathname();
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const localMeetingId = useRecordingStore((s) => s.meetingId);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const rows = await listLiveSessions();
        if (alive) setSessions(rows);
      } catch {
        // Presence is additive — a failed poll just means no pill this tick.
        if (alive) setSessions([]);
      }
    }
    void poll();
    const t = setInterval(poll, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // Don't shadow the local recorder's own dock, and don't nag while you're
  // already looking at the session (its console or its recording screen).
  // ⚠️ THIS DOCK SITS IN `AppShell`, ABOVE EVERY LAYOUT BOUNDARY. A body that
  // is not an array threw here and blanked EVERY page in the product —
  // Projects, Tasks, Calendar — none of which have anything to do with meeting
  // notes. Measured 2026-09-03. A widget must not be able to take the shell.
  const candidates = (Array.isArray(sessions) ? sessions : []).filter(
    (s) =>
      s.meeting_id !== localMeetingId &&
      pathname !== `/notes/live/${s.meeting_id}` &&
      pathname !== `/notes/session/${s.meeting_id}`
  );
  if (candidates.length === 0) return null;

  const first = candidates[0];
  const extra = candidates.length - 1;

  return (
    <div className="chat-fade-in fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-[55] sm:inset-x-auto sm:bottom-4 sm:left-4 sm:w-72">
      <button
        onClick={() => router.push(`/notes/live/${first.meeting_id}`)}
        className="flex w-full items-center gap-2.5 border-t border-border bg-card/95 px-3 py-2 text-left shadow-2xl backdrop-blur hover:bg-accent sm:rounded-xl sm:border"
      >
        <span className="relative flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-70" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">
            {first.title ?? "Untitled meeting"}
          </span>
          <span className="block text-xs text-muted-foreground">
            Live now{extra > 0 ? ` · +${extra} more` : ""}
          </span>
        </span>
        {first.source === "bot" ? (
          <Icon name="Bot" className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <Icon name="Radio" className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>
    </div>
  );
}
