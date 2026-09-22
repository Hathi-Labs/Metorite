/**
 * One scale for a page title, wherever the page lives.
 *
 * Spec: `DESIGN_SYSTEM.md` · `AGENTS.md` rule 1.
 *
 * **Looked at, not reasoned about (2026-09-22).** `PageHeader` and
 * `SettingsHeader` both shipped with their own title classes, and rendering
 * People beside Settings in all eight contexts showed the cost. On
 * `/people/dashboard` the title read `text-sm font-medium` — the SAME size as
 * the tab label directly above it, and usually the same word. The tab looked
 * like the heading and the heading looked like a caption. Tab across to
 * Organisation and the title jumped to `text-base font-bold sm:text-lg`.
 *
 * One product, two title sizes, forty pixels apart. That is the thing the
 * owner named on 2026-09-21: *"including headings of the apps, etc. So that
 * everything is one standard user experience."*
 *
 * ⚠️ **The People app moved to meet Settings, not the other way round.** The
 * `text-sm` came from taking the modal spelling of eight when `PageHeader`
 * was minted — the most common value, not a designed one. Settings' scale is
 * the one that wins an argument with a tab label above it.
 *
 * The fence is `pageHeading.test.ts`: both components must use this constant,
 * and neither may spell a size of its own.
 */

/** The page title. Both header components use exactly this, and nothing else. */
export const HEADING_TITLE = "text-base font-bold text-foreground sm:text-lg";

/** The line under it that says what the surface is for. */
export const HEADING_SUBTITLE = "mt-0.5 max-w-prose text-xs text-muted-foreground";
