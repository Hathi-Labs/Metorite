"use client";

/**
 * The growing body of a reply composer (owner request, 2026-10-14).
 *
 * One hook and two parts, shared by the inline reply of `EmailDetail` and
 * the `DraftCard` of `ConversationView`:
 *
 * - `useComposerSize` holds the mode (auto, manual, expanded) and measures the
 *   visible height of the reading pane, the `[data-email-thread]` scroller.
 * - `ComposerBody` wraps the composer's own `<textarea>`. It sets the height
 *   of the textarea from `bodySize`, and draws the grip below it.
 * - `ComposerExpandButton` is the Expand / Collapse button of the header.
 *
 * The rules are pure, in `../lib/composerSize.ts`, with their tests.
 *
 * Two things this file keeps from #824 (owner report, 2026-10-10):
 * - The textarea does not hold the wheel (`overscroll-contain` stays off it).
 *   The thread holds it, so a wheel never moves the page.
 * - No `scrollIntoView`. A reveal scrolls the thread only, through
 *   `revealWithin`.
 *
 * The textarea height is set on the element, not through React state. So a
 * keystroke renders this small part again, and never the whole composer.
 * The mirror that measures the text is a hidden textarea with `position:
 * fixed` and no height, so it cannot make the document taller.
 */

import {
  useEffect,
  useLayoutEffect,
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { boundChatScope } from "@/lib/sessions";
import { revealWithin } from "@/lib/scrollWithin";
import {
  EXPANDED_MIN_LINES,
  INITIAL_SIZE,
  bodySize,
  dragResize,
  expandedScrollTop,
  keyResize,
  manualMaxPx,
  minBodyPx,
  paneInnerHeight,
  readComposerHeight,
  sizeReducer,
  writeComposerHeight,
  type SizeAction,
  type SizeState,
} from "../lib/composerSize";

/** The reading-pane scroller that holds every composer. */
const THREAD = "[data-email-thread]";
/** The root of one composer. The caret reveal stays inside it. */
const COMPOSER = "[data-email-composer]";

export interface ComposerSize {
  state: SizeState;
  dispatch: (action: SizeAction) => void;
  /**
   * The visible height of the reading pane inside its padding, in px. 0
   * before the first read.
   */
  paneHeight: number;
  /** A narrow window: no grip, and no kept height. */
  narrow: boolean;
  /** Keep the member's height, or forget it with null. */
  persist: (px: number | null) => void;
  /** The extra classes of the composer root. */
  rootClass: string;
  /** The style of the composer root. Expanded, it is as tall as the pane. */
  rootStyle: CSSProperties | undefined;
}

/**
 * The size of one composer.
 *
 * @param rootRef the composer root. It must carry `data-email-composer`.
 * @param open    the composer shows. A close ends the expanded mode.
 * @param narrow  the narrow layout of the app (`useViewMode().isMobile`).
 */
export function useComposerSize(
  rootRef: RefObject<HTMLElement | null>,
  open: boolean,
  narrow: boolean,
): ComposerSize {
  // The kept height of the member, read at mount. No server render draws a
  // composer, and the height is set on the element, so no markup differs.
  const [mountScope] = useState(() => boundChatScope());
  const loadedScope = useRef<string | null>(mountScope);
  const [state, dispatch] = useReducer(sizeReducer, INITIAL_SIZE, (init) =>
    sizeReducer(init, { type: "load", px: readComposerHeight(mountScope) }));
  const [paneHeight, setPaneHeight] = useState(0);

  useEffect(() => {
    if (!open) {
      dispatch({ type: "close" });
      return;
    }
    // The member scope can bind after the mount. An open then reads the
    // kept height of that member, with no reload.
    const scope = boundChatScope();
    if (scope && scope !== loadedScope.current) {
      loadedScope.current = scope;
      dispatch({ type: "load", px: readComposerHeight(scope) });
    }
    const pane = rootRef.current?.closest<HTMLElement>(THREAD) ?? null;
    const read = () => {
      if (!pane) {
        setPaneHeight(window.innerHeight);
        return;
      }
      // Inside the padding, so an expanded composer shows its bottom border.
      const cs = getComputedStyle(pane);
      setPaneHeight(paneInnerHeight(pane.clientHeight, px(cs.paddingTop), px(cs.paddingBottom)));
    };
    read();
    if (pane && typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(read);
      ro.observe(pane);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
  }, [open, rootRef]);

  // Expand puts the top of the composer at the top of the pane's padding, so
  // the whole composer shows. Collapse keeps it in view. Only the thread
  // scrolls. It compares with the value before, so a mount scrolls nothing.
  const wasExpanded = useRef(state.expanded);
  useEffect(() => {
    if (wasExpanded.current === state.expanded) return;
    wasExpanded.current = state.expanded;
    const root = rootRef.current;
    const pane = root?.closest<HTMLElement>(THREAD);
    if (!root || !pane) return;
    if (!state.expanded) {
      revealWithin(pane, root);
      return;
    }
    const portTop = pane.getBoundingClientRect().top + pane.clientTop;
    const top = expandedScrollTop(
      pane.scrollTop, root.getBoundingClientRect().top, portTop, px(getComputedStyle(pane).paddingTop));
    if (top !== pane.scrollTop) pane.scrollTo({ top });
  }, [state.expanded, rootRef]);

  const expanded = state.expanded && paneHeight > 0;
  return {
    state,
    dispatch,
    paneHeight,
    narrow,
    persist: (px) => writeComposerHeight(boundChatScope(), px),
    // Scroll inside only when the header, the AI panel and the footer need
    // more than the pane. Then the body keeps its minimum, and the footer
    // stays reachable.
    rootClass: expanded ? "flex flex-col overflow-y-auto" : "",
    rootStyle: expanded ? { height: paneHeight } : undefined,
  };
}

/** The computed styles that set where a line of text wraps. */
const MIRRORED = [
  "box-sizing", "font-family", "font-size", "font-weight", "font-style", "font-variant",
  "line-height", "letter-spacing", "word-spacing", "text-transform", "text-indent",
  "tab-size", "white-space", "word-break", "overflow-wrap",
  "padding-top", "padding-bottom", "padding-left", "padding-right",
  "border-top-width", "border-bottom-width", "border-left-width", "border-right-width",
  "border-top-style", "border-bottom-style", "border-left-style", "border-right-style",
];

function px(v: string): number {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function lineHeightOf(cs: CSSStyleDeclaration): number {
  const line = parseFloat(cs.lineHeight);
  return Number.isFinite(line) && line > 0 ? line : px(cs.fontSize) * 1.25;
}

/** A hidden textarea in the shape of `ta`, to measure text without a reflow of `ta`. */
function mirrorOf(ref: RefObject<HTMLTextAreaElement | null>, ta: HTMLTextAreaElement): HTMLTextAreaElement {
  let m = ref.current;
  if (!m) {
    m = document.createElement("textarea");
    m.setAttribute("aria-hidden", "true");
    m.tabIndex = -1;
    m.readOnly = true;
    Object.assign(m.style, {
      position: "fixed", top: "0", left: "0", height: "0", minHeight: "0", maxHeight: "none",
      visibility: "hidden", overflow: "hidden", pointerEvents: "none", resize: "none", zIndex: "-1",
    });
    document.body.appendChild(m);
    ref.current = m;
  }
  const cs = getComputedStyle(ta);
  for (const p of MIRRORED) m.style.setProperty(p, cs.getPropertyValue(p));
  m.style.width = `${ta.offsetWidth}px`;
  return m;
}

/**
 * Keep the place of the member in view after the body changed height. In the
 * textarea that is the caret. In the AI panel or the footer it is the
 * focused control. The thread scrolls, and no other box.
 */
function keepFocusInView(ta: HTMLTextAreaElement, mirror: HTMLTextAreaElement, value: string) {
  const pane = ta.closest<HTMLElement>(THREAD);
  const active = document.activeElement;
  if (!pane || !active) return;
  if (active === ta) {
    const cs = getComputedStyle(ta);
    const line = lineHeightOf(cs);
    mirror.value = value.slice(0, ta.selectionEnd ?? value.length);
    const caretBottom = mirror.scrollHeight - px(cs.paddingBottom);
    const top = ta.getBoundingClientRect().top + px(cs.borderTopWidth) + caretBottom - line - ta.scrollTop;
    revealWithin(pane, { getBoundingClientRect: () => ({ top, bottom: top + line }) });
    return;
  }
  const root = ta.closest(COMPOSER);
  if (root && root.contains(active)) revealWithin(pane, active);
}

/**
 * The body of a composer: the composer's own `<textarea>` as the child, with
 * the grip below it. The textarea keeps its props, its keys and its class.
 */
export function ComposerBody({
  size,
  value,
  label = "Resize the draft",
  className = "",
  children,
}: {
  size: ComposerSize;
  /** The text of the textarea. A change measures the body again. */
  value: string;
  /** The accessible name of the grip. */
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLTextAreaElement | null>(null);
  const [heightPx, setHeightPx] = useState(0);
  const [limits, setLimits] = useState({ min: 0, max: 0 });
  const [width, setWidth] = useState(0);
  // The composer around the body: the header, the recipients, the AI panel
  // and the footer. It caps a dragged height, so Send stays in the pane.
  const [chromePx, setChromePx] = useState(0);
  const { state, paneHeight, narrow } = size;

  // A change of width wraps the text again. A change of the composer (the AI
  // panel opens, Cc shows) changes the room that is left for the body.
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const root = wrap.closest<HTMLElement>(COMPOSER);
    const read = () => {
      setWidth(wrap.clientWidth);
      const ta = wrap.querySelector("textarea");
      if (root && ta) setChromePx(Math.max(0, Math.round(root.offsetHeight - ta.offsetHeight)));
    };
    const ro = new ResizeObserver(read);
    ro.observe(wrap);
    if (root) ro.observe(root);
    return () => ro.disconnect();
  }, []);

  useEffect(() => () => {
    mirrorRef.current?.remove();
    mirrorRef.current = null;
  }, []);

  useLayoutEffect(() => {
    const ta = wrapRef.current?.querySelector("textarea");
    if (!ta) return;
    const cs = getComputedStyle(ta);
    const line = lineHeightOf(cs);
    const chrome = px(cs.paddingTop) + px(cs.paddingBottom) + px(cs.borderTopWidth) + px(cs.borderBottomWidth);
    const minPx = minBodyPx(line, chrome);
    const pane = paneHeight || window.innerHeight;
    if (state.expanded) {
      // Flex gives the body the room that the rest of the composer leaves.
      ta.style.height = "";
      ta.style.flex = "1 1 0%";
      ta.style.minHeight = `${minBodyPx(line, chrome, EXPANDED_MIN_LINES)}px`;
      ta.style.overflowY = "auto";
      setHeightPx(Math.round(ta.offsetHeight));
      return;
    }
    const mirror = mirrorOf(mirrorRef, ta);
    mirror.value = value;
    const contentPx = mirror.scrollHeight + px(cs.borderTopWidth) + px(cs.borderBottomWidth);
    const next = bodySize(state, { contentPx, minPx, paneHeight: pane, narrow, chromePx });
    const before = ta.offsetHeight;
    ta.style.flex = "";
    ta.style.minHeight = "";
    ta.style.height = `${next.heightPx}px`;
    ta.style.overflowY = next.scrolls ? "auto" : "hidden";
    setHeightPx(next.heightPx);
    setLimits({ min: minPx, max: manualMaxPx(pane, minPx, chromePx) });
    if (Math.abs(next.heightPx - before) >= 1) keepFocusInView(ta, mirror, value);
  }, [value, state, paneHeight, narrow, width, chromePx]);

  const showGrip = !narrow && !state.expanded;
  return (
    <div
      ref={wrapRef}
      data-composer-body=""
      // No `min-h-0` here. The body keeps the minimum of its textarea, so a
      // tall AI panel makes the composer scroll, and the body never paints
      // over the AI panel (review round 1, P1).
      className={`relative ${state.expanded ? "flex flex-1 flex-col" : ""} ${className}`}
    >
      {children}
      {showGrip && (
        <ResizeGrip
          wrapRef={wrapRef}
          size={size}
          heightPx={heightPx}
          min={limits.min}
          max={limits.max}
          label={label}
          onLive={setHeightPx}
        />
      )}
    </div>
  );
}

/**
 * A key on the grip. ArrowUp and ArrowDown step the height, PageUp and
 * PageDown step it further, and Home and End go to the limits. The member's
 * height is kept. Any other key passes on, Escape too.
 */
export function handleGripKey(
  e: { key: string; preventDefault(): void; stopPropagation(): void },
  size: Pick<ComposerSize, "dispatch" | "persist">,
  heightPx: number,
  min: number,
  max: number,
): void {
  const next = keyResize(e.key, heightPx, min, max);
  if (next === null) return;
  // The page shortcuts never see a key that the grip used.
  e.preventDefault();
  e.stopPropagation();
  size.dispatch({ type: "resize", px: next });
  size.persist(next);
}

/** The grip on the bottom edge of the body. */
function ResizeGrip({
  wrapRef,
  size,
  heightPx,
  min,
  max,
  label,
  onLive,
}: {
  wrapRef: RefObject<HTMLDivElement | null>;
  size: ComposerSize;
  heightPx: number;
  min: number;
  max: number;
  label: string;
  onLive: (px: number) => void;
}) {
  const drag = useRef<{ y: number; start: number; px: number } | null>(null);
  const textarea = () => wrapRef.current?.querySelector("textarea") ?? null;
  const set = (next: number) => {
    size.dispatch({ type: "resize", px: next });
    size.persist(next);
  };
  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuenow={heightPx}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title="Drag to resize. Double-click to fit the text."
      data-composer-grip=""
      onPointerDown={(e) => {
        // A right-click never starts a drag.
        if (e.button !== 0) return;
        const ta = textarea();
        if (!ta) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        const start = ta.offsetHeight;
        drag.current = { y: e.clientY, start, px: start };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        const ta = textarea();
        if (!d || !ta) return;
        // The drag moves the element only. The reducer hears the end, so a
        // drag never renders the whole composer at each frame.
        d.px = dragResize(d.start, e.clientY - d.y, min, max);
        ta.style.height = `${d.px}px`;
        ta.style.overflowY = "auto";
        onLive(d.px);
      }}
      onPointerUp={(e) => {
        const d = drag.current;
        if (!d) return;
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        if (d.px !== d.start) set(d.px);
      }}
      onPointerCancel={() => {
        drag.current = null;
      }}
      onDoubleClick={() => {
        size.dispatch({ type: "reset" });
        size.persist(null);
      }}
      onKeyDown={(e) => handleGripKey(e, size, heightPx, min, max)}
      className="flex h-3 cursor-ns-resize touch-none select-none items-center justify-center text-muted-foreground tech-transition hover:bg-secondary/60 hover:text-foreground focus-visible:bg-secondary/60 focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <Icon name="GripHorizontal" size={12} aria-hidden />
    </div>
  );
}

/** The Expand / Collapse button of the composer header. */
export function ComposerExpandButton({
  expanded,
  onToggle,
}: {
  expanded: boolean;
  onToggle: () => void;
}) {
  const label = expanded ? "Collapse" : "Expand";
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      icon={expanded ? "Minimize2" : "Maximize2"}
      aria-label={label}
      title={expanded ? "Collapse the draft" : "Expand the draft to fill the pane"}
      data-composer-expand={expanded ? "on" : "off"}
      onClick={onToggle}
    />
  );
}
