/**
 * The chat's push prompt: when it shows, and the state it keeps (WS-OB OB-8).
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §10.4.
 *
 * `shouldShowPushPrompt` is the ONE show rule. The component gathers the
 * facts and calls it, and it holds no rule of its own. The state is one key,
 * `push_prompt`, of the member's own prefs (`lib/shell/feed.ts`). A PUT
 * replaces the whole value of one key, so a write reads, merges, then
 * writes (`mergePromptState`).
 *
 * Fence: `pushPrompt.test.ts`.
 */

import type { PushSupport } from "@/lib/push";

/** The stored state of the prompt. Each time is ISO 8601 with an offset. */
export interface PromptState {
  snoozed_until?: string;
  last_shown_at?: string;
  denied_seen_at?: string;
  never?: boolean;
}

/** What the sheet shows. `none` means no sheet. */
export type PromptKind =
  /** The Turn on button. */
  | "turn-on"
  /** The permission is refused: the phone-settings steps. */
  | "denied"
  | "none";

export interface PromptInput {
  /** `GET /push/public-key` answered `prompt: true`. */
  flag: boolean;
  /** `isPhoneNow()`. */
  phone: boolean;
  /** The chat is the member's own assistant: `isOrchestrator && !isRoom`. */
  ownChat: boolean;
  support: PushSupport;
  permission: NotificationPermission | "unsupported";
  /** This device holds a row for this member (`registeredHere`). */
  registered: boolean;
  messageCount: number;
  /** A run streams now. */
  streaming: boolean;
  state: PromptState;
}

/** The sheet does not come back within this long of its last showing. */
export const SHOWN_GAP_MS = 24 * 60 * 60 * 1000;
/** "Remind me later" waits this long. */
export const SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;

function after(iso: string | undefined, now: Date): boolean {
  const at = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(at) && at > now.getTime();
}

/** True when `iso` is a time within the last `ms` before `now`. */
function within(iso: string | undefined, ms: number, now: Date): boolean {
  const at = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(at) && now.getTime() - at < ms;
}

/** Which sheet to show now, or `none`. Every rule of §10.4 item 3 and 6. */
export function shouldShowPushPrompt(input: PromptInput, now: Date): PromptKind {
  if (!input.flag || !input.phone || !input.ownChat) return "none";
  // A Safari tab (`ios-tab`) shows nothing. `InstallNudge` owns the Home Screen ask.
  if (input.support !== "supported") return "none";
  // An empty chat is the first run, and the interview offer shows there.
  if (input.messageCount < 1 || input.streaming) return "none";
  const s = input.state;
  if (s.never === true) return "none";
  if (after(s.snoozed_until, now)) return "none";
  if (within(s.last_shown_at, SHOWN_GAP_MS, now)) return "none";
  if (input.permission === "granted") return input.registered ? "none" : "turn-on";
  if (input.permission === "denied") return s.denied_seen_at ? "none" : "denied";
  if (input.permission === "unsupported") return "none";
  return "turn-on";
}

/** The `push_prompt` object of the prefs, or an empty state. */
export function promptStateOf(prefs: Readonly<Record<string, unknown>> | null): PromptState {
  const raw = prefs?.push_prompt;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const o = raw as Record<string, unknown>;
  const out: PromptState = {};
  for (const k of ["snoozed_until", "last_shown_at", "denied_seen_at"] as const) {
    if (typeof o[k] === "string") out[k] = o[k];
  }
  if (o.never === true) out.never = true;
  return out;
}

/** The whole value to write: the stored state with `patch` on top. */
export function mergePromptState(stored: PromptState, patch: PromptState): PromptState {
  return { ...stored, ...patch };
}

/** The `snoozed_until` of "Remind me later". */
export function snoozeUntil(now: Date): string {
  return new Date(now.getTime() + SNOOZE_MS).toISOString();
}

/** Where a member turns push on, or back on after "Don't ask again". */
export const SETTINGS_PATH = "My Profile → Notifications";

export const PROMPT_TITLE = "Get notified on this phone";
export const PROMPT_BODY =
  "Turn on notifications so reminders, your morning brief and replies that need you reach this phone, even when Metorite is closed.";
