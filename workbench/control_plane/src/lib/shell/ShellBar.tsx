"use client";

/**
 * The shell bar: one row across the top of every desktop page
 * (`navigation_shell.md` §3.1, NS-1, flag `NEXT_PUBLIC_SHELL_BAR`).
 *
 *   [ the app's name and its own buttons ]  [ ✦ Search or ask anything  ⌘K ]  [ the app's tools ]
 *
 * With the shell nav on too, the bar spans the whole window and opens with a
 * `lead` zone that AppShell fills: the sidebar's fold control and the
 * organization's logo (owner, 2026-10-09, `desktopFrame` in `shellNav.ts`).
 *
 * **The app fills the two side slots. The shell owns the middle.** An app that
 * drew `AppTopBar` keeps doing so, and `AppTopBar` portals its title, its rail
 * toggle, its actions and its tools into these slots instead of drawing a row
 * of its own (`useShellSlots`). So Projects and My Tasks change nothing, and
 * no app loses height. A page with no `AppTopBar` shows its app's name and
 * icon in the left slot, so every page says where the member is, in the same
 * place.
 *
 * **This file holds the shell's ONE keyboard listener** (§5.2 rule 2,
 * `seams.test.ts`):
 *   • `⌘K` / `Ctrl K` opens the command bar, from anywhere. It is taken in the
 *     CAPTURE phase on `window` and stopped there, so the three app listeners
 *     that NS-9 deletes never see it while this flag is on.
 *   • `/` focuses the page's own filter (`[data-page-filter]`) when the page
 *     has one, and opens the command bar when it does not (§6.1, owner
 *     decision 2026-10-07). Never while the member types in a field.
 *
 * Anything may open the bar with words already in it:
 * `window.dispatchEvent(new CustomEvent(OPEN_COMMAND_BAR, { detail: { query } }))`.
 * A list filter does that for "Search everywhere for …" (§6.7 rule 3).
 *
 * **The activity control** (WS-51 S3) is the shell's own, at the right end of
 * the bar, after the app's tools: every live assistant run, across apps
 * (`ActivityControl.tsx`). The phone opens the same panel from its Menu drawer
 * with `OPEN_ACTIVITY`, so this frame mounts the one panel for both.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";
import { useActivityRows, useRunActivity } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { visibleSections } from "@/lib/nav";
import { ActivityControl, ActivityPanel } from "./ActivityControl";
import { CommandBar } from "./CommandBar";
import { focusPageFilter, pageFilterTarget } from "./pageFilter";
import { OPEN_ACTIVITY, OPEN_COMMAND_BAR, contextPane, heldPanes } from "./registry";

export { FILL_PAGE_FILTER, OPEN_ACTIVITY, OPEN_COMMAND_BAR } from "./registry";

export interface ShellSlots {
  left: HTMLElement | null;
  right: HTMLElement | null;
  /** An `AppTopBar` claims the slots while it is mounted. Returns the release. */
  claim: () => () => void;
}

const SlotsContext = createContext<ShellSlots | null>(null);

