// Which provider verb the Router may use for which job — the declare form's
// one vocabulary, and the pairing rule it must not break.
//
// 🔴 **The pairing mirrors the Console's `catalog.check_invocation_for_task`**
// (§6A.14 CP-13a). A `native_*` verb serves the ONE job its handler names, and
// `decide` takes a `native_*` verb only. The Console refuses a wrong pair with
// a 400. This module stops the form from offering one, so the operator never
// meets it.
//
// 📌 **VOX-2 (2026-11-08).** `transcribe` takes a litellm verb or
// `native_sarvam`, and a `sarvam/` model on `transcribe` takes `native_sarvam`
// only (`catalog.check_model_for_invocation` with the task). VOX-6
// (2026-11-08) added `native_assemblyai`, which follows the same rule for an
// `assemblyai/` model.
//
// 🔴 **Six constants here are copies:** `VERBS`, `NATIVE_PREFIX`,
// `NATIVE_TASKS` and `NATIVE_ONLY_TASKS` from `catalog.py`, and
// `NATIVE_PROVIDER` and `NATIVE_TASK_OF` from `handlers.NATIVE_HANDLERS`.
// `tests/unit/test_operator_console_invocations.py` fails when any differs.
//
// ⚠️ The Console stays the authority. A pair this module allowed and the
// Console refused is relayed word for word (DESIGN.md §7 rule 4).

/** Every verb an operator may declare. `native_typesafe` joined on
 *  2026-09-23 (CP-13b): TypeSafe's Jev has no litellm verb, so the Console
 *  calls it through its own handler. `native_aimlapi` joined on 2026-09-24
 *  (CP-13h): the same Jev through the AI/ML API reseller. `native_sarvam`
 *  joined on 2026-11-08 (VOX-2): Sarvam speech to text. `native_assemblyai`
 *  joined the same day (VOX-6): AssemblyAI Sync speech to text. */
export const VERBS = [
  "acompletion",
  "aembedding",
  "atranscription",
  "aspeech",
  "aimage_generation",
  "native_typesafe",
  "native_aimlapi",
  "native_sarvam",
  "native_assemblyai",
] as const;

export type Verb = (typeof VERBS)[number];

/** The prefix every native handler carries. A litellm verb never does. */
export const NATIVE_PREFIX = "native_";

/** The jobs a native handler serves. The Console's `NATIVE_TASKS`. */
export const NATIVE_TASKS: readonly string[] = ["decide", "transcribe"];

/** The jobs that take a native verb ONLY. The Console's `NATIVE_ONLY_TASKS`.
 *  `transcribe` is not here, because `atranscription` still serves it. */
export const NATIVE_ONLY_TASKS: readonly string[] = ["decide"];

/** 🔴 **Which credential each native verb calls** (CP-13h). The model id's
 *  prefix picks the key, and the verb picks the host. `aimlapi/typesafe/jev`
 *  with `native_typesafe` would post the AI/ML API key to TypeSafe. The
 *  Console refuses that pair (`catalog.check_model_for_invocation`), and this
 *  map stops the form from offering it. A copy of the Console's handler table:
 *  `test_operator_console_invocations.py` fails when the two differ. */
export const NATIVE_PROVIDER: Readonly<Record<string, string>> = {
  native_typesafe: "typesafe",
  native_aimlapi: "aimlapi",
  native_sarvam: "sarvam",
  native_assemblyai: "assemblyai",
};

/** The ONE job each native verb serves (VOX-2). A copy of each handler's
 *  `task`: `test_operator_console_invocations.py` fails when they differ. */
export const NATIVE_TASK_OF: Readonly<Record<string, string>> = {
  native_typesafe: "decide",
  native_aimlapi: "decide",
  native_sarvam: "transcribe",
  native_assemblyai: "transcribe",
};

const isNative = (verb: string) => verb.startsWith(NATIVE_PREFIX);

/** The credential prefix of a model id: the text before the FIRST slash. */
export const modelPrefix = (model: string) => model.trim().split("/", 1)[0] ?? "";

/** True when the Console would accept this (verb, task) pair. */
export function verbFitsTask(verb: string, task: string): boolean {
  if (isNative(verb)) {
    return (
      NATIVE_TASKS.includes(task) &&
      Object.hasOwn(NATIVE_TASK_OF, verb) &&
      NATIVE_TASK_OF[verb] === task
    );
  }
  return !NATIVE_ONLY_TASKS.includes(task);
}

/** True when a native verb calls the vendor the model prefix names. A litellm
 *  verb always fits, and so does an empty model, which the form has not
 *  filled yet. */
export function verbFitsModel(verb: string, model: string): boolean {
  if (!isNative(verb) || !model.trim()) return true;
  return Object.hasOwn(NATIVE_PROVIDER, verb) && NATIVE_PROVIDER[verb] === modelPrefix(model);
}

/** The verbs the form offers for one job, in `VERBS` order.
 *
 *  - A model whose prefix names a native vendor for this job gets that
 *    native verb only. litellm cannot call the pair (VOX-2: `sarvam/` on
 *    `transcribe`).
 *  - Otherwise, with a model, a native verb is offered only when it calls
 *    the vendor the prefix names.
 *  - If no verb matches, every verb that fits the job stays, and the
 *    Console's 400 names the mismatch. */
export function verbsForTask(task: string, model = ""): Verb[] {
  const fit = VERBS.filter((v) => verbFitsTask(v, task));
  const prefix = model.trim() ? modelPrefix(model) : "";
  const tied = fit.filter(
    (v) => isNative(v) && prefix !== "" && Object.hasOwn(NATIVE_PROVIDER, v) && NATIVE_PROVIDER[v] === prefix,
  );
  if (tied.length > 0) return tied;
  const matched = fit.filter((v) => verbFitsModel(v, model));
  return matched.length > 0 ? matched : fit;
}

/** The verb to keep when the job or the model changes. The current one if it
 *  still fits, or the first one that does. */
export function verbAfterTaskChange(current: string, task: string, model = ""): string {
  const offered = verbsForTask(task, model);
  if (offered.includes(current as Verb)) return current;
  return offered[0] ?? current;
}
