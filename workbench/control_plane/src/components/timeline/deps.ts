/**
 * Timeline block · dependency arrows.
 *
 * Moved from `app/projects/lib/timeline.ts` (STU-D6). Path geometry only. The
 * rule for which arrow is a conflict stays in the app, because it reads task
 * fields (D-PM-12).
 */

import { type Bar, ROW_H } from "./geometry";

/**
 * The elbow path from one bar's right edge to another's left edge.
 *
 * Elbowed rather than straight, and routed OUT of the source before turning,
 * because a straight diagonal across six rows crosses every bar between them
 * and stops being followable at exactly the density where you need it.
 *
 * Returns `null` when either end has no bar: an arrow to a task with no dates
 * has nowhere to land, and drawing it to the row's left margin would invent a
 * date the task does not have.
 */
export interface Point {
  x: number;
  y: number;
}

/** How much of each corner is rounded away, in pixels. */
export const CORNER_R = 7;

/** The point `d` pixels from `a` along the line towards `b`. */
function towards(a: Point, b: Point, d: number): Point {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return { x: a.x, y: a.y };
  return { x: a.x + (dx / len) * d, y: a.y + (dy / len) * d };
}

/**
 * A polyline drawn with ROUNDED corners.
 *
 * Right-angle joins are what make a dense dependency graph unreadable: every
 * corner is a hard visual stop, so six arrows crossing look like a circuit
 * diagram and the eye cannot follow any single one of them. A fillet keeps the
 * line continuous, and continuity is the whole thing a dependency arrow has to
 * express.
 *
 * Quadratic Béziers with the corner itself as the control point, rather than
 * SVG arcs: the curve is tangent to both segments by construction, so there is
 * no radius/sweep arithmetic to get wrong and no case where a arc flips
 * direction on a near-straight join.
 *
 * ⚠️ **The radius is clamped to HALF the shorter adjacent segment.** Without
 * that, a corner near the end of a short leg eats past the next corner and the
 * path folds back on itself — which happens constantly here, because the stubs
 * either side of a bar are deliberately short.
 */
export function roundedPath(
  points: readonly Point[],
  radius = CORNER_R,
): string {
  if (points.length < 2) return "";
  const first = points[0] as Point;
  const out: string[] = [`M ${round(first.x)} ${round(first.y)}`];

  for (let i = 1; i < points.length - 1; i += 1) {
    const prev = points[i - 1] as Point;
    const corner = points[i] as Point;
    const next = points[i + 1] as Point;
    const r = Math.min(
      radius,
      Math.hypot(corner.x - prev.x, corner.y - prev.y) / 2,
      Math.hypot(next.x - corner.x, next.y - corner.y) / 2,
    );
    // A vertex that does not turn needs no fillet. The curve would be visually
    // straight — a quadratic whose control point is on the line IS the line —
    // but it is path data describing nothing, and this is a general helper.
    const turn =
      (corner.x - prev.x) * (next.y - corner.y) -
      (corner.y - prev.y) * (next.x - corner.x);
    if (r <= 0.5 || Math.abs(turn) < 1e-6) {
      out.push(`L ${round(corner.x)} ${round(corner.y)}`);
      continue;
    }
    const enter = towards(corner, prev, r);
    const leave = towards(corner, next, r);
    out.push(`L ${round(enter.x)} ${round(enter.y)}`);
    out.push(
      `Q ${round(corner.x)} ${round(corner.y)} ${round(leave.x)} ${round(leave.y)}`,
    );
  }

  const last = points[points.length - 1] as Point;
  out.push(`L ${round(last.x)} ${round(last.y)}`);
  return out.join(" ");
}

/** Half-pixel precision — enough for a 1.5px stroke, and keeps `d` readable. */
function round(n: number): number {
  return Math.round(n * 2) / 2;
}

/**
 * The corners an edge turns, before rounding.
 *
 * Separated from the path string so the ROUTING can be asserted as geometry —
 * "it leaves the source rightwards and arrives at the target leftwards" — and
 * the rounding as a shape. Testing them together means testing a `d` attribute,
 * which is how a geometry test becomes a change-detector.
 */
