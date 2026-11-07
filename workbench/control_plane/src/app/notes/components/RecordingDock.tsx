"use client";

/**
 * RecordingDock — the "app follows you" pill for an in-progress meeting
 * recording (spec §5.2). Mounted ONCE in AppShell (sibling of the router
 * outlet), it shows whenever a recording is live and you're NOT on its session
 * screen — so navigation itself is the minimize/expand. Tap it to return to the
 * studio; stop/pause without leaving the page you're on.
 *
 * Mirrors the tasks Focus-timer dock. On mobile it sits flush above the fixed
 * bottom nav (reusing the pb-nav / safe-area idiom) and stacks ABOVE the Focus
 * pill when both are minimized, so neither is clipped by the menu bar.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { usePathname, useRouter } from "next/navigation";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import { formatClock } from "../lib/api";
import { isActive, useRecordingStore } from "../lib/recordingStore";

export function RecordingDock() {
  const router = useRouter();
  const pathname = usePathname();
  const meetingId = useRecordingStore((s) => s.meetingId);
  const title = useRecordingStore((s) => s.title);
  const phase = useRecordingStore((s) => s.phase);
  const elapsed = useRecordingStore((s) => s.elapsed);
  const pause = useRecordingStore((s) => s.pause);
  const resume = useRecordingStore((s) => s.resume);
  const stop = useRecordingStore((s) => s.stop);

  // Stack above the Focus-timer pill when it's also showing, so the two docks
  // and the mobile menu bar never overlap.
  const focusPillShown = useTaskStore(
    (s) => !!s.focusSessionId && s.focusMinimized
  );

  const onSessionScreen = pathname === `/notes/session/${meetingId}`;
  if (!meetingId || !isActive(phase) || onSessionScreen) return null;

  const finalizing = phase === "finalizing";
  const paused = phase === "paused";

  async function onStop() {
    const mid = await stop();
    if (mid) router.push(`/notes/meeting/${mid}`);
  }

  // Offsets: base = clear the fixed mobile nav (3.5rem content + safe inset);
  // when the Focus pill is up, stack a row higher. Desktop floats bottom-right.
  const bottomClass = focusPillShown
    ? "bottom-[calc(7rem+env(safe-area-inset-bottom))] sm:bottom-[5.5rem]"
    : "bottom-[var(--phone-bar-h)] sm:bottom-4";

  return (
    <div
      className={`chat-fade-in fixed inset-x-0 z-[60] sm:inset-x-auto sm:right-4 sm:w-80 ${bottomClass}`}
    >
      <div className="flex items-center gap-2.5 border-t border-border bg-card/95 px-3 py-2 shadow-2xl backdrop-blur sm:rounded-xl sm:border">
        <button
          onClick={() => router.push(`/notes/session/${meetingId}`)}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
          title="Back to the recording"
        >
          {finalizing ? (
            <Icon name="Loader2" className="w-3.5 h-3.5 shrink-0 animate-spin text-primary" />
          ) : (
            <span
              className={`w-2.5 h-2.5 shrink-0 rounded-full ${
                paused ? "bg-warning" : "bg-destructive animate-pulse"
              }`}
            />
          )}
          <span className="font-mono text-sm font-semibold tabular-nums text-foreground">
            {formatClock(elapsed)}
          </span>
          <span className="flex min-w-0 items-center gap-1 truncate text-xs text-muted-foreground">
            <Icon name="Mic" className="w-3 h-3 shrink-0" />
            <span className="truncate">
              {finalizing
                ? "Finishing…"
                : paused
                  ? "Paused"
                  : title || "Recording"}
            </span>
          </span>
        </button>

        {!finalizing && (
          <>
            <Button variant="ghost" size="icon-sm" layout="" onClick={() => (paused ? resume() : pause())} aria-label={paused ? "Resume" : "Pause"}>
              {paused ? (
                <Icon name="Play" className="w-4 h-4" />
              ) : (
                <Icon name="Pause" className="w-4 h-4" />
              )}
            </Button>
            <button
              onClick={() => void onStop()}
              className="rounded-lg bg-destructive/15 p-1.5 text-destructive hover:bg-destructive/25 tech-transition"
              aria-label="Stop and transcribe"
            >
              <Icon name="Square" className="w-4 h-4 fill-current" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}
