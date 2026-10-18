/**
 * The `<<<SETUP:service:KEY=value>>>` tokens of a model answer (WS-54 fix
 * round 1, item 8). The same rule as `orchestrator/setup_tokens.py`.
 *
 * - `GITHUB_TOKEN` goes to `PUT /integrations/github/token`, the one writer of
 *   a pasted token, with its own gates. The chat sends no `replace_app`, so a
 *   live Metorite App is never replaced from model output.
 * - Every other key goes to `POST /integrations/configure`, in its own
 *   request, so a GitHub refusal never drops them.
 * - A refusal adds one plain line to the answer. The line never holds the token.
 *
 * Fence: `setupTokens.test.ts`.
 */

export const GITHUB_TOKEN_KEY = "GITHUB_TOKEN";

export const APP_CONNECTED_NOTE =
  "Metorite did not save the GitHub token, because the Metorite GitHub App is connected and a token " +
  "replaces it. To use a token, paste it in Integrations, under Advanced.";

export const UNREACHABLE_NOTE =
  "Metorite could not reach its server to save the GitHub token. Paste it in Integrations, under Advanced.";

const MAX_DETAIL = 120;

export interface SetupMatch {
  service: string;
  key: string;
  value: string;
}

/** The GitHub token and the other keys of one answer. Empty values drop out. */
export function splitSetupVars(matches: SetupMatch[]): {
  githubToken: string | null;
  vars: { key: string; value: string }[];
} {
  let githubToken: string | null = null;
  const vars: { key: string; value: string }[] = [];
  for (const m of matches) {
    const value = m.value.trim();
    if (!value) continue;
    if (m.key.trim().toUpperCase() === GITHUB_TOKEN_KEY) {
      githubToken = value;
      continue;
    }
    vars.push({ key: m.key, value });
  }
  return { githubToken, vars };
}

/** The line to add to the answer for the token save, or "" for a save. */
export function githubSetupNote(status: number, detail: unknown, token: string): string {
  if (status === 200) return "";
  if (status === 409 && detail === "app_connected") return APP_CONNECTED_NOTE;
  let words = "GitHub refused this token.";
  if (typeof detail === "string" && detail && detail.length <= MAX_DETAIL && !detail.includes(token)) {
    words = detail.endsWith(".") ? detail : `${detail}.`;
  }
  return `Metorite did not save the GitHub token: ${words} Paste it in Integrations, under Advanced.`;
}
