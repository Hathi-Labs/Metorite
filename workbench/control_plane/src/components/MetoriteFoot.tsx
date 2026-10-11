"use client";

/**
 * The sidebar's foot with the shell bar on: our comet and the word
 * "Metorite", which open the Metorite menu (owner, 2026-10-12).
 *
 * The owner said: "Move the previous account selector from the bottom-left
 * sidebar to the Metorite logo and text. When the sidebar is minimized,
 * display only the logo. When expanded, show the logo with the word
 * 'Metorite.' Clicking the Metorite logo opens information and settings for
 * configuring Metorite, such as appearance, layout, and future options. The
 * user's profile and access controls also appear alongside the Metorite logo."
 *
 * - **Open rail.** The comet at 20px, then "Metorite" in the type the old
 *   account foot used for the name.
 * - **Folded rail.** The comet alone, in the geometry of a folded nav row
 *   (`p-2` block, `p-2.5` button, centred), so it sits on the rail's icon
 *   column. `e2e/shell-account.spec.ts` measures that within 1px.
 * - **The menu** opens upward from the foot. Its rows come from ONE list,
 *   `metoriteMenu` in `lib/shell/shellNav.ts`, so a new option is one line
 *   there. It holds NO account act. Switch, add and sign-out are in the
 *   shell bar's account menu (`BarAccount`). Fence: `shellAccount.test.ts`.
 *
 * No build version shows in the header. The browser can reach no version
 * that a member can read: `/api/health` carries the Next build id, a random
 * name, and the gateway's `/version` has no proxy (decision, 2026-10-12).
 *
 * The classic frame (shell bar off) keeps the account foot it had
 * (`SidebarAccountFooter`). The phone has no sidebar, so this never shows
 * there.
 */

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { MetoriteMark } from "@/components/MetoriteMark";
import { ThemeToggleMenuItem } from "@/components/ThemeToggle";
import { useDismiss } from "@/hooks/useDismiss";
import type { NavSection } from "@/lib/nav";
import { CHANGE_LAYOUT } from "@/lib/shell/shellPrefs";
import { metoriteMenu, type MetoriteItem } from "@/lib/shell/shellNav";

/** What each `action` item sends. One line per action id. */
const ACTIONS: Record<Extract<MetoriteItem, { kind: "action" }>["id"], () => void> = {
  "change-layout": () => window.dispatchEvent(new CustomEvent(CHANGE_LAYOUT)),
};

/** One rhythm for every row of the menu, a link or a button. */
const ROW = "w-full gap-3 px-3 py-2 text-[13px]";

export default function MetoriteFoot({
  collapsed,
  sections,
  layout,
}: {
  collapsed: boolean;
  /** The member's `visibleSections`, the one input of the rows. */
  sections: readonly NavSection[];
  /** "Change my layout" shows only while the shell nav is on (NS-7). */
  layout: boolean;
}) {
  const [open, setOpen] = useState(false);
  // Where the menu sits: its BOTTOM edge just above the button, its left
  // edge on the button's. ⚠️ Not AnchoredPanel, which places a flipped panel
  // by its top edge from its MAXIMUM height (the old account foot's note).
  const [box, setBox] = useState<{ left: number; bottom: number } | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const escape = useCallback(() => {
    setOpen(false);
    rootRef.current?.querySelector<HTMLButtonElement>("button[data-metorite-trigger]")?.focus();
  }, []);
  useDismiss(open, close, rootRef, escape);
  const groups = metoriteMenu(sections, { layout });

  const toggle = () => {
    if (!open) {
      const r = rootRef.current?.getBoundingClientRect();
      if (r) setBox({ left: Math.max(8, r.left), bottom: window.innerHeight - r.top + 6 });
    }
    setOpen((o) => !o);
  };

  return (
    <div
      data-metorite-foot=""
      className={`border-t border-sidebar-border ${collapsed ? "flex flex-col gap-1 p-2" : "px-2 py-2"}`}
    >
      <div ref={rootRef} className={collapsed ? "flex flex-col" : ""}>
        <button
          type="button"
          data-metorite-trigger=""
          onClick={toggle}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label="Metorite settings"
          title="Metorite"
          className={`flex items-center rounded-lg text-left hover:bg-sidebar-accent tech-transition ${
            collapsed ? "justify-center p-2.5" : "w-full gap-2.5 px-3 py-2"
          }`}
        >
          <MetoriteMark size={20} />
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-sidebar-foreground/90">Metorite</span>
              <Icon name="ChevronsUpDown" size={13} className="shrink-0 text-muted-foreground" />
            </>
          )}
        </button>
        {/* `position: fixed` inside the rail, so the rail's overflow does not
            clip it, and a click inside it is inside `rootRef`. */}
        {open && box && (
          <div
            role="dialog"
            aria-label="Metorite"
            style={{ left: box.left, bottom: box.bottom }}
            className="fixed z-[60] max-h-[calc(100vh-1rem)] w-64 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-md border border-border bg-card shadow-md"
          >
            <div className="flex items-center gap-2.5 px-4 pb-2.5 pt-3">
              <MetoriteMark size={22} />
              <span className="text-sm font-semibold text-foreground">Metorite</span>
            </div>
            {groups.map((g) => (
              <div key={g.id} role="group" aria-label={g.label} data-metorite-group={g.id} className="border-t border-border p-1">
                <div aria-hidden className="px-3 pb-1 pt-1.5 text-[11px] text-muted-foreground">
                  {g.label}
                </div>
                {g.items.map((item) => (
                  <MetoriteRow key={rowKey(item)} item={item} onDone={close} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function rowKey(item: MetoriteItem): string {
  return item.kind === "link" ? item.href : item.kind === "action" ? item.id : item.kind;
}

/** One row. A link and an action close the menu. The colour mode keeps it open, so the change shows. */
function MetoriteRow({ item, onDone }: { item: MetoriteItem; onDone: () => void }) {
  if (item.kind === "link") {
    return (
      <Link
        href={item.href}
        onClick={onDone}
        className={`flex items-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground tech-transition ${ROW}`}
      >
        <Icon name={item.icon} size={16} className="shrink-0" />
        {item.label}
      </Link>
    );
  }
  if (item.kind === "colour-mode") return <ThemeToggleMenuItem className={ROW} />;
  return (
    <Button
      variant="ghost"
      size="none"
      layout="flex items-center"
      onClick={() => {
        onDone();
        ACTIONS[item.id]();
      }}
      className={ROW}
    >
      <Icon name={item.icon} size={16} className="shrink-0" />
      {item.label}
    </Button>
  );
}
