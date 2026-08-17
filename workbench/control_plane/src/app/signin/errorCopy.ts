/**
 * Friendly copy for the `?error=` codes Auth.js v5 sends back to the sign-in
 * page.
 *
 * v5 emits ERROR TYPES (`OAuthCallbackError`, `Configuration`,
 * `AccessDenied`), not the v4 codes (`OAuthSignin` / `OAuthCallback`) the old
 * page keyed on — so a user cancelling at the IdP consent screen used to fall
 * through to the raw-code fallback. Kept as a pure module so the mapping is
 * testable without dragging `next-auth/react` into a node test
 * (`signin.test.ts`).
 */
export function signInErrorMessage(code: string | null): string | null {
  if (!code) return null;
  switch (code) {
    case "OAuthCallbackError":
      return "Sign-in was cancelled or failed. Try again.";
    case "Configuration":
      return "Sign-in is misconfigured on the server. Contact your admin.";
    case "AccessDenied":
      return "Your account isn't authorized for this workspace. Ask your admin for an invite.";
    default:
      return `Authentication error: ${code}`;
  }
}
