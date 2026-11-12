/**
 * How an external event looks (CE-5a, `calendar_events_reminders.md` §6.2).
 * A muted surface with a stripe. The stripe is `--hatch-stripe`, a token of
 * `globals.css` and `themes.ts`. No colour is written here.
 *
 * A free entry (`busy` false) is lighter. A tentative one has a dashed border.
 */
const STRIPES =
  "bg-[image:repeating-linear-gradient(135deg,var(--hatch-stripe)_0_2px,transparent_2px_7px)]";

export function externalLook(e: { free: boolean; tentative: boolean }): string {
  return [
    e.free ? "bg-muted/40" : "bg-muted",
    STRIPES,
    "text-foreground border",
    e.tentative ? "border-dashed border-muted-foreground/60" : "border-border",
  ]
    .filter(Boolean)
    .join(" ");
}
