// Where the data on this screen CAME FROM — the guardrail on building the UI
// before the backend.
//
// 🔴 **An operator console that shows invented data is worse than a missing
// page.** A missing page sends someone to ask. A page of plausible numbers gets
// believed, and this app's screens decide whether a customer's AI works and
// what we charge them. So the rule is not "avoid sample data" — sample data is
// how the UI gets designed at all — the rule is that sample data can never be
// SILENT.
//
// Three things enforce that, and none of them is a comment:
//
//   1. `sampleMode()` is OFF unless someone sets the flag. Production never
//      sets it, so an unbuilt screen there is honestly empty.
//   2. Every read returns `Sourced<T>`, so the origin travels WITH the data and
//      a screen cannot render one without having been handed the other.
//   3. No file under `src/app/` may import `@/lib/sample`. `source.test.ts`
//      scans for it. Sample data reaches a page through `read.ts` or not at
//      all, and `read.ts` is the one place that stamps the origin.

/** Where a screen's data came from.
 *
 * ⚠️ `missing` and `error` are different facts and must stay different.
 * `missing` means the backend for this does not exist yet — expected, and the
 * screen says what is owed. `error` means it exists and refused, which is a
 * problem someone has to look at now. Collapsing them makes an outage read as
 * an unfinished feature. */
export type Origin = "live" | "sample" | "missing" | "error";

export type Sourced<T> = {
  data: T;
  origin: Origin;
  /** Why, in an operator's words. Required for everything except `live`. */
  note?: string;
};

const TRUTHY = new Set(["1", "true", "yes", "on"]);

/** Is this deployment allowed to show sample data?
 *
 * 🔴 **Default OFF, and the parse is deliberately strict.** A loose truthiness
 * test makes `OPERATOR_CONSOLE_SAMPLE_DATA=0` turn sample data ON, because "0"
 * is a non-empty string. That single mistake would put fiction in front of a
 * production operator. */
export function sampleMode(
  env: Record<string, string | undefined> = process.env,
): boolean {
  return TRUTHY.has((env.OPERATOR_CONSOLE_SAMPLE_DATA ?? "").trim().toLowerCase());
}

export const live = <T>(data: T): Sourced<T> => ({ data, origin: "live" });

export const sample = <T>(data: T, note: string): Sourced<T> => ({
  data, origin: "sample", note,
});

export const missing = <T>(data: T, note: string): Sourced<T> => ({
  data, origin: "missing", note,
});

export const failed = <T>(data: T, note: string): Sourced<T> => ({
  data, origin: "error", note,
});

/** The banner a screen shows above everything else.
 *
 * ⚠️ **`live` returns null and that is the only silent case.** Every other
 * origin says something, because every other origin means the numbers below are
 * not this deployment's numbers. */
export function provenanceBanner(
  origin: Origin,
  note?: string,
): { tone: "info" | "warn" | "danger"; text: string } | null {
  if (origin === "live") return null;
  if (origin === "sample") {
    return {
      tone: "warn",
      text:
        "SAMPLE DATA — none of the numbers below are real. This screen is " +
        "built and waiting for its backend. " + (note ?? ""),
    };
  }
  if (origin === "missing") {
    return {
      tone: "info",
      text: "Not connected yet. " + (note ?? ""),
    };
  }
  return { tone: "danger", text: note ?? "The Console refused this read." };
}

/** Pick between what the backend gave us and the designed placeholder.
 *
 * 🔴 **The caller supplies BOTH the sample and the empty, and they must be
 * different values.** An earlier draft took one placeholder and returned it for
 * both outcomes, which meant a production screen with no backend rendered the
 * sample rows under a mild blue "not connected" banner — the precise failure
 * this module exists to prevent. Two arguments make that unwriteable.
 */
export function resolve<T>(
  result: { ok: boolean; data?: T; note?: string },
  opts: {
    /** What a designer sees when sample mode is on. */
    sample: T;
    /** What production shows: genuinely nothing. */
    empty: T;
    /** What the backend still owes, in an operator's words. */
    owed: string;
  },
  env: Record<string, string | undefined> = process.env,
): Sourced<T> {
  if (result.ok && result.data !== undefined) return live(result.data);
  // A refusal outranks everything. The endpoint exists and said no, and that
  // is never "not built yet".
  if (result.note) return failed(opts.empty, result.note);
  if (sampleMode(env)) return sample(opts.sample, opts.owed);
  return missing(opts.empty, opts.owed);
}
