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
 *
 * **The star says what the sidebar shows** (owner, 2026-10-11). A filled
 * star means that the app shows in the sidebar, under its own category. An
 * outline star means that it does not. The owner said "clicking the star",
 * so the star stays, and its tooltip and its name say what it does. The
 * star shows once the member's layout is known. Before that, a toggle would
 * write over a layout the shell has not read. The write shows at once and
 * goes back when the server refuses it (`saveShellPrefs`).
 *
 * `hint` is one line at the top. The sidebar's "Add apps" row opens this
 * dialog with it, so a member who came to add an app reads what to do.
 */
import Link from "next/link";
import { useRef, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import type { NavSection } from "@/lib/nav";
import { EMPTY_SHELL, STARRABLE, isStarred, toggleStar } from "@/lib/shell/presets";
import { isActive, launcherGroups } from "@/lib/shell/shellNav";
import { saveShellPrefs, useShellPrefs } from "@/lib/shell/shellPrefs";

export default function AppLauncher({
  open,
  onClose,
  sections,
  pathname,
  hint,
}: {
  open: boolean;
  onClose: () => void;
  /** `visibleSections(...)` for this member. */
  sections: readonly NavSection[];
  pathname: string | null;
  /** One line at the top, for a member who came here to add an app. */
  hint?: string;
}) {
  const groups = launcherGroups(sections);
  // ⚠️ Focus the app the member is in, else the first app. Without this the
  // dialog focuses its first tabbable, the header's Close button, and Enter
  // shuts the dialog with no app opened (review of NS-2, 2026-10-09).
  const shown = groups.flatMap((g) => g.items);
  const focusHref = (shown.find((p) => isActive(pathname, p.href)) ?? shown[0])?.href;
  const focusRef = useRef<HTMLAnchorElement | null>(null);
  const shell = useShellPrefs();
  const canStar = shell.enabled && shell.stored !== undefined;
  const [starError, setStarError] = useState<string | null>(null);
  const toggle = (href: string) => {
    setStarError(null);
    const next = { ...EMPTY_SHELL, ...shell.stored, ...toggleStar(shell.layout, sections, href) };
    saveShellPrefs(next).catch(() => setStarError("Your sidebar was not saved. Try again."));
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      initialFocus={focusHref ? focusRef : undefined}
      title="All apps"
      description="Everything you can open here, and what each one is for."
      icon="LayoutGrid"
      size="2xl"
      placement="top"
    >
      {/* The body pads itself, as every Modal body does (TaskSettingsModal). */}
      <div className="flex max-h-[70vh] flex-col gap-5 overflow-y-auto p-4" data-testid="app-launcher">
        {groups.length === 0 && (
          <p className="text-sm text-muted-foreground">
            You have no apps yet. An admin of your organization chooses them.{" "}
            <Link href="/people/access" onClick={onClose} className="text-primary underline-offset-2 hover:underline">
              See what you can open
            </Link>
          </p>
        )}
        {hint && groups.length > 0 && (
          <p className="text-sm text-muted-foreground" data-testid="launcher-hint">
            <Icon name="Star" size={14} className="mr-1.5 inline-block align-[-2px] text-primary" />
            {hint}
          </p>
        )}
        {starError && (
          <p role="alert" className="text-xs text-destructive">
            {starError}
          </p>
        )}
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
                const starrable = canStar && STARRABLE.has(p.href);
                const shown = starrable && isStarred(shell.layout, sections, p.href);
                return (
                  <li key={p.href} className="relative">
                    <Link
                      ref={p.href === focusHref ? focusRef : undefined}
                      href={p.href}
                      onClick={onClose}
                      aria-current={here ? "page" : undefined}
                      className={`flex h-full items-start gap-3 rounded-lg border px-3 py-2.5 tech-transition ${starrable ? "pr-10" : ""} ${
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
                    {/* A sibling of the link, never inside it: a button in a
                        link is two controls in one, and a click on the star
                        must not open the app. */}
                    {/* ⚠️ The wrapper places the star. `.cc-control` sets its
                        own `position`, which beats an `absolute` class on
                        the button, and the star then drew under the tile. */}
                    {starrable && (
                      <span className="absolute right-2 top-2">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-pressed={shown}
                          aria-label={shown ? `Hide ${p.label} from sidebar` : `Show ${p.label} in sidebar`}
                          title={shown ? "Shown in sidebar" : "Show in sidebar"}
                          onClick={() => toggle(p.href)}
                          data-testid="launcher-star"
                        >
                          <Icon
                            name="Star"
                            size={14}
                            className={shown ? "fill-current text-primary" : "text-muted-foreground"}
                          />
                        </Button>
                      </span>
                    )}
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
