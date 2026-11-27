/**
 * The flag for the brief card on My Day (`core_brief.md` §5 item 1, BR-2c-2).
 * Off, My Day draws no card and sends no read to `/api/brief/today`.
 *
 * The gateway has its own switch, `BRIEF_IN_APP`. This one only shows the
 * card. Turning either on in production is the `enforcement-flip` act.
 */

/** Whether My Day draws the brief card in this browser. */
export function briefCardOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_BRIEF_CARD === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-brief-card"] = "1"`.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-brief-card") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function briefCardOnServer(): boolean {
  return process.env.NEXT_PUBLIC_BRIEF_CARD === "1";
}
