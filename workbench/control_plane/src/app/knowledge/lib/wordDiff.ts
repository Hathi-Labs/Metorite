// ── A word diff (WS-61 KB-2g, `knowledge_base.md` §21.7) ────────────────────
//
// Pure. The history modal compares two versions of a page word by word. This
// file holds the one diff: the shortest edit script of Myers (1986) over two
// lists of keys, with the common head and tail cut off first.
//
// A diff of two long, very different bodies costs time in the square of the
// edits. So the search stops at `MAX_EDITS`, and the answer is then one
// change: all of the middle of A goes out, and all of the middle of B comes
// in. The view still draws, and it says the versions differ too much to mark
// each word (`DiffResult.coarse`).

/** One step of the script. `a` and `b` are indexes into the two lists. */
export type DiffStep =
  | { kind: "equal"; a: number; b: number }
  | { kind: "delete"; a: number }
  | { kind: "insert"; b: number };

export interface DiffResult {
  steps: DiffStep[];
  /** True when the search stopped at `MAX_EDITS` and the middle is one change. */
  coarse: boolean;
}

/** The most edits the search looks for before it gives the coarse answer. */
export const MAX_EDITS = 2000;

/** The steps of a list that A and B share, from `a0` and `b0`, `count` long. */
function equalRun(steps: DiffStep[], a0: number, b0: number, count: number): void {
  for (let i = 0; i < count; i++) steps.push({ kind: "equal", a: a0 + i, b: b0 + i });
}

/**
 * The Myers search over `a` and `b`, which share no head and no tail. It
 * returns the steps with indexes from 0, or `null` past `maxEdits`. It keeps
 * the row of each round, at the size of that round only, so the memory is the
 * square of the edits and not of the lengths.
 */
function myers(a: Int32Array, b: Int32Array, maxEdits: number): DiffStep[] | null {
  const n = a.length;
  const m = b.length;
  const limit = Math.min(maxEdits, n + m);
  const offset = limit + 1;
  const v = new Int32Array(2 * limit + 3);
  const trace: Int32Array[] = [];
  for (let d = 0; d <= limit; d++) {
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]) ? v[offset + k + 1] : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) return backtrack(trace, n, m, d);
    }
    trace.push(v.slice(offset - d, offset + d + 1));
  }
  return null;
}

/** Walk the kept rows back from the end, and return the steps in order. */
function backtrack(trace: Int32Array[], n: number, m: number, edits: number): DiffStep[] {
  const out: DiffStep[] = [];
  let x = n;
  let y = m;
  for (let d = edits; d > 0; d--) {
    const prev = trace[d - 1];
    const at = (k: number) => prev[k + d - 1];
    const k = x - y;
    const down = k === -d || (k !== d && at(k - 1) < at(k + 1));
    const prevK = down ? k + 1 : k - 1;
    const prevX = at(prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      x--;
      y--;
      out.push({ kind: "equal", a: x, b: y });
    }
    if (down) out.push({ kind: "insert", b: prevY });
    else out.push({ kind: "delete", a: prevX });
    x = prevX;
    y = prevY;
  }
  while (x > 0 && y > 0) {
    x--;
    y--;
    out.push({ kind: "equal", a: x, b: y });
  }
  return out.reverse();
}

/**
 * The shortest edit script from `a` to `b`. Two keys are the same word when
 * they are the same string.
 */
export function diffKeys(a: readonly string[], b: readonly string[], maxEdits = MAX_EDITS): DiffResult {
  const steps: DiffStep[] = [];
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  equalRun(steps, 0, 0, head);

  // The middle, as numbers: one number for each distinct key.
  const ids = new Map<string, number>();
  const idOf = (key: string) => {
    let id = ids.get(key);
    if (id === undefined) {
      id = ids.size;
      ids.set(key, id);
    }
    return id;
  };
  const midA = Int32Array.from(a.slice(head, a.length - tail), idOf);
  const midB = Int32Array.from(b.slice(head, b.length - tail), idOf);
  const found = myers(midA, midB, maxEdits);
  let coarse = false;
  if (found) {
    for (const s of found) {
      if (s.kind === "equal") steps.push({ kind: "equal", a: s.a + head, b: s.b + head });
      else if (s.kind === "delete") steps.push({ kind: "delete", a: s.a + head });
      else steps.push({ kind: "insert", b: s.b + head });
    }
  } else {
    coarse = true;
    for (let i = 0; i < midA.length; i++) steps.push({ kind: "delete", a: head + i });
    for (let i = 0; i < midB.length; i++) steps.push({ kind: "insert", b: head + i });
  }
  equalRun(steps, a.length - tail, b.length - tail, tail);
  return { steps, coarse };
}
