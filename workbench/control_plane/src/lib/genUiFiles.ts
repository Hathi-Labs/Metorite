/**
 * What a generative-UI card may do with a workspace file: open it, and build
 * its address (the report card, owner directive 2026-10-16).
 *
 * A template gets its `data` and no session, so it cannot name a file's
 * address or open the side panel. The chat that hosts the card knows both.
 * `MessageBubble` provides this context around its cards, and the `report`
 * template reads it. A card with no host (a test, a panel) gets null, and it
 * draws without "Open report" and without the PDF button.
 *
 * The rule of where a file opens is the file card's (`ArtifactCard`): the
 * side panel on a desktop where it fits, else the full-screen viewer. A
 * phone has no side panel. Fence: `src/lib/genUiFiles.test.ts`.
 */
import { createContext } from "react";

import type { FileEntry } from "@/components/ArtifactSidebar";
import { workspaceFileUrl } from "@/lib/artifactKind";

export interface GenUiFiles {
  sessionId: string;
  /** Open a workspace file rendered, or null when nothing here can. */
  open: ((path: string) => void) | null;
}

export const GenUiFileContext = createContext<GenUiFiles | null>(null);

export interface GenUiFileDeps {
  sessionId: string | null | undefined;
  isMobile: boolean;
  /** `lib/sidePanelFit.ts`'s answer: the panel fits beside the page. */
  panelFits: boolean;
  openDoc: (tab: { path: string; name: string; sessionId: string }) => void;
  /** The chat's full-screen viewer (`ArtifactViewerModal`). */
  onFileOpen?: (entry: FileEntry) => void;
}

/**
 * The two acts of a report card: open the report by ITS path, and the
 * address of its PDF. Null for an act this chat cannot do. Pure, so a test
 * can prove which path a click opens.
 */
export function reportCardActions(
  report: { path: string | null; pdf: string | null },
  files: GenUiFiles | null,
): { open: (() => void) | null; pdfUrl: string | null } {
  const path = report.path;
  const opener = files?.open ?? null;
  return {
    open: opener && path ? () => opener(path) : null,
    pdfUrl: files && report.pdf ? workspaceFileUrl(files.sessionId, report.pdf) : null,
  };
}

/** Where a click opens a file, or null when there is no place for it. */
export function fileTarget(d: Pick<GenUiFileDeps, "isMobile" | "panelFits" | "onFileOpen">): "panel" | "viewer" | null {
  if (!d.isMobile && d.panelFits) return "panel";
  return d.onFileOpen ? "viewer" : null;
}

/** The context value for one chat, or null with no session. */
export function genUiFiles(d: GenUiFileDeps): GenUiFiles | null {
  const sessionId = d.sessionId;
  if (!sessionId) return null;
  const target = fileTarget(d);
  if (target === null) return { sessionId, open: null };
  return {
    sessionId,
    open: (path) => {
      const name = path.split("/").pop() || path;
      if (target === "panel") {
        d.openDoc({ path, name, sessionId });
        return;
      }
      d.onFileOpen?.({
        path,
        name,
        size: 0,
        modified_at: new Date().toISOString(),
        mime_type: "application/json",
      });
    },
  };
}
