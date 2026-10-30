"use client";

/**
 * PushPromptSheet — the chat asks a phone member to turn on notifications
 * (WS-OB OB-8, `calendar_events_reminders.md` §10.4).
 *
 * One inline sheet, docked above the composer. It is not a modal: it never
 * covers the messages and never blocks the composer. `PushPromptPanel` draws
 * it and holds no hook, so a test calls it as a function. `PushPromptSheet`
 * gathers the facts, asks `shouldShowPushPrompt` (`lib/pushPrompt.ts`, the
 * ONE show rule), and holds the state.
 *
 * The flag is the server's answer: `GET /push/public-key` says `prompt`.
 * With it off, this component draws nothing and writes nothing.
 *
 * ⚠️ **Turn on calls `enablePush` as the FIRST thing the tap does.** A browser
 * refuses a permission request that no tap started, and any `await` before the
 * call can end the tap's standing (`lib/push.ts` module note).
 *
 * Fence: `PushPromptSheet.test.ts` and `lib/pushPrompt.test.ts`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { otherAccountsStay } from "@/lib/accountSwitch";
import {
  enablePush,
  fetchPublicKey,
  isPhoneNow,
  permissionState,
  pushSupport,
  registeredHere,
  thisDeviceEndpoint,
} from "@/lib/push";
import {
  PROMPT_BODY,
  PROMPT_TITLE,
  SETTINGS_PATH,
  mergePromptState,
  promptStateOf,
  shouldShowPushPrompt,
  snoozeUntil,
  type PromptKind,
  type PromptState,
} from "@/lib/pushPrompt";
import { loadFeedPrefs, saveFeedPref } from "@/lib/shell/feed";

/** How long "Notifications are on" stays before the sheet closes. */
export const ON_SHOWN_MS = 2500;

export type SheetKind = Exclude<PromptKind, "none">;
export type SheetPhase = "ask" | "working" | "on";

export interface PushPromptPanelProps {
  kind: SheetKind;
  phase: SheetPhase;
  /** A line under the body: why Turn on did not finish. */
  note?: string | null;
  onTurnOn: () => void;
  onLater: () => void;
  onNever: () => void;
  onClose: () => void;
}

/** What a member does in the phone's settings after a refusal. */
export const DENIED_STEPS: readonly string[] = [
  "Open your phone's Settings.",
  "Find your browser or the Metorite app, and open Notifications.",
  "Allow notifications, then come back here.",
];

