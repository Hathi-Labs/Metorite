"use client";

/**
 * Notifications on this device, on My Profile (WS-66 PN-1).
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §5.9 and §10.2
 * item 7, and `chat_run_continuity.md` §S7 item 3.
 *
 * One checkbox turns web push on for THIS browser or installed app. Every
 * decision is in `@/lib/push`.
 *
 * - ⚠️ **The server decides whether the section draws.** The mount asks
 *   `GET /api/push/public-key` once (`loadPushView`). A 404 (the gateway's
 *   `WEB_PUSH_ENABLED` is off) or any failure draws NOTHING, so a member for
 *   whom push is dark never sees it. There is no build-time flag.
 * - ⚠️ **The browser asks for permission only after a tap on the checkbox.**
 *   The mount reads state and never prompts: `enablePush` runs from the
 *   change handler and from nowhere else (fence: `push.test.ts`).
 * - In a Safari tab on iOS, there is no checkbox. The section shows the three
 *   steps to add Metorite to the Home Screen, because iOS sends push only to
 *   a Home Screen app.
 *
 * `NotificationsPanel` draws a view and holds no state, so a test renders it
 * with no DOM (`notificationsSection.test.ts`).
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { loadHideDetails, saveHideDetails } from "@/lib/hidePushDetails";
import {
  BLOCKED_MESSAGE,
  type PushView,
  disablePush,
  enablePush,
  loadPushView,
  sendTestPush,
} from "@/lib/push";
// The STRICT read: it throws on a failed read, and `push.ts` then keeps the
// browser subscription, so one failed read never ends another account's pushes.
import { otherAccountsStay } from "@/lib/accountSwitch";

export type Note = { ok: boolean; message: string } | null;

export function NotificationsSection() {
  const [view, setView] = useState<PushView>({ kind: "hidden" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  // WS-66 RE-9a. `null` until the read answers, and after a failed read.
  const [hideDetails, setHideDetails] = useState<boolean | null>(null);

  // Reads only (`loadPushView`): the server's answer first, then the support,
  // the permission, the browser's subscription and THIS member's row of it.
  // Nothing here can show a prompt or register the worker.
  const reload = useCallback(async () => {
    setView(await loadPushView().catch((): PushView => ({ kind: "hidden" })));
  }, []);

  useEffect(() => {
    void reload();
    void loadHideDetails().then(setHideDetails);
  }, [reload]);

  // The tap saves at once. The box shows what the server stored, and a failed
  // save leaves the old value with a note.
  async function onHideDetails(next: boolean) {
    setBusy(true);
    setNote(null);
    try {
      const stored = await saveHideDetails(next);
      if (stored === null) {
        setNote({ ok: false, message: "Could not save that. Try again." });
      } else {
        setHideDetails(stored);
      }
    } finally {
      setBusy(false);
    }
  }

  // The ONE path to the permission prompt: the member's tap.
  async function onToggle(next: boolean) {
    setBusy(true);
    setNote(null);
    try {
      if (next) {
        const res = await enablePush(otherAccountsStay);
        if (!res.ok) setNote({ ok: false, message: res.message });
      } else {
        await disablePush(otherAccountsStay).catch(() => undefined);
      }
      await reload();
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setNote(null);
    try {
      setNote(await sendTestPush());
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <NotificationsPanel
      view={view}
      busy={busy}
      note={note}
      onToggle={onToggle}
      onTest={onTest}
      hideDetails={hideDetails}
      onHideDetails={onHideDetails}
    />
  );
}

/** What the section draws for one view. No state, no fetch, no effect. */
export function NotificationsPanel({
  view,
  busy,
  note,
  onToggle,
  onTest,
  hideDetails = null,
  onHideDetails,
}: {
  view: PushView;
  busy: boolean;
  note: Note;
  onToggle: (next: boolean) => Promise<void> | void;
  onTest: () => Promise<void> | void;
  /** The `hide_push_details` pref. `null` draws no toggle (not read yet). */
  hideDetails?: boolean | null;
  onHideDetails?: (next: boolean) => Promise<void> | void;
}) {
  if (view.kind === "hidden" || view.kind === "loading") return null;

  return (
    <section className="rounded-xl border border-border p-3" aria-labelledby="notifications-heading">
      <div id="notifications-heading" className="flex items-center gap-2 text-xs font-medium text-foreground">
        <Icon name="BellRing" className="h-4 w-4" />
        Notifications on this device
      </div>

      {view.kind === "ios-tab" && (
        <div className="mt-1 space-y-1 text-[11px] text-muted-foreground">
          <p>On iPhone and iPad, notifications come only to Metorite on your Home Screen.</p>
          <ol className="list-decimal space-y-0.5 pl-4">
            <li>Tap the Share button in the browser bar.</li>
            <li>Tap Add to Home Screen, then tap Add.</li>
            <li>Open Metorite from your Home Screen, and turn notifications on here.</li>
          </ol>
        </div>
      )}

      {view.kind === "unsupported" && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          This browser cannot show notifications from Metorite.
        </p>
      )}

      {view.kind === "blocked" && (
        <p className="mt-1 text-[11px] text-muted-foreground">{BLOCKED_MESSAGE}</p>
      )}

      {view.kind === "ready" && (
        <>
          <label className="mt-2 flex items-start gap-2.5">
            <Checkbox
              checked={view.on}
              disabled={busy}
              onChange={(e) => void onToggle(e.target.checked)}
              aria-label="Show Metorite notifications on this device"
            />
            <span className="space-y-0.5">
              <span className="block text-xs font-medium text-foreground">
                Show Metorite notifications on this device
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Your browser asks you to allow them when you turn this on. Signing out turns
                them off for this device.
              </span>
            </span>
          </label>
          {hideDetails !== null && onHideDetails && (
            <label className="mt-3 flex items-start gap-2.5">
              <Checkbox
                checked={hideDetails}
                disabled={busy}
                onChange={(e) => void onHideDetails(e.target.checked)}
                aria-label="Hide details on the lock screen"
              />
              <span className="space-y-0.5">
                <span className="block text-xs font-medium text-foreground">
                  Hide details on the lock screen
                </span>
                <span className="block text-[11px] text-muted-foreground">
                  A reminder shows only your organization name. It does not show the
                  title or the time. This applies to all your devices.
                </span>
              </span>
            </label>
          )}
          {view.on && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button size="sm" variant="secondary" icon="Send" loading={busy} onClick={() => void onTest()}>
                Send a test
              </Button>
            </div>
          )}
        </>
      )}

      {note && (
        <p
          className={`mt-2 text-[11px] ${note.ok ? "text-muted-foreground" : "text-destructive"}`}
          role={note.ok ? "status" : "alert"}
        >
          {note.message}
        </p>
      )}
    </section>
  );
}
