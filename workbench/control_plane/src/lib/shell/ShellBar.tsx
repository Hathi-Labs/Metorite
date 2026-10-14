"use client";

/**
 * The shell bar: one row across the top of every desktop page
 * (`navigation_shell.md` §3.1, NS-1, flag `NEXT_PUBLIC_SHELL_BAR`).
 *
 *   [ fold · logo ]     [ ✦ Ask Metorite, or search and open anything…  in <App>  ⌘K ]     [ panel · bell · activity ]
 *
 * With the shell nav on too, the bar spans the whole window and opens with a
 * `lead` zone that AppShell fills: the sidebar's fold control and the
 * organization's logo (owner, 2026-10-09, `desktopFrame` in `shellNav.ts`).
 *
 * **The bar is CONSTANT. An app never renders into it** (owner, 2026-10-10).
 * It holds only shell things: the lead zone, the command bar, the shell's
 * bell (NS-6, behind `NEXT_PUBLIC_SHELL_DOCK`) and the shell's activity
 * control. It has no slot, no portal target and no app name, so it draws the
 * same pixels on every page. Each app opens with its own title bar
 * under it, `AppTopBar` (`components/AppTopBar.tsx`), which holds the app's
 * name and tools. The "in <App>" chip inside the command bar stays: it is
 * the command bar's own scope (§6.4 rule 3), not the app's chrome.
 * Fence: `appBar.test.ts` beside this file.
 *
 * **This file holds the shell's ONE keyboard listener** (§5.2 rule 2,
 * `seams.test.ts`):
 *   • `⌘K` / `Ctrl K` opens the command bar, from anywhere. It is taken in the
 *     CAPTURE phase on `window` and stopped there, so the three app listeners
 *     that NS-9 deletes never see it while this flag is on.
 *   • `/` focuses the page's own filter (`[data-page-filter]`) when the page
 *     has one, and opens the command bar when it does not (§6.1, owner
 *     decision 2026-10-07). Never while the member types in a field.
 *   • `⌘J` / `Ctrl J`, with `NEXT_PUBLIC_SHELL_CHAT` on (D99, §7.1): opens
 *     the assistant's side panel. With the assistant open it puts the cursor
 *     in it, and pressed again in the assistant it closes it.
 *
 * **With `NEXT_PUBLIC_SHELL_CHAT` on, the field in the middle is REAL**
 * (`AskField` in `ChatBar.tsx`, D102 as amended by the owner on 2026-10-14).
 * It asks Metorite, searches and opens, and there is no bar at the foot of
 * the page. ⌘K, `/` and "search everywhere for …" put the cursor in it. The
 * panel button beside the bell opens and closes the assistant, and the
 * activity control keeps its one job: it shows the running count and opens
 * the list of runs.
 *
 * Anything may open the bar with words already in it:
 * `window.dispatchEvent(new CustomEvent(OPEN_COMMAND_BAR, { detail: { query } }))`.
 * A list filter does that for "Search everywhere for …" (§6.7 rule 3).
 *
 * **The activity control** (WS-51 S3) is the shell's own, at the right end of
 * the bar: every live assistant run, across apps
 * (`ActivityControl.tsx`). `AppShell` mounts its one panel, in every layout,
 * so the control works with this bar's flag off too (from the sidebar head).
 *
 * **The bell** (NS-6 slice 6a, `ShellBell.tsx`) sits just before it, while
 * `NEXT_PUBLIC_SHELL_DOCK` is on. The owner placed both here on 2026-10-10:
 * "They belong to the whole product, not to an app." An app's own bell or
 * tools never move into this bar.
 *
 * **The bar holds no account** (owner, 2026-10-14). The account is the
 * sidebar's foot again, with the member's pages and settings in its one menu
 * (`AccountFoot` in `components/AccountSwitcher.tsx`). The 2026-10-12 shape,
 * an account at this bar's right end, is superseded. The bar draws no
 * Metorite logo of its own, and only a workspace with no logo shows the comet
 * as its badge in the lead zone. Fences: `shellAccount.test.ts` and
 * `e2e/shell-account.spec.ts`.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";
import { visibleSections } from "@/lib/nav";
import { ActivityControl } from "./ActivityControl";
import Button from "@/components/ui/Button";
import {
  ASSISTANT_NAME,
  assistantState,
  closeAssistant,
  focusAssistant,
  openAssistant,
  useAssistant,
} from "./assistant";
import { AskField, FOCUS_ASK_FIELD, type FocusAskFieldDetail, useBarKeyLabel } from "./ChatBar";
import { shellChatOn } from "./chatFlag";
import { CommandBar } from "./CommandBar";
import { shellDockOn } from "./dockFlag";
import { focusPageFilter, pageFilterTarget } from "./pageFilter";
import { OPEN_COMMAND_BAR, contextPane, heldPanes } from "./registry";
import { ShellBell } from "./ShellBell";
import { useShellPrefs } from "./shellPrefs";

export { FILL_PAGE_FILTER, OPEN_COMMAND_BAR } from "./registry";

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
   * The bar's first zone: the sidebar's fold control and the organization's
   * logo, when the bar spans the whole width (owner, 2026-10-09). The shell
   * fills it, never an app.
   */
  lead?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [seed, setSeed] = useState("");
  const openWith = useCallback((query: string) => {
    setSeed(query);
    setOpen(true);
  }, []);

  // D99: the chat bar's key. Read once, as the flags below are.
  const [chatOn] = useState(() => shellChatOn());
  // D102: with the chat on, a desktop has ONE field, in the middle of this
  // row (owner, 2026-10-14). ⌘K, `/` and "search everywhere for …" put the
  // cursor there with the results open. The row is above the assistant, so
  // the field shows in every form, and the modal never opens on a desktop.
  // It runs the same engine (`useBarResults`). The phone draws no row, so
  // the phone keeps the modal.
  const unified = chatOn && bar;
  const search = useCallback(
    (query: string) => {
      if (unified) {
        window.dispatchEvent(
          new CustomEvent<FocusAskFieldDetail>(FOCUS_ASK_FIELD, { detail: { query, results: true } }),
        );
        return;
      }
      openWith(query);
    },
    [unified, openWith],
  );

  // ── The one listener ──────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (unified) {
          search("");
          return;
        }
        setSeed("");
        setOpen((o) => !o);
        return;
      }
      if (chatOn && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key === "j" || e.key === "J")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!assistantState().open) {
          openAssistant();
          return;
        }
        const inside = (e.target as HTMLElement | null)?.closest?.("[data-assistant]");
        if (inside) closeAssistant();
        else focusAssistant();
        return;
      }
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        // The page's filter when it has one (opened first if it is closed),
        // the command bar when it has none.
        if (pageFilterTarget()) void focusPageFilter();
        else search("");
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [chatOn, unified, search]);

  useEffect(() => {
    const onOpen = (e: Event) => search((e as CustomEvent<{ query?: string }>).detail?.query ?? "");
    window.addEventListener(OPEN_COMMAND_BAR, onOpen);
    return () => window.removeEventListener(OPEN_COMMAND_BAR, onOpen);
  }, [search]);

  const pathname = usePathname() ?? "/";
  const { access, loading } = useAccess();
  // ⚠️ EVERY held pane, never the pins. The sidebar shows only the pinned
  // apps, so the command bar is how a member opens the rest (owner,
  // 2026-10-11). Fence: `registry.test.ts`.
  const panes = useMemo(
    () => heldPanes(visibleSections(loading ? null : access.features, access.is_admin)),
    [loading, access.features, access.is_admin],
  );
  const here = contextPane(pathname, panes);
  const { data: session } = useSession();
  // The member's preset orders the jobs of the empty bar (NS-7). Off, the
  // shell nav reads nothing and the bar keeps its own order.
  const shell = useShellPrefs();
  // NS-6: the one bell, with its flag on. Read once, as the sidebar reads
  // the bar's flag, because the dev override lives in `localStorage`.
  const [dockOn] = useState(() => shellDockOn());

  return (
    <>
      {/* The phone draws no row: its Menu drawer opens the same bar (§9). */}
      {bar ? (
        <ShellBarRow
          here={here}
          onOpen={() => openWith("")}
          field={unified ? <AskField /> : undefined}
          lead={lead}
          assistant={chatOn ? <ShellAssistantButton /> : null}
          bell={dockOn ? <ShellBell /> : null}
          activity={<ActivityControl />}
        />
      ) : null}
      {children}
      <CommandBar
        open={open}
        seed={seed}
        onClose={() => setOpen(false)}
        panes={panes}
        here={here}
        email={session?.user?.email ?? null}
        jobOrder={shell.enabled ? shell.layout.jobs : undefined}
      />
    </>
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

/**
 * The assistant's button: it opens the side panel, and closes the assistant
 * when it is open. It shows the panel's state, so the member can see what a
 * press will do (owner, 2026-10-14: "I'm not sure when to expect the sidebar
 * or the full screen").
 */
function ShellAssistantButton() {
  const st = useAssistant();
  const key = useBarKeyLabel().replace("K", "J");
  const label = st.open ? `Close ${ASSISTANT_NAME} (${key})` : `Open ${ASSISTANT_NAME} (${key})`;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      icon={st.open ? "PanelRightClose" : "PanelRightOpen"}
      data-assistant-toggle={st.open ? "open" : "closed"}
      aria-label={label}
      title={label}
      aria-pressed={st.open}
      onClick={() => (st.open ? closeAssistant() : openAssistant())}
    />
  );
}

