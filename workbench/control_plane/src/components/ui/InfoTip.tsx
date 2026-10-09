"use client";

/**
 * A small "i" button that opens help text — the one help control (WS-41
 * I-10b, `project_import.md` §7.7).
 *
 * Owner directive, 2026-10-09: "wherever required, just have tooltips or
 * help somewhere so that we can give a good explanation about what stage and
 * status are". A native `title` does not do that. It shows nothing on a
 * phone, and nothing to a keyboard. This control opens on a click, a tap,
 * Enter or Space, and Escape or a click outside closes it.
 *
 * ⚠️ **Built from the primitives, never from the substrate.** The trigger is
 * a ghost `Button` (`size="icon-xs"`, `icon="Info"`), so it carries
 * `.cc-control` (`AGENTS.md` rules 3 and 8). The panel is `AnchoredPanel`,
 * and the dismiss walk is `outsideClick.shouldDismiss`, as `SelectButton`
 * does. Nothing here imports `@base-ui/react`.
 *
 * ⚠️ The vitest setup has no DOM, so the behaviour is a pure reducer
 * (`infoTipReducer`) and the markup is checked with `renderToStaticMarkup`.
 */
import { useCallback, useEffect, useId, useReducer, useRef, useState } from "react";

import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

export interface InfoTipState {
  open: boolean;
}

export type InfoTipAction =
  | { type: "click" }
  | { type: "key"; key: string }
  | { type: "outside"; dismiss: boolean }
  | { type: "close" };

/** Enter and Space toggle, as a click does. Escape closes. */
export function infoTipReducer(state: InfoTipState, action: InfoTipAction): InfoTipState {
  switch (action.type) {
    case "click":
      return { open: !state.open };
    case "key":
      if (action.key === "Enter" || action.key === " ") return { open: !state.open };
      if (action.key === "Escape") return { open: false };
      return state;
    case "outside":
      return action.dismiss ? { open: false } : state;
    case "close":
      return { open: false };
  }
}

/** The widest the panel draws, in px: 20rem at the default text size. */
export const INFO_TIP_WIDTH = 320;

/**
 * How the panel hangs, so it stays inside the viewport at 390 px. It hangs
 * from the side with more room, and it never grows wider than that room.
 */
export function infoTipFit(
  rect: { left: number; right: number },
  viewportWidth: number,
): { align: "start" | "end"; width: number } {
  const roomRight = viewportWidth - rect.left - 8;
  const roomLeft = rect.right - 8;
  const align = roomRight >= INFO_TIP_WIDTH || roomRight >= roomLeft ? "start" : "end";
  return { align, width: Math.max(160, Math.min(INFO_TIP_WIDTH, align === "start" ? roomRight : roomLeft)) };
}

export interface InfoTipProps {
  /** What the help is about, for assistive technology: "Status and stage". */
  label: string;
  /** A bold first line in the panel. Absent: the panel holds only the text. */
  title?: string;
  children?: React.ReactNode;
  /** Starts open. For a test render only. */
  defaultOpen?: boolean;
}

export function InfoTip({ label, title, children, defaultOpen = false }: InfoTipProps) {
  const [{ open }, dispatch] = useReducer(infoTipReducer, { open: defaultOpen });
  const root = useRef<HTMLSpanElement | null>(null);
  const [anchor, setAnchor] = useState<HTMLSpanElement | null>(null);
  const panelId = useId();
  // Stable, so React does not detach and attach it on every render: a ref
  // callback that sets state would then render again and again.
  const attach = useCallback((node: HTMLSpanElement | null) => {
    root.current = node;
    setAnchor(node);
  }, []);
  const [fit, setFit] = useState<{ align: "start" | "end"; width: number }>({
    align: "start",
    width: INFO_TIP_WIDTH,
  });

  // Measured as it opens, in the handler, so no effect sets state.
  const act = (action: InfoTipAction) => {
    if (!open && anchor && infoTipReducer({ open }, action).open) {
      setFit(infoTipFit(anchor.getBoundingClientRect(), window.innerWidth));
    }
    dispatch(action);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) =>
      dispatch({
        type: "outside",
        dismiss: shouldDismiss(event.target as Element | null, domClickWalk(root.current)),
      });
    // ⚠️ In the CAPTURE phase on `window`, and stopped there. A tip inside a
    // `Modal` (the import wizard) otherwise lets Escape reach the dialog too,
    // and one key closed the whole wizard (measured in the I-10b walk).
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      event.preventDefault();
      dispatch({ type: "key", key: "Escape" });
      root.current?.querySelector("button")?.focus();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <span ref={attach} className="inline-flex align-middle">
      <Button
        variant="ghost"
        size="icon-xs"
        icon="Info"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => act({ type: "click" })}
        onKeyDown={(event) => {
          // Handled here, so the native click does not toggle a second time.
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            act({ type: "key", key: event.key });
          }
        }}
      />
      <AnchoredPanel
        anchor={anchor}
        open={open}
        align={fit.align}
        maxHeight={360}
        className="p-3 text-xs"
        panelProps={{ id: panelId, role: "note", "aria-label": label }}
      >
        <div style={{ width: fit.width }} className="space-y-1.5 text-foreground">
          {title ? <p className="font-semibold">{title}</p> : null}
          {children}
        </div>
      </AnchoredPanel>
    </span>
  );
}

export default InfoTip;
