"use client";

/**
 * The sidebar's fold state, with ONE owner (`navigation_shell.md` §3.1, §3.2).
 *
 * The rules are pure functions in `lib/sidebarFold.ts`. This file holds the
 * state those rules act on: folded or not, the arm, the pulse and the tip.
 *
 * Why a provider. Until 2026-10-09 the sidebar owned all of it, because the
 * fold control sat in the sidebar's head. The owner then moved the control and
 * the organization's logo into one bar across the top, so the logo stays in
 * view when the sidebar is folded. The control and the rail now live in two
 * components. Two copies of "is it folded" would drift, so the state lives
 * here and both read it.
 *
 * `placement` says where the control is:
 *   - `rail` — in the sidebar's head, as before (the shell nav or the shell
 *     bar is off). The markup there is the one this sidebar always had.
 *   - `bar` — at the left end of the full-width shell bar. The sidebar has no
 *     head, and the tip points up at the bar's button.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
  type RefObject,
} from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  HINT_DISMISS_MS,
  isPlainNavClick,
  readCollapsed,
  setAutoFoldEnabled,
  takeHint,
  writeCollapsed,
} from "@/lib/sidebarFold";

export type FoldPlacement = "rail" | "bar";

export interface SidebarFold {
  placement: FoldPlacement;
  collapsed: boolean;
  /** Whether a sidebar link armed the fold. A ref: arming must not re-render. */
  armedRef: RefObject<boolean>;
  /** Every link in the rail calls this. Only a plain click arms. */
  arm: (e: MouseEvent) => void;
  /** The member's own toggle. It wins over the fold until the next app. */
  toggle: () => void;
  /** The fold while you work: fold, pulse the control, and maybe show the tip. */
  foldForWork: () => void;
  /** Counts folds. It keys the control, so a second fold restarts the pulse. */
  beacon: number;
  pulsing: boolean;
  tipOpen: boolean;
  closeTip: () => void;
  /** "Keep it open": the fold stops, and the sidebar opens. */
  keepOpen: () => void;
}

const FoldContext = createContext<SidebarFold | null>(null);

export function useSidebarFold(): SidebarFold {
  const fold = useContext(FoldContext);
  if (!fold) throw new Error("useSidebarFold needs a SidebarFoldProvider (AppShell mounts one)");
  return fold;
}

export function SidebarFoldProvider({
  placement,
  children,
}: {
  placement: FoldPlacement;
  children: ReactNode;
}) {
  // ⚠️ Read in the initializer, not in an effect. AppShell mounts this
  // provider only after access resolves, on the client, so there is no server
  // markup to disagree with. An effect would draw the open rail and then
  // animate it shut on every reload.
  const [collapsed, setCollapsedState] = useState(() =>
    typeof window === "undefined" ? false : readCollapsed(),
  );
  const armedRef = useRef(false);
  // `pulsing` says whether this fold's pulse still runs. It ends by itself,
  // and on the member's own toggle, so a later manual collapse does not pulse
  // and the reduced-motion tint does not stay.
  const [beacon, setBeacon] = useState(0);
  const [pulsing, setPulsing] = useState(false);
  const [tipOpen, setTipOpen] = useState(false);

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next);
    writeCollapsed(next);
  }, []);

  const toggle = useCallback(() => {
    armedRef.current = false;
    setTipOpen(false);
    setPulsing(false);
    setCollapsed(!collapsed);
  }, [collapsed, setCollapsed]);

  const arm = useCallback((e: MouseEvent) => {
    if (isPlainNavClick(e)) armedRef.current = true;
  }, []);

  const foldForWork = useCallback(() => {
    setCollapsed(true);
    setBeacon((n) => n + 1);
    setPulsing(true);
    if (takeHint()) setTipOpen(true);
  }, [setCollapsed]);

  const closeTip = useCallback(() => setTipOpen(false), []);
  const keepOpen = useCallback(() => {
    setAutoFoldEnabled(false);
    setTipOpen(false);
    setCollapsed(false);
  }, [setCollapsed]);

  // The pulse runs three times (`.sidebar-beacon`, about 3.7s), then ends.
  useEffect(() => {
    if (!pulsing) return;
    const timer = setTimeout(() => setPulsing(false), 4000);
    return () => clearTimeout(timer);
  }, [pulsing, beacon]);

  // The tip closes by itself, and on Escape.
  useEffect(() => {
    if (!tipOpen) return;
    const timer = setTimeout(() => setTipOpen(false), HINT_DISMISS_MS);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTipOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
    };
  }, [tipOpen]);

  const value = useMemo<SidebarFold>(
    () => ({
      placement,
      collapsed,
      armedRef,
      arm,
      toggle,
      foldForWork,
      beacon,
      pulsing,
      tipOpen,
      closeTip,
      keepOpen,
    }),
    [placement, collapsed, arm, toggle, foldForWork, beacon, pulsing, tipOpen, closeTip, keepOpen],
  );

  return <FoldContext.Provider value={value}>{children}</FoldContext.Provider>;
}

