/**
 * How long a React artifact waits before it asks the server to compile it
 * (task 2432, F1).
 *
 * The compile route now has a rate limit for each member and one build slot
 * for the whole process. The live preview of the artifact editor used to send
 * one compile for each key press. So a changed source waits for a pause in
 * the typing, and only the last source compiles. The first source of a frame
 * compiles at once, so a chat card shows with no extra wait.
 */
export const COMPILE_DEBOUNCE_MS = 600;

/** The wait before a compile: none for the first source, else the debounce. */
export function compileDelay(firstSource: boolean): number {
  return firstSource ? 0 : COMPILE_DEBOUNCE_MS;
}

/**
 * Start `run` after {@link compileDelay}. Returns the cancel, for an effect's
 * cleanup: a source that changes again before its pause never compiles.
 * `SandboxedReact` schedules every compile through here
 * (`compileDebounce.test.ts` holds both halves).
 */
export function scheduleCompile(firstSource: boolean, run: () => void): () => void {
  const timer = setTimeout(run, compileDelay(firstSource));
  return () => clearTimeout(timer);
}
