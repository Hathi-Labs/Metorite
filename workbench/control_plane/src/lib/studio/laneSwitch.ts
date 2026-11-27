/**
 * The lane switcher of a board on a phone (STU-LY-4b, `studio_layouts.md` L3). Pure: no React, no I/O.
 *
 * At 390 px a board page shows ONE lane at a time, and a row of lane tabs picks which. From a tablet up, all
 * lanes show and the tabs are gone. The switch is CSS (`max-sm:`), so the server and the first paint agree.
 * Only a board of a page with the `board` preset asks for it (`phoneSwitch` sets `phone: "switch"` on the node).
 * A card moved with the Move to menu leaves the shown lane, and the live line says where it went.
 *
 * The keyboard move takes the shown lane with it. While a card is held, the lane under the hold shows, and
 * the lane of the held card stays in the page out of sight, so the card keeps focus (`laneMark` "held").
 * After a drop or a refusal, the shown lane is the lane of the card, and the card takes focus there.
 */

/** The lane that shows on a phone: the wanted one when it exists, else the first. `null` for no lane. */
export function activeLane(laneIds: readonly string[], wanted: string | null | undefined): string | null {
  if (laneIds.length === 0) return null;
  return wanted && laneIds.includes(wanted) ? wanted : laneIds[0];
}

/** The lane on screen: the lane under a held card wins over the wanted lane. */
export function shownLane(
  laneIds: readonly string[],
  wanted: string | null | undefined,
  hold: { over: string } | null,
): string | null {
  return activeLane(laneIds, hold ? hold.over : wanted);
}

/**
 * How a phone draws a lane. `shown`: on screen. `held`: out of sight but still focusable, because it holds
 * the card that the keyboard carries (`display: none` would drop focus to the body). `off`: not drawn.
 */
export function laneMark(laneId: string, shown: string | null, hold: { from: string } | null): "shown" | "held" | "off" {
  if (laneId === shown) return "shown";
  return hold?.from === laneId ? "held" : "off";
}

/** The tabs of the switcher: the title and the count of each lane. */
export function laneTabs(lanes: readonly { id: string; title: string; total: number }[]): { id: string; label: string }[] {
  return lanes.map((l) => ({ id: l.id, label: `${l.title} (${l.total})` }));
}

type NodeLike = { props?: { data?: unknown } & Record<string, unknown> } & Record<string, unknown>;

/** The board node with `phone: "switch"` in its data. The input is not changed. A node with no data comes back as it is. */
export function phoneSwitch<T extends NodeLike>(node: T): T {
  const data = node?.props?.data;
  if (typeof data !== "object" || data === null || Array.isArray(data)) return node;
  return { ...node, props: { ...node.props, data: { ...(data as Record<string, unknown>), phone: "switch" } } };
}
