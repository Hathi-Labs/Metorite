/**
 * The page-scoped header — one title treatment for every surface.
 *
 * Spec: `workbench/control_plane/DESIGN_SYSTEM.md` · `AGENTS.md` rule 1
 * (every app is a projection of one product, never a surface with its own
 * look).
 *
 * **Measured 2026-09-21, and this is why it exists.** The `<h1>` across
 * People, Projects and Settings came in EIGHT spellings, from
 * `text-xs font-medium text-muted-foreground` to
 * `text-lg font-semibold`. The People app alone carried three: `text-sm
 * font-semibold` on the directory, `text-sm font-medium` on four surfaces,
 * and `text-lg font-semibold` on three more. Tab across them and the title
 * changes size.
 *
 * Nothing was broken. It simply did not read as one product, which is the
 * failure `AGENTS.md` rule 1 names and which nothing in the conformance
 * suite could see — it checks eight regexes, and none of them is a heading.
 *
 * ⚠️ **This is the PAGE header, not the app bar.** Projects and Tasks open
 * with a slim `h-10` bar carrying a rail toggle, the app's name and
 * app-level actions. That is a different, deliberate shape for a rail-based
 * app, and this component does not replace it — it is what goes *inside* a
 * surface, above its content. A surface that has both gets the bar from its
 * layout and the header from here.
 *
 * **Three parts, and only the title is required:**
 *
 * - `title` — what this surface is. One line, truncated rather than wrapped,
 *   because a heading that reflows moves everything under it.
 * - `subtitle` — what it is FOR. Optional, and bounded by `max-w-prose`,
 *   since the reason for a surface is prose and prose has a measure.
 * - `actions` — the controls that act on the whole surface. Right-aligned,
 *   and they wrap under the title on a narrow screen rather than crushing
 *   it (measured at 390px, where three controls plus a title do not fit).
 */

import type { ReactNode } from "react";

export interface PageHeaderProps {
  /** What this surface is. Required — a surface without a name is a bug. */
  title: ReactNode;
  /** What it is for. One sentence; the component bounds the measure. */
  subtitle?: ReactNode;
  /** Whole-surface controls, right-aligned, wrapping under on mobile. */
  actions?: ReactNode;
  /** A count or status that belongs beside the title rather than under it. */
  meta?: ReactNode;
  /** Extra classes on the outer element. Never a `max-w-` or a text size. */
  className?: string;
}

export function PageHeader({
  title,
  subtitle,
  actions,
  meta,
  className = "",
}: PageHeaderProps) {
  return (
    <header
      // `flex-wrap` and `items-start`: at 390px the actions drop under the
      // title instead of compressing each label onto two lines. Measured.
      className={`mb-3 flex flex-wrap items-start justify-between gap-x-3 gap-y-2 ${className}`}
    >
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <h1 className="truncate text-sm font-medium text-foreground">
            {title}
          </h1>
          {meta ? (
            <span className="shrink-0 text-xs text-muted-foreground">{meta}</span>
          ) : null}
        </div>
        {subtitle ? (
          <p className="mt-0.5 max-w-prose text-[11px] text-muted-foreground">
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

export default PageHeader;
