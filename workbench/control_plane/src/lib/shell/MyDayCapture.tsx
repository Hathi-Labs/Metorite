"use client";

/**
 * The capture box of My Day (CC-1, `my_day_command_centre.md`, task 1968).
 *
 * The box sits under the greeting. It holds no flow of its own. It is the
 * door to the ONE capture flow of the task store, which Quick Capture also
 * uses (`QuickCapture.tsx`, mounted here for the split's review):
 *
 *  - Enter files the text to the Inbox through the store's `captureLine`.
 *    Every capture lands as INBOX (D170). The box clears, and a toast says so.
 *  - Shift+Enter, or the "AI split" button, opens Quick Capture on the AI
 *    split of the text. The member confirms the items there, and they file
 *    through the store's `captureMany`.
 *  - A line that names a clock time for today ("call the bank at 3 pm") also
 *    sets a REAL reminder at that time, through `createReminder` as Quick
 *    Capture's "Remind me" does (`captureTime.ts`). A time block would need a
 *    task with a schedule, and the Inbox holds captures only. The toast offers
 *    Undo, and Undo removes the Inbox item and drops the reminder.
 *  - "Inbox N · Clarify" opens My task inbox in Projects, where the clarify
 *    pass runs. The count is the summary read of Projects (`my/summary`),
 *    on the same cache key.
 *
 * The task store is not loaded on My Day (a load reads every task of the
 * member). So the first act loads it, once, as `completeFromHome` does.
 */
import { useEffect, useRef, useState } from "react";

import { QuickCapture } from "@/app/tasks/components/QuickCapture";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import { browserTimeZone } from "@/app/tasks/lib/utils";
import { projectsApi, projectsKey, type NodeSummary } from "@/app/projects/lib/api";
import { myWorkEnabled } from "@/app/projects/lib/myWorkFlag";
import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { createReminder, createdWords, REMINDER_TITLE_MAX } from "@/lib/reminderWrite";
import { gridZone, instantIso } from "@/lib/reminderClock";
import { useCachedResource } from "@/lib/useCachedResource";

import { captureToast, namedTimeToday, skippedTimeReason, type NamedTime } from "./captureTime";
import { runReminderAct } from "./reminderOpen";

const TOAST_KEY = "my-day-capture";
const LOAD_FAILED = "Metorite could not load your Inbox. Try again.";

let loading: Promise<void> | null = null;
/** Load the task store once. A demo store (no gateway) writes nothing, so it fails loudly. */
async function ensureLive(): Promise<void> {
  if (useTaskStore.getState().backend !== "live") {
    loading ??= useTaskStore
      .getState()
      .hydrate()
      .finally(() => {
        loading = null;
      });
    await loading;
  }
  if (useTaskStore.getState().backend !== "live") throw new Error(LOAD_FAILED);
}

/** A capture still in flight holds a local id. Undo waits for the server's id. */
async function settledCaptureIds(): Promise<string[]> {
  for (let i = 0; i < 30; i++) {
    const ids = useTaskStore.getState().lastCaptureIds;
    if (ids.length === 0 || !ids.some((id) => id.startsWith("local-"))) return ids;
    await new Promise((r) => setTimeout(r, 100));
  }
  return useTaskStore.getState().lastCaptureIds;
}

