"use client";

/**
 * The horizontal scroller of a Kanban board (WS-53 CRM-C2,
 * `crm_platform.md` §20.5). Its children are `BoardColumn` lanes.
 *
 * Two ways to scroll, because two pages hold a board two ways:
 *
 * - `own` (My Tasks, the CRM): the board fills the height it is given and
 *   scrolls across by itself. Each lane takes that height, and its body
 *   scrolls down.
 * - `parent` (Projects): the page's canvas scrolls both ways, so the board
 *   is as wide as its lanes and as tall as the longest one.
 *
 * Extra props (`tabIndex`, `onKeyDown`, `aria-label`) land on the scroller, so
 * a board's keyboard cursor keeps its focus target.
 */

import type React from "react";

export function Board({
  scroll = "own",
  className = "",
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { scroll?: "own" | "parent" }) {
  return (
    <div
      {...rest}
      className={[
        "flex gap-3 p-3",
        scroll === "own" ? "h-full min-h-0 overflow-x-auto" : "w-max min-w-full",
        className,
      ].join(" ")}
    >
      {children}
    </div>
  );
}
