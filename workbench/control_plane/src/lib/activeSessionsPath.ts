/**
 * The gateway path of the live-run list, from the browser's request
 * (WS-51 S3). Only `steps=1` passes through: the activity panel asks for each
 * run's step while it is open. Nothing else in the query reaches the gateway.
 *
 * Fence (R7): `src/lib/shell/activityControl.test.ts`.
 */
export const ACTIVE_SESSIONS_PATH = "/chat/active-sessions";

/**
 * The header of a list that is NOT complete (WS-51 S5): a Redis, Postgres or
 * question read error in the gateway, or a lost gateway call in the BFF. The
 * poller keeps its last list when it sees it. The gateway's twin is
 * `RUNS_PARTIAL_HEADER` in `gateway/routes/chat.py`.
 */
export const RUNS_PARTIAL_HEADER = "X-Runs-Partial";

export function activeSessionsPath(requestUrl: string | null | undefined): string {
  let steps = false;
  try {
    steps = new URL(requestUrl ?? "", "http://local").searchParams.get("steps") === "1";
  } catch {
    steps = false;
  }
  return steps ? `${ACTIVE_SESSIONS_PATH}?steps=1` : ACTIVE_SESSIONS_PATH;
}
