/**
 * The header of a Settings sub-page — back link, title, subtitle, actions.
 *
 * Spec: `workbench/control_plane/DESIGN_SYSTEM.md` · `AGENTS.md` rule 1.
 *
 * **Why this is NOT `PageHeader`.** The product has two legitimate heading
 * shapes, and collapsing them would be the wrong fix:
 *
 * - `PageHeader` is the **document header**. It is quiet (`text-sm
 *   font-medium`), it has no rule under it, and it sits at the top of a page
 *   that flows and scrolls. The People app is that shape.
 * - This is the **pane header**. A Settings sub-page is a full-height pane you
 *   navigated INTO, so it opens with a heavier title and the back link that
 *   takes you out again. Losing that link is losing the way back.
 *
 * **Measured 2026-09-22, and that is why this exists.** Six Settings pages —
 * Appearance, Billing, Teams, Roles, Organisation and Branding — each
 * hand-rolled the identical markup: a flex row, a bordered back `Link`, a
 * `text-base font-bold text-foreground sm:text-lg` title and a
 * `mt-0.5 text-xs text-muted-foreground` subtitle. Six copies of one shape is
 * five chances for it to drift, and two had already drifted to
 * `text-base font-semibold`.
 *
 * ⚠️ `settings/appearance/page.tsx` had gone further and declared a LOCAL
 * component called `PageHeader`, which is a different component wearing the
 * name of the shared one. That is the CLAUDE.md §5 defect exactly: a second
 * implementation of an existing seam, discoverable only by reading the import.
 *
 * The fence is `src/components/pageHeading.test.ts`.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import Icon from "@/components/Icon";
import { HEADING_SUBTITLE, HEADING_TITLE } from "@/components/headingScale";

export interface SettingsHeaderProps {
  /** What this pane is. */
  title: ReactNode;
  /** What it is for. One sentence. */
  subtitle?: ReactNode;
  /** Where the back arrow goes. Omit for a pane with no parent. */
  backHref?: string;
  /** Read by a screen reader in place of the arrow. */
  backLabel?: string;
  /** Whole-pane controls, right-aligned. */
  actions?: ReactNode;
  /** True when the title is data (a person's name) and may be long. */
  truncate?: boolean;
  /** Extra classes on the outer element. Never a text size. */
  className?: string;
}

export function SettingsHeader({
  title,
  subtitle,
  backHref,
  backLabel = "Back",
  actions,
  truncate = false,
  className = "",
}: SettingsHeaderProps) {
  return (
    <header
      // `flex-wrap` and `gap-y-2`: at 390px a title plus two actions does not
      // fit on one line, and wrapping beats compressing every label.
      className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-2 ${className}`}
    >
      <div className="flex min-w-0 items-center gap-3">
        {backHref ? (
          <Link
            href={backHref}
            className="shrink-0 rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
            aria-label={backLabel}
          >
            <Icon name="ArrowLeft" size={15} />
          </Link>
        ) : null}
        <div className="min-w-0">
          <h1 className={`${HEADING_TITLE}${truncate ? " truncate" : ""}`}>
            {title}
          </h1>
          {subtitle ? (
            <p className={HEADING_SUBTITLE}>{subtitle}</p>
          ) : null}
        </div>
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

export default SettingsHeader;
