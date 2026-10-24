/**
 * The flag for the Assistant pane in Settings (WS-63 PZ-7, flag
 * `NEXT_PUBLIC_ASSISTANT_PROFILE`, `project-docs/specs/personalization.md`
 * §7). Off, no link row shows and `/settings/assistant` says it is not
 * available. The gateway routes stay as they are.
 *
 * A file of its own, with no imports, as `shell/chatFlag.ts` is, so the
 * Appearance page can ask a yes or no without the pane in its bundle.
 */

/** The pure rule: the build value is "1", or a dev-only override is "1". */
export function assistantProfileFlagValue(
  built: string | undefined,
  override: string | null,
): boolean {
  return built === "1" || override === "1";
}

/** Whether the Assistant pane is on in this browser. */
export function assistantProfileOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  const built = process.env.NEXT_PUBLIC_ASSISTANT_PROFILE;
  if (built === "1") return true;
  // Development and test builds ONLY. Next inlines NODE_ENV, so a production
  // build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return (
        typeof localStorage !== "undefined" &&
        assistantProfileFlagValue(built, localStorage.getItem("cc-assistant-profile"))
      );
    } catch {
      return false;
    }
  }
  return false;
}
