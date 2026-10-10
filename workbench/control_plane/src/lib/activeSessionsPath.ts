/**
 * The gateway path of the live-run list, from the browser's request
 * (WS-51 S3). Only `steps=1` passes through: the activity panel asks for each
 * run's step while it is open. Nothing else in the query reaches the gateway.
 *
 * Fence (R7): `src/lib/shell/activityControl.test.ts`.
 */
export const ACTIVE_SESSIONS_PATH = "/chat/active-sessions";

export function activeSessionsPath(requestUrl: string | null | undefined): string {
  let steps = false;
  try {
    steps = new URL(requestUrl ?? "", "http://local").searchParams.get("steps") === "1";
  } catch {
    steps = false;
  }
  return steps ? `${ACTIVE_SESSIONS_PATH}?steps=1` : ACTIVE_SESSIONS_PATH;
}
