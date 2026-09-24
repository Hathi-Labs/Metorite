/**
 * Whether the side panel may open beside a board (WS-27bm S8 visual review).
 *
 * The Projects page lays out, left to right: the project tree, the side
 * panel, the board and the docked chat. At 1440 wide with the chat docked, a
 * document opened in the side panel squeezed the board to about 50px. The
 * board's tabs and filters then drew over the chat. Two rules fix that:
 *
 * 1. The board never draws outside its own column (`min-w-0` and
 *    `overflow-hidden` on the page's `<main>`).
 * 2. The board keeps {@link BOARD_MIN_REM} of room. When the row cannot hold
 *    the tree, the panel, that minimum and the dock, a document opens in the
 *    full-screen viewer (`ArtifactViewerModal`) instead of the side panel.
 *
 * This module is the decision (pure, and tested in `sidePanelFit.test.ts`)
 * and the context that carries it to the chat. A surface with no provider,
 * such as `/chat`, gets `true`: its own layout has no board to protect.
 */
import { createContext, useContext } from "react";

/** The narrowest board a member can still use: its tabs and one column. */
export const BOARD_MIN_REM = 32;

export interface SidePanelFitInput {
  /** The width of the row that holds the tree, the panel, the board and the dock. */
  rowWidth: number;
  /** The tree's width, 0 when it is closed. */
  navWidth: number;
  /** The docked chat's width, 0 when it is not on screen. */
  dockWidth: number;
  /** The side panel's width (`sidePanelStore`'s `width`). */
  panelWidth: number;
  /** {@link BOARD_MIN_REM} in pixels, at the member's density. */
  boardMinPx: number;
}

/** True when the board keeps its minimum width with the side panel open. */
export function sidePanelFits(input: SidePanelFitInput): boolean {
  const { rowWidth, navWidth, dockWidth, panelWidth, boardMinPx } = input;
  // An unmeasured row (0 before the first layout) is not a narrow one. Say
  // yes, and let the first measurement decide.
  if (!(rowWidth > 0)) return true;
  return rowWidth - navWidth - dockWidth - panelWidth >= boardMinPx;
}

export const SidePanelFitContext = createContext<boolean>(true);

/** Whether a document may open in the side panel here. */
export function useSidePanelFits(): boolean {
  return useContext(SidePanelFitContext);
}
