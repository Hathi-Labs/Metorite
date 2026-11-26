/** Assign overlapping spans to side-by-side lanes, as Google Calendar does.
 *  A double booking shows, and nothing hides. Each item comes back with its
 *  lane and the lane count of its overlap group. More than one lane is a
 *  conflict. */
export function layoutSpans<T extends { start: Date; end: Date }>(
  spans: T[],
): { block: T; lane: number; lanes: number }[] {
  const sorted = [...spans].sort(
    (a, b) =>
      a.start.getTime() - b.start.getTime() || a.end.getTime() - b.end.getTime(),
  );
  const out: { block: T; lane: number; lanes: number }[] = [];
  let i = 0;
  while (i < sorted.length) {
    // The largest group of spans that overlap each other in a chain.
    let groupEnd = sorted[i].end.getTime();
    let j = i + 1;
    while (j < sorted.length && sorted[j].start.getTime() < groupEnd) {
      groupEnd = Math.max(groupEnd, sorted[j].end.getTime());
      j++;
    }
    const group = sorted.slice(i, j);
    const laneEnds: number[] = [];
    const laneOf = new Map<T, number>();
    for (const b of group) {
      let lane = laneEnds.findIndex((end) => end <= b.start.getTime());
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(b.end.getTime());
      } else {
        laneEnds[lane] = b.end.getTime();
      }
      laneOf.set(b, lane);
    }
    const lanes = laneEnds.length;
    for (const b of group) out.push({ block: b, lane: laneOf.get(b) ?? 0, lanes });
    i = j;
  }
  return out;
}