export function edgePoints(
  from: { bar: Bar | null; row: number },
  to: { bar: Bar | null; row: number },
): Point[] | null {
  if (!from.bar || !to.bar) return null;
  const y1 = from.row * ROW_H + ROW_H / 2;
  const y2 = to.row * ROW_H + ROW_H / 2;
  const x1 = from.bar.leftPx + from.bar.widthPx;
  const x2 = to.bar.leftPx;
  const stub = EDGE_STUB_PX;

  // Same row with room ahead: a straight line. A dogleg between two bars on
  // one line is a corner drawn for nothing.
  if (y1 === y2 && x2 > x1) {
    return [{ x: x1, y: y1 }, { x: x2, y: y2 }];
  }

  // ⚠️ The bars TOUCH — the blocked task starts the day the blocker ends, which
  // is the commonest dependency there is. A drop straight down is the whole
  // picture. Owner report, 2026-09-05.
  //
  // This used to fall through to the detour below, because the detour's test
  // was "is there room for two stubs" rather than "does the target start
  // before the source ends". With no room the detour turned right 12px, ran
  // back left past its own start, and dropped in — a squiggle drawn to avoid
  // a conflict that is not there.
  if (x2 === x1) {
    return [{ x: x1, y: y1 }, { x: x2, y: y2 }];
  }

  // Room to route forwards: out, across, in. The vertical takes the midpoint
  // between the two bars, which is `stub` clear of both whenever the gap is
  // wide enough to be, and lands as a short neat drop when it is not.
  if (x2 > x1) {
    const mid = (x1 + x2) / 2;
    return [
      { x: x1, y: y1 },
      { x: mid, y: y1 },
      { x: mid, y: y2 },
      { x: x2, y: y2 },
    ];
  }

  // The blocked bar starts BEFORE the blocker ends — the conflict case, and
  // the one a naive path draws backwards through both bars. Route out, into
  // the gap between the rows, back, and DOWN into the target from above. It
  // stays followable while it is wrong, which is when it matters most.
  //
  // ⚠️ It used to overshoot to `x2 - stub` and hook back rightwards, so it
  // could arrive from the left like every other edge. On a one-day overlap
  // that hook is 12px wide between two 20px drops, and the fillets eat all
  // three: the reader gets a knot under the bar. Owner report, 2026-09-05.
  //
  // Arriving from ABOVE is the rule the short cases already follow — the
  // touching case above drops straight down — so this is one rule, not two:
  // an edge arrives from the left when there is room ahead, and from above
  // when there is not.
  const lane =
    y1 === y2 ? y1 + ROW_H / 2 : (y1 + y2) / 2;
  return [
    { x: x1, y: y1 },
    { x: x1 + stub, y: y1 },
    { x: x1 + stub, y: lane },
    { x: x2, y: lane },
    { x: x2, y: y2 },
  ];
}

/**
 * ── STAGGERING: two arrows must not share one corridor ────────────────────
 *
 * `edgePoints` routes each edge on its own, and its vertical run sits at the
 * midpoint between the two bars. Two edges with similar endpoints therefore
 * get near-identical midpoints and draw their vertical runs ON TOP of one
 * another — one line hides the other, and where they diverge the pair reads as
 * a single line that forks for no reason. Owner report, 2026-09-05.
 *
 * The fix is the standard one for orthogonal edge routing: a corridor is a set
 * of parallel CHANNELS, and colliding runs each take their own. Nothing about
 * an individual edge changes; what changes is that they are placed with
 * knowledge of each other, which routing one at a time cannot do.
 *
 * Two kinds of run collide, and both are handled:
 *
 *  - the **vertical** x of a forward route (4 points), and
 *  - the **horizontal** lane y of a backward route (6 points), which is the
 *    conflict case, and exactly where several arrows converge.
 *
 * Centred on the group, so a lone edge never moves and a pair splits evenly
 * either side of where it would have been. Deterministic: the order is the
 * caller's, so the same board always draws the same way.
 */
export const CHANNEL_PX = 7;

