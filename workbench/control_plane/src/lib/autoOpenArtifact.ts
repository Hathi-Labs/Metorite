/**
 * What a chat does when the agent writes a file — ONE rule for every chat
 * (WS-27bm S8, spec `projects_ai_chat.md` §14).
 *
 * The main chat (`app/chat/page.tsx`) grew this first, inline: a document the
 * agent writes opens in the side panel with a "writing" badge, so the member
 * watches it build. The Projects rail had no copy, so a report the rail wrote
 * appeared only as a card. Two copies of the rule would drift, so both chats
 * call this module.
 *
 * Which files open is `artifactKind`'s answer, not a second extension list:
 * Markdown, HTML, and a React artifact under `outputs/`. Anything else stays a
 * card in the thread.
 *
 * ⚠️ **Never on mobile.** A phone has no side panel. Opening a tab there writes
 * to a store nothing renders, and the member sees nothing. The card in the
 * thread is the phone's way in (`ArtifactCard` opens the full-screen viewer).
 *
 * The side-panel calls are injected so the rule is a pure function a node-env
 * test can drive (`autoOpenArtifact.test.ts`).
 */
import { classifyArtifact, isRenderable } from "@/lib/artifactKind";
import { openDoc, pruneToSession, setDocLive } from "@/lib/sidePanelStore";

/** How long the "writing" badge stays after the last write to a file. */
export const LIVE_BADGE_MS = 2500;

export interface AutoOpenDeps {
  openDoc: typeof openDoc;
  setDocLive: typeof setDocLive;
  schedule: (fn: () => void, ms: number) => unknown;
}

const DEFAULT_DEPS: AutoOpenDeps = {
  openDoc,
  setDocLive,
  schedule: (fn, ms) => window.setTimeout(fn, ms),
};

/**
 * Whether a written file opens by itself. Pure.
 *
 * `panelFits` is `lib/sidePanelFit.ts`'s answer. Where the board beside the
 * panel would drop below its minimum width, nothing opens by itself, and the
 * card's Open goes to the full-screen viewer.
 */
export function shouldAutoOpen(path: string, isMobile: boolean, panelFits = true): boolean {
  if (isMobile || !panelFits) return false;
  const name = path.split("/").pop() ?? path;
  const kind = classifyArtifact(name, path);
  return kind === "markdown" || isRenderable(kind);
}

/**
 * Open a written document in the side panel, live, and clear the badge after
 * {@link LIVE_BADGE_MS}. A later write to the same path sets it live again.
 * Returns whether it opened anything.
 */
export function autoOpenArtifact(
  path: string,
  ctx: { sessionId: string | null | undefined; isMobile: boolean; panelFits?: boolean },
  deps: AutoOpenDeps = DEFAULT_DEPS,
): boolean {
  const { sessionId, isMobile, panelFits = true } = ctx;
  if (!sessionId || !shouldAutoOpen(path, isMobile, panelFits)) return false;
  const name = path.split("/").pop() ?? path;
  deps.openDoc({ path, name, sessionId, live: true });
  deps.schedule(() => deps.setDocLive(sessionId, path, false), LIVE_BADGE_MS);
  return true;
}

/**
 * The `onArtifact` handler a chat surface hands to `AgentChat`. The Projects
 * rail builds it from `useSidePanelFits()`, so a narrow row opens nothing by
 * itself. A factory so a test drives it without rendering the rail.
 */
export function artifactHandler(
  ctx: { sessionId: string | null | undefined; isMobile: boolean; panelFits: boolean },
  deps: AutoOpenDeps = DEFAULT_DEPS,
): (entry: { path: string }) => boolean {
  return (entry) => autoOpenArtifact(entry.path, ctx, deps);
}

/**
 * Keep the side panel on the ACTIVE session's files only. Both chats call it
 * when their session changes, so a tab from another conversation, or from
 * `/chat`, never renders in this one's panel.
 */
export function syncPanelToSession(
  sessionId: string | null | undefined,
  prune: (sessionId: string) => void = pruneToSession,
): void {
  if (sessionId) prune(sessionId);
}
