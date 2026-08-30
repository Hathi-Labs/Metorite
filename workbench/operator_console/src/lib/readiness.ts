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
//
// ⚠️ **`vendorWarning` belongs here and nowhere else.** It answers "is the
// vendor half of this id one we can actually call", which is `providerOf`'s
// question with the credentials added. Putting it in the declare form would
// hide it from the test suite, which carries no React renderer.

import type { ProviderAccount } from "./contract";
import { KNOWN_PROVIDERS, vendorLabel } from "./providerGuides";
import { armedProviders } from "./providers";

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

/** What the typed model id says about the vendor half, before anything is
 *  saved.
 *
 * 🔴 **The vendor half is the half that goes wrong silently.** The Router
 * resolves it as `model.split("/", 1)[0]` and looks the credential up on
 * exactly that word. A model declared under a vendor we hold no key for is
 * accepted here, binds to a tier happily, and answers 503 on the first
 * customer request. Two spellings caused that in this very console —
 * `google` for what litellm calls `gemini`, and `together` for `together_ai`.
 *
 * ⚠️ **This warns. It never blocks.** Declaring a model before installing its
 * key is a legitimate order of work, and a form that refused it would send
 * somebody back to the API. */
export function vendorWarning(model: string, accounts: ProviderAccount[]): string | null {
  const id = model.trim();
  if (!id) return null;
  if (!id.includes("/")) {
    return (
      "This id names no vendor. The Router reads the vendor from the part " +
      "before the first slash, so it will look for a credential called " +
      `"${id}".`
    );
  }
  const vendor = providerOf(id);
  if (armedProviders(accounts).includes(vendor)) return null;
  const known = KNOWN_PROVIDERS.includes(vendor);
  return known
    ? `No live key for ${vendorLabel(vendor)}. Declaring this is fine — a ` +
      "tier bound to it answers 503 until you install one on Providers."
    : `"${vendor}" is not a vendor we hold a key for, and not one we have ` +
      "written up. Check it is the id litellm uses, not the company's name.";
}
