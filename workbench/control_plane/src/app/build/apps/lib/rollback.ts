/**
 * "Make live" on the app run page (task 3976): which version the confirm strip
 * asks about, whether a request is in flight, and why the last one failed.
 * No React and no DOM here, so a node test can drive the whole flow.
 *
 * The gateway answers 409 with a plain `detail` sentence when a version may
 * not go live (`gateway/routes/apps/publish.py`, `rollback_app` and
 * `require_org_review`). The member reads that sentence. Each other failure
 * gets one fallback line, so no raw status reaches the member.
 */

export interface RollbackState {
  /** The version the strip asks about. `null` means no strip. */
  version: number | null;
  busy: boolean;
  /** Why the last try on `version` failed. */
  error: string | null;
}

export const ROLLBACK_IDLE: RollbackState = {
  version: null,
  busy: false,
  error: null,
};

export type RollbackEvent =
  | { type: "ask"; version: number }
  | { type: "cancel" }
  | { type: "send" }
  | { type: "refused"; version: number; message: string }
  | { type: "accepted"; version: number }
  | { type: "settled" };

export function rollbackReducer(
  state: RollbackState,
  event: RollbackEvent
): RollbackState {
  switch (event.type) {
    case "ask":
      return { ...state, version: event.version, error: null };
    case "cancel":
      return { ...state, version: null, error: null };
    case "send":
      return { ...state, busy: true, error: null };
    case "refused":
      // An answer for a version that the strip left changes no words.
      return {
        ...state,
        busy: false,
        error: state.version === event.version ? event.message : state.error,
      };
    case "accepted":
      return state.version === event.version
        ? { ...state, version: null, error: null }
        : state;
    case "settled":
      return { ...state, busy: false };
  }
}

export function rollbackFailedLine(version: number): string {
  return `Could not make v${version} live. Try again.`;
}

/**
 * Ask the gateway to make `version` live. `null` means it did.
 * Keep the literal `fetch` path: `tests/unit/test_chat_parity.py` reads it.
 */
async function postRollback(
  slug: string,
  version: number
): Promise<string | null> {
  try {
    const res = await fetch(`/api/apps/${encodeURIComponent(slug)}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    });
    if (res.ok) return null;
    if (res.status === 409) {
      const body = (await res.json()) as { detail?: unknown } | null;
      if (typeof body?.detail === "string" && body.detail) return body.detail;
    }
  } catch {
    // A dropped connection, or a 409 body that is not JSON.
  }
  return rollbackFailedLine(version);
}

/** One Confirm press, from the request to the refreshed page. */
export async function runRollback({
  slug,
  version,
  dispatch,
  refresh,
}: {
  slug: string;
  version: number;
  dispatch: (event: RollbackEvent) => void;
  /** Reload what the page shows, after the live version moved. */
  refresh: () => Promise<void>;
}): Promise<void> {
  dispatch({ type: "send" });
  const message = await postRollback(slug, version);
  if (message !== null) {
    dispatch({ type: "refused", version, message });
    return;
  }
  dispatch({ type: "accepted", version });
  try {
    await refresh();
  } catch {
    // The version is live. The next page load shows it.
  } finally {
    dispatch({ type: "settled" });
  }
}
