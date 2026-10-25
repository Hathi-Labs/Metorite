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
 * - ⚠️ **The browser asks for permission only after a tap on the checkbox.**
 *   The mount reads state and never prompts: `enablePush` runs from the
 *   change handler and from nowhere else (fence: `push.test.ts`).
 * - In a Safari tab on iOS, there is no checkbox. The section shows the three
 *   steps to add Metorite to the Home Screen, because iOS sends push only to
 *   a Home Screen app.
 * - It draws NOTHING while `NEXT_PUBLIC_WEB_PUSH` is off or the gateway's
 *   `/push` answers 404, so a member for whom push is dark never sees it.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  BLOCKED_MESSAGE,
  type PushView,
  currentSubscription,
  disablePush,
  enablePush,
  fetchPublicKey,
  permissionState,
  pushSupport,
  pushView,
  registeredHere,
  sendTestPush,
  webPushOn,
} from "@/lib/push";
import { fetchAccounts } from "@/lib/accountSwitch";

type Note = { ok: boolean; message: string } | null;

/** True when another account of the switcher stays on this browser, so the
 * toggle keeps the browser subscription for it. */
async function otherAccountsStay(): Promise<boolean> {
  return (await fetchAccounts(false)).others.length > 0;
}

export function NotificationsSection() {
  const [view, setView] = useState<PushView>({ kind: "hidden" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  // Reads only: the key answer, the support, the permission, the browser's
  // subscription and THIS member's row of it. Nothing here can show a prompt.
  // One browser subscription serves every account of the switcher, so the
  // browser alone does not say whether this member gets the pushes.
  const reload = useCallback(async () => {
    if (!webPushOn()) {
      setView({ kind: "hidden" });
      return;
    }
    const key = await fetchPublicKey();
    const sub = await currentSubscription().catch(() => null);
    const registered = sub ? await registeredHere(sub.endpoint) : false;
    setView(
      pushView({
        flag: true,
        support: pushSupport(),
        key,
        permission: permissionState(),
        subscribed: sub !== null,
        registered,
      }),
    );
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (view.kind === "hidden" || view.kind === "loading") return null;

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

      {view.kind === "not-configured" && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          Notifications are not set up on this server yet.
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
