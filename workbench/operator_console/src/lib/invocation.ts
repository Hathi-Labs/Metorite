// Which provider verb the Router may use for which job — the declare form's
// one vocabulary, and the pairing rule it must not break.
//
// 🔴 **The pairing mirrors the Console's `catalog.check_invocation_for_task`**
// (§6A.14 CP-13a). A `native_*` verb serves `decide` only, and `decide` takes a
// `native_*` verb only. The Console refuses a wrong pair with a 400. This
// module stops the form from offering one, so the operator never meets it.
//
// 🔴 **Three constants here are copies:** `VERBS`, `NATIVE_PREFIX` and
// `NATIVE_TASKS`. `tests/unit/test_operator_console_invocations.py` fails
// when they differ from `catalog.py`.
//
// ⚠️ The Console stays the authority. A pair this module allowed and the
// Console refused is relayed word for word (DESIGN.md §7 rule 4).

/** Every verb an operator may declare. `native_typesafe` joined on
 *  2026-09-23 (CP-13b): TypeSafe's Jev has no litellm verb, so the Console
 *  calls it through its own handler. */
export const VERBS = [
  "acompletion",
  "aembedding",
  "atranscription",
  "aspeech",
  "aimage_generation",
  "native_typesafe",
] as const;

export type Verb = (typeof VERBS)[number];

/** The prefix every native handler carries. A litellm verb never does. */
export const NATIVE_PREFIX = "native_";

/** The jobs a native handler serves. The Console's `NATIVE_TASKS`. */
export const NATIVE_TASKS: readonly string[] = ["decide"];

const isNative = (verb: string) => verb.startsWith(NATIVE_PREFIX);

/** True when the Console would accept this (verb, task) pair. */
export function verbFitsTask(verb: string, task: string): boolean {
  const nativeTask = NATIVE_TASKS.includes(task);
  return isNative(verb) ? nativeTask : !nativeTask;
}

/** The verbs the form offers for one job, in `VERBS` order. */
export function verbsForTask(task: string): Verb[] {
  return VERBS.filter((v) => verbFitsTask(v, task));
}

/** The verb to keep when the job changes. The current one if it still fits,
 *  or the first one that does. */
export function verbAfterTaskChange(current: string, task: string): string {
  if (verbFitsTask(current, task)) return current;
  return verbsForTask(task)[0] ?? current;
}
