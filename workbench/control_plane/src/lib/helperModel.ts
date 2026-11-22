/**
 * The default model of the two small helper routes: `/compact` and the
 * follow-up suggestions (task 1128 follow-up).
 *
 * It is a TIER name and not a model id. With the Router on, the gateway
 * sends the name to the Console, and the Console's `tier_binding` chain picks
 * the model. The Console refuses a bare model id such as
 * `deepseek/deepseek-chat` with a 400, so compaction failed every time.
 * With the Router off, the gateway's local `/v1` path maps the same name to
 * the live tier model (`_resolve_model`). One name works on both paths.
 *
 * `SUGGESTION_MODEL` still overrides it. Fence:
 * `tests/unit/test_helper_model_default.py`.
 */
export const HELPER_MODEL_DEFAULT = "tier-fast";

/** The model of a helper route: the env override, else the tier name. */
export function helperModel(env: string | undefined = process.env.SUGGESTION_MODEL): string {
  const v = (env ?? "").trim();
  return v || HELPER_MODEL_DEFAULT;
}
