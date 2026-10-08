"use client";

/**
 * All apps (NS-2, `navigation_shell.md` §3.2). Every app the member holds, by
 * team, each with its purpose in one line.
 *
 * Why a list with purposes, and not a grid of icons. A new member does not
 * know what "People" or "Projects" holds here. Reading "Find a colleague,
 * their skills and who is out" is recognition, which is cheap, where guessing
 * from a name is recall, which is not. The groups keep the list short to scan:
 * at most four headings, a few apps under each.
 *
 * It reads `launcherGroups(visibleSections(...))`, so it shows nothing the
 * member does not hold (`launch_surface.md` §8.4).
 */
import Link from "next/link";
import Icon from "@/components/Icon";
import Modal from "@/components/ui/Modal";
import type { NavSection } from "@/lib/nav";
import { isActive, launcherGroups } from "@/lib/shell/shellNav";

export default function AppLauncher({
  open,
  onClose,
  sections,
  pathname,
}: {
  open: boolean;
  onClose: () => void;
  /** `visibleSections(...)` for this member. */
  sections: readonly NavSection[];
  pathname: string | null;
}) {
  const groups = launcherGroups(sections);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="All apps"
      description="Everything you can open here, and what each one is for."
      icon="LayoutGrid"
      size="2xl"
      placement="top"
    >
      {/* The body pads itself, as every Modal body does (TaskSettingsModal). */}
      <div className="flex max-h-[70vh] flex-col gap-5 overflow-y-auto p-4" data-testid="app-launcher">
        {groups.map((g) => (
          <section key={g.id} aria-labelledby={`launcher-${g.id}`}>
            <h3
              id={`launcher-${g.id}`}
              className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
            >
              {g.label}
            </h3>
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {g.items.map((p) => {
                const here = isActive(pathname, p.href);
                return (
                  <li key={p.href}>
                    <Link
                      href={p.href}
                      onClick={onClose}
                      aria-current={here ? "page" : undefined}
                      className={`flex h-full items-start gap-3 rounded-lg border px-3 py-2.5 tech-transition ${
                        here
                          ? "border-primary/40 bg-primary/10"
                          : "border-border hover:border-primary/30 hover:bg-secondary"
                      }`}
                    >
                      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                        <Icon name={p.icon} size={16} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-medium text-foreground">{p.label}</span>
                        <span className="block text-xs leading-snug text-muted-foreground">
                          {p.blurb ?? p.note}
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  );
}
