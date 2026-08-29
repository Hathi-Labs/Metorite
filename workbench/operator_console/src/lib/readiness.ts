// Model identity — the two questions every AI surface asks about a model id.
//
// 🔴 **This file used to hold a readiness MATRIX, and the matrix is gone.** It
// built one cell per (tier, task) across four tables, and it was the wrong
// shape twice over. First it drew twenty cells to say four facts, sixteen of
// them reading "not bound", and still needed a horizontal scrollbar. Then,
// once tiers grew ordered backups, a cell could no longer hold the answer at
// all — a chain is a list, not a square.
//
// `fallback.ts` owns that judgement now, and it owns it ALONE. Two modules
// answering "what should I do next" is the second-implementation defect, and
// the two would disagree within a month.
//
// What survives is what had nothing to do with the matrix: how to read a
// vendor out of a model id, and which models may be offered for a job.

/** The vendor a model id belongs to.
 *
 * ⚠️ **Split on the FIRST slash only.** Ids are `vendor/model`, and the model
 * half legitimately contains more slashes — `openrouter/anthropic/claude-3` is
 * one OpenRouter model, not an Anthropic one. Splitting on the last slash, or
 * on every slash, attributes it to the wrong vendor and the chip then claims a
 * credential we may not hold.
 *
 * An id with no slash is its own vendor: `whisper-1` is served by whoever the
 * platform credential belongs to, and guessing here would be worse than
 * showing the id back. */
export function providerOf(model: string): string {
  const s = (model || "").trim();
  const i = s.indexOf("/");
  return i > 0 ? s.slice(0, i) : s;
}

/** Every model that has DECLARED it can serve this task.
 *
 * 🔴 This is the list a tier form must offer. The old form was a free-text
 * box, so an operator could bind a tier to any string at all — and a model
 * that has not declared the capability produces a 500 on the first request
 * rather than a validation error. Offering only capable models makes the
 * broken state unreachable by hand. */
export function capableModelsFor(
  capabilities: { model: string; task: string }[],
  task: string,
): string[] {
  return [
    ...new Set(capabilities.filter((c) => c.task === task).map((c) => c.model)),
  ].sort();
}
