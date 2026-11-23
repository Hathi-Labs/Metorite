/**
 * The flag for the pin suggestion (`package_host.md` §9.3 item 2, PKG-19a-2).
 * Off, the shell counts no pane open and My Day draws no pin card. The card
 * adds no request. The counter lives in this browser (`pinSuggestStore.ts`).
 *
 * Turning it on in production is the `enforcement-flip` act.
 */

/** Whether the shell counts pane opens and My Day draws the pin card. */
export function pinSuggestOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_PIN_SUGGEST === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-pin-suggest"] = "1"`.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-pin-suggest") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function pinSuggestOnServer(): boolean {
  return process.env.NEXT_PUBLIC_PIN_SUGGEST === "1";
}
