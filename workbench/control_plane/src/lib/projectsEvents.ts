/**
 * The window event of a Projects write from chat (S4), in a module with no import.
 *
 * `ProjectToolCards` raises it once for each receipt that reports a done write, and the
 * Projects page and `liveRefresh.ts` listen for it. It lives here, not in the card file,
 * because `liveRefresh.ts` runs in the shell of every page. An import of the card file
 * pulls the chat renderer into that shell (PKG-8a fix round 2).
 *
 * Fence: `src/lib/shell/shellImports.test.ts`.
 */
export const PROJECTS_CHANGED_EVENT = "cc-projects-changed";