export default function MyDayCapture({ className = "" }: { className?: string }) {
  const { access, loading: accessLoading } = useAccess();
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const myWork = myWorkEnabled();
  const allowed = !accessLoading && (access.is_admin || access.features.includes("projects"));
  // Same cache key as the Projects page and My tasks: one request.
  const summary = useCachedResource<NodeSummary>(allowed ? projectsKey("my/summary") : null, () =>
    projectsApi.mySummary(),
  );
  const inbox = summary.data?.by_disposition?.inbox;
  const refreshCount = summary.refresh;

  // The AI split files from inside Quick Capture. When its box closes with new
  // captures, this box clears and says so.
  const splitting = useRef<{ text: string; before: string[] } | null>(null);
  const undoRef = useRef<() => Promise<void>>(async () => {});

  async function undoCapture(reminderId?: string) {
    const ids = await settledCaptureIds();
    if (ids.length) useTaskStore.getState().undoLastCapture();
    let words = "";
    if (reminderId) {
      const got = await runReminderAct(reminderId, "drop");
      if (!got.ok) words = got.words;
    }
    refreshCount();
    if (words) toast.show({ key: TOAST_KEY, variant: "error", title: "The reminder stays", description: words });
    else toast.show({ key: TOAST_KEY, title: "Undone" });
  }
  useEffect(() => {
    undoRef.current = () => undoCapture();
  });

  useEffect(
    () =>
      useTaskStore.subscribe((s) => {
        const open = splitting.current;
        if (!open || s.quickCaptureOpen) return;
        splitting.current = null;
        const made = s.lastCaptureIds.filter((id) => !open.before.includes(id));
        if (!made.length) return;
        setValue((v) => (v.trim() === open.text ? "" : v));
        refreshCount();
        toast.show({
          key: TOAST_KEY,
          title: `Filed ${made.length} ${made.length === 1 ? "item" : "items"} to your Inbox`,
          action: { label: "Undo", onClick: () => void undoRef.current() },
          timeout: 8000,
        });
      }),
    [refreshCount, toast],
  );

  if (!allowed) return null;

  async function file() {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      await ensureLive();
      const store = useTaskStore.getState();
      store.captureLine(text, { targets: [] });
      const now = new Date();
      const named: NamedTime | null = text.length <= REMINDER_TITLE_MAX ? namedTimeToday(text, now) : null;
      let reminderId: string | undefined;
      let reminderFailed: string | undefined;
      // A line that looks timed but got no reminder says so in the toast.
      const reminderSkipped = named ? undefined : (skippedTimeReason(text) ?? undefined);
      if (named) {
        const zone = gridZone(browserTimeZone());
        const got = await createReminder({ title: text, kind: "action", fireAt: instantIso(named.ms, zone), tz: zone });
        if (!got.ok) reminderFailed = got.words;
        else if (got.already) reminderFailed = createdWords(got, { ms: named.ms }, zone);
        else reminderId = got.id;
      }
      setValue("");
      refreshCount();
      const words = captureToast({ reminder: reminderId ? named! : undefined, reminderFailed, reminderSkipped });
      toast.show({
        key: TOAST_KEY,
        ...words,
        action: { label: "Undo", onClick: () => void undoCapture(reminderId) },
        timeout: 8000,
      });
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Metorite could not file it. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function split() {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      await ensureLive();
      splitting.current = { text, before: useTaskStore.getState().lastCaptureIds };
      useTaskStore.getState().openQuickCapture("sweep", text, false, true);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : LOAD_FAILED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={className} data-testid="my-day-capture">
      <div className="flex flex-wrap items-center gap-2">
        {/* A phone gives the field its own row. From sm it shares the row. */}
        <div className="w-full sm:min-w-48 sm:flex-1 sm:basis-0">
          <Input
            icon="Plus"
            inputSize="lg"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onFocus={() => void ensureLive().catch(() => {})}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              e.preventDefault();
              if (e.shiftKey) void split();
              else void file();
            }}
            placeholder="Capture a task, a note or a reminder…"
            aria-label="Capture to my Inbox"
            aria-describedby="my-day-capture-hint"
            enterKeyHint="send"
            className="w-full"
          />
        </div>
        <Button
          variant="ghost"
          size="md"
          icon="Sparkles"
          onClick={() => void split()}
          disabled={!value.trim() || busy}
          aria-label="Split with AI"
        >
          AI split
        </Button>
        <Button
          variant="secondary"
          size="md"
          icon="Inbox"
          href={myWork ? "/projects?app=my-inbox" : "/tasks"}
          data-testid="my-day-clarify"
        >
          {typeof inbox === "number" ? `Inbox ${inbox} · Clarify` : "Inbox · Clarify"}
        </Button>
      </div>
      <p id="my-day-capture-hint" className="mt-1.5 hidden px-1 text-[11px] text-muted-foreground sm:block">
        ↵ files it to your Inbox · ⇧↵ splits it with AI · a time such as “at 3 pm” also sets a reminder
      </p>
      {error ? (
        <p role="alert" className="mt-1.5 px-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <QuickCapture />
    </div>
  );
}