/** The shell's slots, or null when the shell bar is off. */
export function useShellSlots(): ShellSlots | null {
  return useContext(SlotsContext);
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

export function ShellFrame({
  children,
  bar = true,
  lead,
}: {
  children: ReactNode;
  bar?: boolean;
  /**
   * The bar's first zone, before the app's slot: the sidebar's fold control
   * and the organization's logo, when the bar spans the whole width (owner,
   * 2026-10-09). The shell fills it, never an app.
   */
  lead?: ReactNode;
}) {
  const [left, setLeft] = useState<HTMLElement | null>(null);
  const [right, setRight] = useState<HTMLElement | null>(null);
  const [claims, setClaims] = useState(0);
  const claim = useCallback(() => {
    setClaims((c) => c + 1);
    return () => setClaims((c) => c - 1);
  }, []);
  const slots = useMemo<ShellSlots>(() => ({ left, right, claim }), [left, right, claim]);

  const [open, setOpen] = useState(false);
  const [seed, setSeed] = useState("");
  const openWith = useCallback((query: string) => {
    setSeed(query);
    setOpen(true);
  }, []);

  // ── The one listener ──────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        setSeed("");
        setOpen((o) => !o);
        return;
      }
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        // The page's filter when it has one (opened first if it is closed),
        // the command bar when it has none.
        if (pageFilterTarget()) void focusPageFilter();
        else openWith("");
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [openWith]);

  useEffect(() => {
    const onOpen = (e: Event) => openWith((e as CustomEvent<{ query?: string }>).detail?.query ?? "");
    window.addEventListener(OPEN_COMMAND_BAR, onOpen);
    return () => window.removeEventListener(OPEN_COMMAND_BAR, onOpen);
  }, [openWith]);

  const pathname = usePathname() ?? "/";
  const { access, loading } = useAccess();
  const panes = useMemo(
    () => heldPanes(visibleSections(loading ? null : access.features, access.is_admin)),
    [loading, access.features, access.is_admin],
  );
  const here = contextPane(pathname, panes);
  const { data: session } = useSession();

  // ── The activity control and its panel (WS-51 S3) ─────────────────────
  // The S1 store only: no poll of its own, and no poll at all for a person
  // with no workspace (`shouldPollWorkspace`, as the sidebar reads it).
  const workspace = shouldPollWorkspace(access, loading);
  const { total: running, needsTotal } = useRunActivity(null, workspace);
  const activityRows = useActivityRows(workspace);
  const heldKey = panes.map((p) => p.href).join(",");
  const heldHrefs = useMemo(
    () => new Set(heldKey ? heldKey.split(",") : []),
    [heldKey],
  );
  const [activityOpen, setActivityOpen] = useState(false);
  useEffect(() => {
    const onOpen = () => setActivityOpen(true);
    window.addEventListener(OPEN_ACTIVITY, onOpen);
    return () => window.removeEventListener(OPEN_ACTIVITY, onOpen);
  }, []);

  return (
    <SlotsContext.Provider value={slots}>
      {/* The phone draws no row: its Menu drawer opens the same bar (§9). */}
      {bar ? (
        <ShellBarRow
          here={here}
          claimed={claims > 0}
          setLeft={setLeft}
          setRight={setRight}
          onOpen={() => openWith("")}
          lead={lead}
          activity={
            workspace ? (
              <ActivityControl
                running={running}
                needsInput={needsTotal}
                open={activityOpen}
                onOpen={() => setActivityOpen(true)}
              />
            ) : null
          }
        />
      ) : null}
      {children}
      <ActivityPanel
        open={activityOpen}
        onClose={() => setActivityOpen(false)}
        rows={activityRows}
        visibleHrefs={heldHrefs}
        placement={bar ? "end" : "sheet"}
      />
      <CommandBar
        open={open}
        seed={seed}
        onClose={() => setOpen(false)}
        panes={panes}
        here={here}
        email={session?.user?.email ?? null}
      />
    </SlotsContext.Provider>
  );
}

/** "⌘K" on a Mac, "Ctrl K" elsewhere. Read after mount, so the server agrees. */
function useShortcutLabel(): string {
  const [label, setLabel] = useState("Ctrl K");
  useEffect(() => {
    try {
      if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) setLabel("⌘K");
    } catch {
      /* keep Ctrl K */
    }
  }, []);
  return label;
}

function ShellBarRow({
  here,
  claimed,
  setLeft,
  setRight,
  onOpen,
  lead,
  activity,
}: {
  here: { label: string; icon: string } | null;
  claimed: boolean;
  setLeft: (el: HTMLElement | null) => void;
  setRight: (el: HTMLElement | null) => void;
  onOpen: () => void;
  lead?: ReactNode;
  /** The shell's own control at the right end, after the app's tools. */
  activity?: ReactNode;
}) {
  const shortcut = useShortcutLabel();
  // ⚠️ With the brand zone, a side slot never gets LESS than its content, and
  // the command bar gives way instead. The zone keeps its width when the
  // sidebar folds, so at 1280px My Tasks' title, Capture and My day ran under
  // the command bar (measured 2026-10-09). The bar stays centred while it fits.
  const side = lead ? "min-w-max" : "min-w-0";
  return (
    <header
      data-shell-bar
      className={`flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card ${lead ? "pr-2" : "px-2"}`}
    >
      {lead}
      <div className={`flex ${side} flex-1 basis-0 items-center gap-2`}>
        {/* The app's name for a page with no AppTopBar. Not an h1: such a
            page already has its own heading. */}
        {!claimed && here ? (
          <span className="flex min-w-0 items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
            <Icon name={here.icon} size={14} className="shrink-0" />
            <span className="truncate">{here.label}</span>
          </span>
        ) : null}
        <div ref={setLeft} className="flex min-w-0 items-center gap-2 empty:hidden" />
      </div>

      {/* A fake field, on purpose: it looks like the place to type, and a
          press opens the real one. Every member reads it as "search here". */}
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-label={`Search or ask anything (${shortcut})`}
        className="flex h-8 w-full max-w-xl shrink items-center gap-2 rounded-lg border border-border bg-background px-3 text-left text-[13px] text-muted-foreground tech-transition hover:border-primary/40 hover:text-foreground"
      >
        <Icon name="Sparkles" size={15} className="shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate">Search or ask anything</span>
        {here ? (
          <span className="hidden shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground lg:inline">
            in {here.label}
          </span>
        ) : null}
        <kbd className="shrink-0 rounded border border-border px-1.5 py-0.5 font-sans text-[10px] text-muted-foreground">
          {shortcut}
        </kbd>
      </button>

      <div className={`flex ${side} flex-1 basis-0 items-center justify-end gap-1`}>
        {/* The app's tools portal here (`AppTopBar`), the bell among them. */}
        <div ref={setRight} className="flex min-w-0 items-center justify-end gap-1 empty:hidden" />
        {activity}
      </div>
    </header>
  );
}