/** How far clear of a bar a route turns. One number, read by the router and
 *  by the trunk below, so the two cannot disagree about where a corner goes. */
export const EDGE_STUB_PX = 12;


export interface RoutedEdge {
  id: string;
  points: Point[];
}

/** Consecutive runs of values within `CHANNEL_PX` of each other. */
function cluster<T>(items: T[], value: (item: T) => number): T[][] {
  const sorted = [...items].sort((a, b) => value(a) - value(b));
  const groups: T[][] = [];
  for (const item of sorted) {
    const last = groups[groups.length - 1];
    if (last && Math.abs(value(item) - value(last[last.length - 1])) < CHANNEL_PX) {
      last.push(item);
    } else {
      groups.push([item]);
    }
  }
  return groups;
}

/**
 * The same edges, with colliding runs spread into parallel channels.
 *
 * Never reorders the result and never changes an endpoint — an arrow still
 * leaves the same bar and still arrives at the same one. Only the middle moves.
 */
export function stagger(routes: readonly RoutedEdge[]): RoutedEdge[] {
  const moved = new Map<string, Point[]>();
  /**
   * ── The TRUNK: arrows leaving one bar branch, they do not weave ───────────
   *
   * Owner report, 2026-09-05, second round: the branches "don't look neat" and
   * the arrows leaving one bar cross each other. The shared first leg is fine —
   * that was checked. The tangle is the fault.
   *
   * `edgePoints` turns each edge down at the MIDPOINT between its two bars, and
   * that midpoint comes from the target's X. Two arrows leaving one bar
   * therefore run along the same y and turn down at two different places, so
   * the further one's horizontal passes straight through the nearer one's
   * corner. Nesting the channels does not help: they were already nested in the
   * reported case, and the junction is what reads as a crossing.
   *
   * A fan is a TREE, so it is drawn as one: siblings share a single trunk just
   * clear of the bar they leave, and each peels off right at its own row. One
   * line down, one branch per target, and nothing crosses anything — the
   * shorter branch's trunk is contained in the longer one's rather than meeting
   * it at an angle.
   *
   * A LONE arrow keeps the balanced midpoint, which is the better shape when
   * there is nothing to branch with. So the two routings are not a mixture:
   * one edge is a line, several from one bar are a tree.
   */
  const trunked = new Set<string>();
  const bySource = new Map<string, RoutedEdge[]>();
  for (const r of routes) {
    if (r.points.length !== 4 || r.points[1].x !== r.points[2].x) continue;
    const key = `${Math.round(r.points[0].x)}:${Math.round(r.points[0].y)}`;
    const list = bySource.get(key);
    if (list) list.push(r);
    else bySource.set(key, [r]);
  }
  for (const group of bySource.values()) {
    if (group.length < 2) continue;
    const trunk = group[0].points[0].x + EDGE_STUB_PX;
    for (const route of group) {
      trunked.add(route.id);
      // Never past the bar it points at: a target that starts inside the stub
      // keeps its own channel rather than being dragged on top of itself.
      if (trunk >= route.points[3].x) continue;
      if (route.points[1].x === trunk) continue;
      const pts = route.points.map((p) => ({ ...p }));
      pts[1].x = trunk;
      pts[2].x = trunk;
      moved.set(route.id, pts);
    }
  }

  /**
   * ── The MERGE: arrows arriving at one bar join, they do not weave ─────────
   *
   * Owner report, 2026-09-05, third round: the same tangle at the other end.
   * A bar with several dependencies is fed by arrows that each turn down at
   * their own midpoint, so the one from the higher row runs its horizontal
   * along the target row and straight THROUGH the vertical of the other. It is
   * the trunk pathology read backwards.
   *
   * So it is fixed backwards. A merge is a tree with its root at the target:
   * each arrow runs along its own row to ONE trunk held clear of the bar it
   * feeds, joins it there, and the trunk arrives once. Every junction is a T,
   * and a T is not a crossing.
   *
   * ⚠️ **A fan wins over a merge.** An edge can be a branch of one and a leg of
   * the other, and it cannot put its vertical in two places. Ranking them keeps
   * the choice deterministic, and it costs nothing to look at: the edge left on
   * the fan meets the merge trunk at the target row and runs COLLINEAR into the
   * bar with it, because both end at the same arrowhead. Overlap, not a cross.
   */
  const byTarget = new Map<string, RoutedEdge[]>();
  for (const r of routes) {
    if (r.points.length !== 4 || r.points[1].x !== r.points[2].x) continue;
    const end = r.points[3];
    const key = `${Math.round(end.x)}:${Math.round(end.y)}`;
    const list = byTarget.get(key);
    if (list) list.push(r);
    else byTarget.set(key, [r]);
  }
  for (const group of byTarget.values()) {
    if (group.length < 2) continue;
    const merge = group[0].points[3].x - EDGE_STUB_PX;
    for (const route of group) {
      if (trunked.has(route.id)) continue;
      trunked.add(route.id);
      // Never behind the bar it leaves: a source ending inside the stub keeps
      // its own channel rather than being routed back through itself.
      if (merge <= route.points[0].x) continue;
      if (route.points[1].x === merge) continue;
      const pts = route.points.map((p) => ({ ...p }));
      pts[1].x = merge;
      pts[2].x = merge;
      moved.set(route.id, pts);
    }
  }

  // Forward routes: the vertical run at points[1].x === points[2].x.
  const forward = routes
    .filter((r) => !trunked.has(r.id))
    .map((r) => ({ id: r.id, points: moved.get(r.id) ?? r.points }))
    .filter((r) => r.points.length === 4 && r.points[1].x === r.points[2].x);
  for (const group of cluster(forward, (r) => r.points[1].x)) {
    if (group.length < 2) continue;
    group.forEach((route, i) => {
      const shift = (i - (group.length - 1) / 2) * CHANNEL_PX;
      const pts = (moved.get(route.id) ?? route.points).map((p) => ({ ...p }));
      // Clamped inside the two bars, so a channel can never route backwards
      // through the thing it leaves or the thing it points at.
      const lo = Math.min(pts[0].x, pts[3].x) + 1;
      const hi = Math.max(pts[0].x, pts[3].x) - 1;
      const x = Math.max(lo, Math.min(hi, pts[1].x + shift));
      pts[1].x = x;
      pts[2].x = x;
      moved.set(route.id, pts);
    });
  }

  // Backward routes: the horizontal lane at points[2].y === points[3].y.
  const backward = routes.filter(
    (r) => r.points.length === 5 && r.points[2].y === r.points[3].y,
  );
  for (const group of cluster(backward, (r) => r.points[2].y)) {
    if (group.length < 2) continue;
    group.forEach((route, i) => {
      const shift = (i - (group.length - 1) / 2) * CHANNEL_PX;
      const pts = (moved.get(route.id) ?? route.points).map((p) => ({ ...p }));
      const y = pts[2].y + shift;
      pts[2].y = y;
      pts[3].y = y;
      moved.set(route.id, pts);
    });
  }

  return routes.map((r) => (moved.has(r.id) ? { id: r.id, points: moved.get(r.id)! } : r));
}