/**
 * The row itself. Exported for `appBar.test.ts`, which renders it and checks
 * that it holds no app name and no slot. Only `ShellFrame` mounts it.
 */
export function ShellBarRow({
  here,
  onOpen,
  field,
  lead,
  assistant,
  bell,
  activity,
}: {
  /** The command bar's scope chip, "in <App>". Its only use of the app. */
  here: { label: string; icon: string } | null;
  onOpen: () => void;
  /**
   * The real field, in the middle (D102: `AskField`, with the chat on). With
   * none, the row draws its fake field, and a press opens the modal.
   */
  field?: ReactNode;
  lead?: ReactNode;
  /** The assistant's open and close button, first of the right group (D99). */
  assistant?: ReactNode;
  /** The shell's one bell (NS-6), before the activity control. */
  bell?: ReactNode;
  /** The shell's own control, the last item of the row. */
  activity?: ReactNode;
}) {
  const shortcut = useShortcutLabel();
  return (
    <header
      data-shell-bar
      className={`flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card ${lead ? "pr-2" : "px-2"}`}
    >
      {lead}
      {/* An empty zone on purpose: it centres the command bar. No app
          renders here (owner, 2026-10-10). */}
      <div aria-hidden className="min-w-0 flex-1 basis-0" />

      {/* With the chat on, the real field (D102). Without it, a fake field,
          on purpose: it looks like the place to type, and a press opens the
          real one. Every member reads it as "search here". */}
      {field ?? (
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
      )}

      {/* The shell's own controls, the same on every page: the assistant,
          the bell, then the activity control. No account (owner,
          2026-10-14). An app's tools sit at the right end of its own title
          bar, never here.
          ⚠️ No `min-w-0` (measured 2026-10-12). The group keeps the width of
          its content, so it never spills over the command bar. Where the
          room is short, the command bar gives way and sits a little off
          centre. With `min-w-0`, a wide control put the bell over the
          command bar at 1024px. Fence: `e2e/shell-account.spec.ts`. */}
      <div className="flex flex-1 basis-0 items-center justify-end gap-1">
        {assistant}
        {bell}
        {activity}
      </div>
    </header>
  );
}