export function PushPromptPanel({ kind, phase, note, onTurnOn, onLater, onNever, onClose }: PushPromptPanelProps) {
  return (
    <section
      role="region"
      aria-label={PROMPT_TITLE}
      data-push-prompt={kind}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="mb-2 rounded-2xl border border-border bg-secondary/30 px-3 py-3 text-xs"
    >
      {phase === "on" ? (
        <p role="status" className="flex items-center gap-1.5 font-medium text-foreground">
          <Icon name="Check" size={14} className="shrink-0 text-success" />
          Notifications are on
        </p>
      ) : (
        <>
          <div className="flex items-start gap-2">
            <Icon name="BellRing" size={14} className="mt-0.5 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">{PROMPT_TITLE}</p>
              <p className="mt-0.5 text-muted-foreground">{PROMPT_BODY}</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              icon="X"
              onClick={onClose}
              aria-label="Close"
              title="Close"
              className="shrink-0"
            />
          </div>

          {kind === "ios-tab" && (
            <ol className="mt-2 list-decimal space-y-1 pl-9 text-muted-foreground">
              <li>
                Tap Share <Icon name="Share" size={12} className="inline align-text-bottom" aria-label="Share" role="img" /> in
                the Safari toolbar.
              </li>
              <li>Choose Add to Home Screen.</li>
              <li>Open Metorite from your Home Screen, and turn on notifications there.</li>
            </ol>
          )}

          {kind === "denied" && (
            <>
              <p className="mt-2 pl-6 text-muted-foreground">
                This phone blocks notifications from Metorite. To turn them on:
              </p>
              <ol className="mt-1 list-decimal space-y-1 pl-9 text-muted-foreground">
                {DENIED_STEPS.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
            </>
          )}

          {note && (
            <p role="alert" className="mt-2 pl-6 text-destructive">
              {note}
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2 pl-6">
            {kind === "turn-on" && (
              <Button type="button" size="sm" onClick={onTurnOn} loading={phase === "working"}>
                Turn on
              </Button>
            )}
            {kind === "denied" ? (
              <Button type="button" size="sm" variant="secondary" onClick={onClose}>
                Got it
              </Button>
            ) : (
              <>
                <Button type="button" size="sm" variant="secondary" onClick={onLater} disabled={phase === "working"}>
                  Remind me later
                </Button>
                <Button type="button" size="sm" variant="text" onClick={onNever} disabled={phase === "working"}>
                  Don&apos;t ask again
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export interface PushPromptSheetProps {
  /** The member's own assistant: `isOrchestrator && !isRoom`. */
  ownChat: boolean;
  messageCount: number;
  /** A run streams now. */
  streaming: boolean;
}

export default function PushPromptSheet({ ownChat, messageCount, streaming }: PushPromptSheetProps) {
  const toast = useToast();
  const [kind, setKind] = useState<PromptKind>("none");
  const [phase, setPhase] = useState<SheetPhase>("ask");
  const [note, setNote] = useState<string | null>(null);
  // The check ran to its end. Without it, a re-render would check again.
  const decided = useRef(false);
  const stored = useRef<PromptState>({});
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  /** Read, merge, then write: a PUT replaces the whole value of the key. */
  const write = useCallback(async (patch: PromptState) => {
    const prefs = await loadFeedPrefs();
    const base = prefs ? promptStateOf(prefs) : stored.current;
    const next = mergePromptState(base, patch);
    const saved = await saveFeedPref("push_prompt", next);
    stored.current = saved ? promptStateOf(saved) : next;
  }, []);

  const hasMessages = messageCount > 0;
  useEffect(() => {
    if (!ownChat || !hasMessages || streaming || decided.current) return;
    let cancelled = false;
    void (async () => {
      // The cheap local reads come first, so a desktop makes no request.
      const support = pushSupport();
      if (!isPhoneNow() || support === "unsupported") {
        decided.current = true;
        return;
      }
      const key = await fetchPublicKey();
      if (cancelled) return;
      if (key.kind !== "key" || !key.prompt) {
        decided.current = true;
        return;
      }
      const prefs = await loadFeedPrefs();
      // A failed read draws no guess. The next mount tries again.
      if (cancelled || !prefs) return;
      const state = promptStateOf(prefs);
      const permission = permissionState();
      let registered = false;
      if (support === "supported" && permission === "granted") {
        const endpoint = await thisDeviceEndpoint();
        registered = endpoint ? await registeredHere(endpoint) : false;
        if (cancelled) return;
      }
      const now = new Date();
      const verdict = shouldShowPushPrompt(
        { flag: true, phone: true, ownChat, support, permission, registered, messageCount, streaming: false, state },
        now,
      );
      decided.current = true;
      if (verdict === "none") return;
      stored.current = state;
      setKind(verdict);
      // The sheet shows once for each 24 hours. A refusal's steps show once.
      void write({
        last_shown_at: now.toISOString(),
        ...(verdict === "denied" ? { denied_seen_at: now.toISOString() } : {}),
      });
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // `messageCount` is read when the effect runs. Only the edges re-run it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownChat, hasMessages, streaming, write]);

  const close = useCallback(() => setKind("none"), []);

  function onTurnOn() {
    // FIRST: the tap must still count when the browser asks.
    const run = enablePush(otherAccountsStay);
    setPhase("working");
    setNote(null);
    void run.then(
      (res) => {
        if (res.ok) {
          setPhase("on");
          closeTimer.current = setTimeout(close, ON_SHOWN_MS);
          return;
        }
        setPhase("ask");
        if (res.reason === "denied") {
          setKind("denied");
          void write({ denied_seen_at: new Date().toISOString() });
        } else if (res.reason !== "dismissed") {
          setNote(res.message);
        }
      },
      () => {
        setPhase("ask");
        setNote("Metorite could not turn on notifications. Try again.");
      },
    );
  }

  function onLater() {
    void write({ snoozed_until: snoozeUntil(new Date()) });
    close();
  }

  function onNever() {
    void write({ never: true });
    close();
    toast.show({
      key: "push-prompt-never",
      variant: "success",
      title: "We will not ask again",
      description: `To turn on notifications later, open ${SETTINGS_PATH}.`,
      action: { label: "Undo", onClick: () => void write({ never: false }) },
    });
  }

  if (kind === "none") return null;
  return (
    <PushPromptPanel
      kind={kind}
      phase={phase}
      note={note}
      onTurnOn={onTurnOn}
      onLater={onLater}
      onNever={onNever}
      onClose={close}
    />
  );
}