export function edgePath(
  from: { bar: Bar | null; row: number },
  to: { bar: Bar | null; row: number },
): string | null {
  const points = edgePoints(from, to);
  return points ? roundedPath(points) : null;
}

/**
 * ⚠️ **A DRAWN EDGE IS ABOUT 1.5 PIXELS WIDE AND CANNOT BE HIT.**
 *
 * So every edge is drawn TWICE: this transparent stroke underneath, which
 * receives the pointer, and the visible line on top with pointer events off.
 * Without the pair, hovering an arrow is a game of pixel-hunting and the
 * feature reads as broken rather than as fussy.
 *
 * 12 is the same number `edgePoints` uses for its stub, which is not a
 * coincidence — a hit area wider than the stub would overlap the bar it
 * leaves, and the bar's own drag zones must win there.
 */
export const EDGE_HIT_PX = 12;

/**
 * How far from the remove control the pointer may stray and still keep the
 * edge aimed.
 *
 * The control is `r=8`. This is the corridor around it, so the last few pixels
 * of the journey cannot be stolen by an arrow underneath.
 */
export const CONTROL_SHIELD_PX = 22;

/**
 * How long an arrow stays aimed after the pointer leaves its stroke.
 *
 * The remove control sits half way ALONG a dogleg, so the straight line a hand
 * takes to reach it leaves the 12px stroke. Without a grace window the control
 * unmounts mid-journey — measured absent on 10 of 16 steps of that walk.
 *
 * Long enough to cross a corner, short enough that an arrow you have genuinely
 * left stops glowing before you notice. It is not an animation: nothing moves
 * during it, and the owner's no-motion ruling is about transitions.
 */
