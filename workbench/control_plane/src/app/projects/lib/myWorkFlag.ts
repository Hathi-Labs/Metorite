/** The My work flag (WS-64 OP-2). A leaf file: it imports nothing. */

/**
 * The flag. ⚠️ Only the LITERAL `process.env.NEXT_PUBLIC_PROJECTS_MY_WORK`
 * is inlined into the browser bundle, so the default reads it literally and a
 * test passes an explicit `env` instead. `publicFlags.test.ts` fences it.
 */
export function myWorkEnabled(env?: Record<string, string | undefined>): boolean {
  const raw = env
    ? env.NEXT_PUBLIC_PROJECTS_MY_WORK
    : process.env.NEXT_PUBLIC_PROJECTS_MY_WORK;
  return ["1", "true", "on", "yes"].includes(String(raw ?? "").trim().toLowerCase());
}
