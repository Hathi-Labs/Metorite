// Embedded Signup session messages (WS-20 WA-C2, spec
// `whatsapp_message_manager.md` §12.4).
//
// Meta's popup posts a `WA_EMBEDDED_SIGNUP` message to this window when the
// member finishes, cancels or meets an error. This module reads that message
// and decides what the page does next. It is pure and has no imports, so a
// `node` vitest can test it without a DOM.

/** The `extras` that ask Meta's popup for coexistence onboarding. */
export const SIGNUP_EXTRAS = {
  setup: {},
  featureType: "whatsapp_business_app_onboarding",
  sessionInfoVersion: "3",
} as const;

/**
 * What a coexistence number does not bring in (spec §12.2). The connect
 * screen shows this list BEFORE the popup opens.
 */
export const COEXISTENCE_LIMITS: readonly string[] = [
  "Group chats do not sync.",
  "Broadcast lists do not sync.",
  "Disappearing and view-once messages do not sync.",
  "Live location does not sync.",
  "Calls do not sync.",
  "Sending is limited to 20 messages a second.",
  "The connection drops after about 14 days with no activity in the WhatsApp Business app.",
];

export type SignupOnboarding = "cloud" | "coexistence";

export type SignupEvent =
  | {
      kind: "finish";
      onboarding: SignupOnboarding;
      waba_id: string;
      phone_number_id?: string;
    }
  | { kind: "cancel"; current_step: string }
  | { kind: "error"; error_message: string; error_id: string };

/** The text for FINISH_ONLY_WABA, and for a FINISH that names no number. */
export const NO_NUMBER_MESSAGE =
  "No number was selected. Connect again and select a WhatsApp Business number.";

/**
 * True only for a Facebook origin over https. A suffix regex such as
 * `/facebook\.com$/` also matches `evilfacebook.com`, so the check compares
 * the whole hostname or a `.facebook.com` suffix.
 */
export function isFacebookOrigin(origin: string): boolean {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  const host = url.hostname.toLowerCase();
  return host === "facebook.com" || host.endsWith(".facebook.com");
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

/**
 * Read one `message` event. Returns `null` for a message that is not an
 * Embedded Signup session message, or that came from another origin.
 */
export function parseSignupMessage(origin: string, raw: unknown): SignupEvent | null {
  if (!isFacebookOrigin(origin)) return null;
  let msg: unknown = raw;
  if (typeof raw === "string") {
    try {
      msg = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!msg || typeof msg !== "object") return null;
  const m = msg as { type?: unknown; event?: unknown; data?: unknown };
  if (m.type !== "WA_EMBEDDED_SIGNUP") return null;
  const data = (m.data && typeof m.data === "object" ? m.data : {}) as Record<
    string,
    unknown
  >;
  const wabaId = str(data.waba_id);
  const phoneId = str(data.phone_number_id);

  switch (m.event) {
    case "FINISH":
      if (!wabaId || !phoneId) {
        return { kind: "error", error_message: NO_NUMBER_MESSAGE, error_id: "" };
      }
      return { kind: "finish", onboarding: "cloud", waba_id: wabaId, phone_number_id: phoneId };
    case "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING":
      if (!wabaId) {
        return { kind: "error", error_message: NO_NUMBER_MESSAGE, error_id: "" };
      }
      // Meta returns no phone_number_id here. The backend reads it from the
      // WABA. Keep one if a later version of Meta sends it.
      return phoneId
        ? { kind: "finish", onboarding: "coexistence", waba_id: wabaId, phone_number_id: phoneId }
        : { kind: "finish", onboarding: "coexistence", waba_id: wabaId };
    case "FINISH_ONLY_WABA":
      return { kind: "error", error_message: NO_NUMBER_MESSAGE, error_id: "" };
    case "CANCEL":
      // Meta reports an error that the member saw as a CANCEL that carries
      // `error_message`. Treat it as an error.
      if (str(data.error_message)) {
        return {
          kind: "error",
          error_message: str(data.error_message) ?? "",
          error_id: str(data.error_id) ?? "",
        };
      }
      return { kind: "cancel", current_step: str(data.current_step) ?? "" };
    case "ERROR":
      return {
        kind: "error",
        error_message: str(data.error_message) ?? "Meta reported an error during signup.",
        error_id: str(data.error_id) ?? "",
      };
    default:
      return null;
  }
}

/** The text that the page shows for a CANCEL or an ERROR. */
export function signupErrorText(ev: SignupEvent): string {
  if (ev.kind === "cancel") {
    return ev.current_step
      ? `Signup was cancelled at step ${ev.current_step}. Nothing was connected.`
      : "Signup was cancelled. Nothing was connected.";
  }
  if (ev.kind === "error") {
    return ev.error_id
      ? `${ev.error_message} (Meta error ${ev.error_id})`
      : ev.error_message;
  }
  return "";
}

export type SignupRequest = {
  code: string;
  waba_id: string;
  phone_number_id?: string;
  onboarding: SignupOnboarding;
};

export type SignupStep =
  | { action: "wait" }
  | { action: "fail"; message: string }
  | { action: "submit"; request: SignupRequest };

/**
 * Decide the next step from the two halves of a signup. The FB.login
 * callback gives the `code`, and the message event gives the result. They
 * can arrive in either order.
 *
 * `code` is `undefined` before the callback runs, and `null` when the
 * callback ran with no code. `ev` is `null` until the message arrives.
 */
export function nextSignupStep(
  code: string | null | undefined,
  ev: SignupEvent | null,
): SignupStep {
  if (ev && ev.kind !== "finish") return { action: "fail", message: signupErrorText(ev) };
  if (code === null) {
    return {
      action: "fail",
      message: ev
        ? "Facebook returned no authorization code. Connect again."
        : "Signup was cancelled. Nothing was connected.",
    };
  }
  if (!ev || code === undefined) return { action: "wait" };
  const request: SignupRequest = { code, waba_id: ev.waba_id, onboarding: ev.onboarding };
  if (ev.phone_number_id) request.phone_number_id = ev.phone_number_id;
  return { action: "submit", request };
}