export const EDGE_GRACE_MS = 260;

/**
 * The hit corridor around the arrow you are ALREADY pointing at.
 *
 * `EDGE_HIT_PX` is 12, which is enough to point AT a line and not enough to
 * travel ALONG one. The remove control sits half way along a dogleg, so the
 * route to it cuts corners, and a 12px corridor loses the pointer part-way.
 *
 * Widened only while the edge is aimed, and only for that edge. An arrow you
 * are not pointing at keeps its 12, so a fat invisible stroke never makes its
 * neighbours unpointable — and `edgeHitOrder` puts the aimed one on top, so the
 * corridor wins wherever two arrows share a path.
 */
export const AIM_KEEP_PX = 44;

/**
 * The edges, with the AIMED one drawn last.
 *
 * ⚠️ **This is a hit-testing rule, not a paint order preference.** SVG hands
 * the pointer to the last-painted element, and two edges that leave the same
 * bar share a path prefix — their `EDGE_HIT_PX` strokes lie on top of one
 * another for that whole stretch. So whichever edge happened to sit later in
 * `links` won the pointer everywhere they overlapped.
 *
 * The failure that reports as: hover one arrow, its remove control appears,
 * move toward the control, and part-way there the pointer crosses the shared
 * segment. The other edge's `onMouseEnter` fires, `hoverEdge` flips, and the
 * control vanishes and reappears on the WRONG arrow — under a cursor that
 * never left the first one. Owner report, 2026-09-05, with two edges fanning
 * out of one blocker.
 *
 * Putting the aimed edge last makes it win every overlap it is part of, so an
 * edge you are pointing at keeps the pointer until you genuinely leave it.
 * Nothing else changes: where the aimed edge is not, the others are reachable
 * exactly as before, and with nothing aimed the order is the input's own.
 *
 * Stable for the rest, so the drawing order of the edges you are NOT pointing
 * at never shuffles under you.
 */
export function edgeHitOrder<T extends { id: string }>(
  edges: readonly T[],
  aimedId: string | null,
): T[] {
  if (!aimedId) return [...edges];
  const index = edges.findIndex((edge) => edge.id === aimedId);
  if (index === -1) return [...edges];
  const out = [...edges];
  const [aimed] = out.splice(index, 1);
  out.push(aimed);
  return out;
}

/** Total length of a polyline. The corners are ignored — see `edgeMidpoint`. */
export function polylineLength(points: readonly Point[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  }
  return total;
}

/**
 * The point half way ALONG the edge, for placing the remove control.
 *
 * ⚠️ Half way along, not half way between the ends. On a dogleg those are
 * different points, and the midpoint of the endpoints can land nowhere near
 * the line — often inside a bar, where the control would sit on top of the
 * thing it is not about.
 *
 * Measured on the straight segments and not on the rounded corners. Each
 * corner shaves a few pixels off the true arc, and a control a few pixels
 * from centre is indistinguishable from one at centre.
 */
export function edgeMidpoint(points: readonly Point[]): Point | null {
  if (points.length < 2) return null;
  const half = polylineLength(points) / 2;
  if (half === 0) return { x: points[0].x, y: points[0].y };
  let walked = 0;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    if (len === 0) continue;
    if (walked + len >= half) {
      const t = (half - walked) / len;
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
    }
    walked += len;
  }
  // Only reachable through floating-point drift on the last segment.
  const last = points[points.length - 1];
  return { x: last.x, y: last.y };
}