/**
 * The fold control, and its tip. One component for both places.
 *
 * In the rail it is the chevron the sidebar head always had, with the same
 * markup. In the bar it is the `Menu` glyph (Gmail's pattern), because a
 * chevron at the top of the window points at nothing, and a panel glyph
 * would copy the apps' own rail toggles beside it.
 */
export function SidebarFoldButton() {
  const fold = useSidebarFold();
  const anchorRef = useRef<HTMLElement>(null);
  const label = fold.collapsed ? "Expand sidebar" : "Collapse sidebar";
  const beaconClass = fold.collapsed && fold.pulsing ? "sidebar-beacon" : "";

  return (
    <>
      {/* `key` restarts the pulse on each fold. Zero means no fold yet. */}
      {fold.placement === "bar" ? (
        // `data-sidebar-fold`: the fold's own control is never "a menu is
        // open" (`floatingOpen`), though it is expanded outside the rail.
        <span ref={anchorRef} key={fold.beacon} data-sidebar-fold className="inline-flex shrink-0">
          <Button
            variant="ghost"
            size="icon-sm"
            // ⚠️ `Menu`, not a panel glyph (coordinator review, 2026-10-09).
            // An app's own rail toggle (Projects' tree, My Tasks' lists) sits
            // in the same bar with PanelLeftOpen/Close. Two look-alike
            // controls that do different jobs make a member stop and guess.
            // One glyph for both states. `aria-expanded` carries the state.
            icon="Menu"
            onClick={fold.toggle}
            className={beaconClass}
            title={label}
            aria-label={label}
            aria-expanded={!fold.collapsed}
          />
        </span>
      ) : (
        <button
          ref={anchorRef as RefObject<HTMLButtonElement>}
          key={fold.beacon}
          onClick={fold.toggle}
          className={`shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition ${beaconClass}`}
          title={label}
          aria-label={label}
          aria-expanded={!fold.collapsed}
        >
          {fold.collapsed ? <Icon name="ChevronRight" size={16} /> : <Icon name="ChevronLeft" size={16} />}
        </button>
      )}
      {fold.tipOpen && fold.collapsed && (
        <FoldTip
          anchorRef={anchorRef}
          placement={fold.placement}
          onClose={fold.closeTip}
          onKeepOpen={fold.keepOpen}
        />
      )}
    </>
  );
}

/**
 * The tip beside the expand control, on the first three folds (`HINT_LIMIT`).
 *
 * It names the control, so the member learns where the sidebar went, and it
 * offers the way out. `position: fixed`, so no `overflow-hidden` clips it.
 * Measured in a layout effect, because the control remounts on the fold (its
 * `key` restarts the pulse) and the ref is current only after commit.
 *
 * In the rail it sits to the right of the control, top-aligned. In the bar it
 * sits under the control, with the caret pointing up at it, because to the
 * right is the organization's logo.
 */
function FoldTip({
  anchorRef,
  placement,
  onClose,
  onKeepOpen,
}: {
  anchorRef: RefObject<HTMLElement | null>;
  placement: FoldPlacement;
  onClose: () => void;
  onKeepOpen: () => void;
}) {
  const [box, setBox] = useState<{ top: number; left: number; caret: number } | null>(null);
  useLayoutEffect(() => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    if (placement === "bar") {
      const left = Math.max(8, rect.left - 4);
      setBox({ top: rect.bottom + 8, left, caret: rect.left + rect.width / 2 - left });
      return;
    }
    // ⚠️ Top-aligned with the button, never centred on it. The button sits
    // near the top of the window, so a centred tip ran off the top and hid
    // its title. 56px rail plus a 12px gap: the rail is that width now.
    const top = Math.max(8, rect.top - 6);
    setBox({ top, left: 68, caret: rect.top + rect.height / 2 - top });
  }, [anchorRef, placement]);
  if (box === null) return null;
  const below = placement === "bar";
  return (
    <div
      role="status"
      data-testid="sidebar-fold-tip"
      style={{ top: box.top, left: box.left }}
      className="sidebar-tip fixed z-[60] w-64 rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-lg"
    >
      <span
        aria-hidden
        style={below ? { left: box.caret } : { top: box.caret }}
        className={
          below
            ? "absolute -top-[5px] h-2.5 w-2.5 -translate-x-1/2 rotate-45 border-l border-t border-border bg-popover"
            : "absolute -left-[5px] h-2.5 w-2.5 -translate-y-1/2 rotate-45 border-b border-l border-border bg-popover"
        }
      />
      <div className="flex items-start gap-2">
        {/* The glyph of the control the tip names, so the member knows it. */}
        <Icon name={below ? "Menu" : "PanelLeftClose"} size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold">Sidebar folded</div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {below
              ? "The app has more room now. Select the sidebar button at the top left to open it again."
              : "The app has more room now. Select the arrow button to open the sidebar again."}
          </p>
        </div>
      </div>
      <div className="mt-2.5 flex justify-end gap-1.5">
        <Button variant="ghost" size="sm" onClick={onKeepOpen}>
          Keep it open
        </Button>
        <Button variant="secondary" size="sm" onClick={onClose}>
          Got it
        </Button>
      </div>
    </div>
  );
}
