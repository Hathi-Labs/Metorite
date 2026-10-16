"use client";

/**
 * A link to a workspace file in a chat reply (owner report, 2026-10-16).
 *
 * The owner asked for a report. The agent wrote a Markdown file and pasted
 * its `download_url` into the reply. `MarkdownLink` opened that `/api/` link
 * in a new tab, and the browser showed raw Markdown text. Now a plain click
 * opens the file where its card opens it: a document in the side panel on a
 * desktop, and every other file, and every file on a phone, in the full
 * viewer. `artifactOpenSurface` in `lib/artifactKind.ts` is that one rule.
 *
 * The link is a real anchor (`ControlLink`), so a modified click still opens a
 * tab. Its `href` is the rendered view in the Artifacts app
 * (`artifactViewUrl`), never the raw file. A link to a file of another chat,
 * or a reply with no chat around it, goes to that view in this tab.
 *
 * Fence: `artifactLink.test.ts`.
 */

import { createContext, useContext, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import type { FileEntry } from "@/components/ArtifactSidebar";
import { ControlLink } from "@/components/ControlLink";
import { useViewMode } from "@/components/ViewModeProvider";
import {
  artifactOpenSurface,
  artifactViewUrl,
  type ArtifactSurface,
  type WorkspaceFileRef,
} from "@/lib/artifactKind";
import { announceInAppLink } from "@/lib/inAppLink";

/** The viewers of the chat that draws a reply. `MessageBubble` provides them. */
export interface ArtifactOpeners {
  /** The chat's session. A link to a file of another session opens the page. */
  sessionId: string;
  /** Open the full viewer (`ArtifactViewerModal`). */
  openModal?: (entry: FileEntry) => void;
  /** Open the side panel. Absent on a row where the panel does not fit. */
  openPanel?: (entry: FileEntry) => void;
}

export const ArtifactOpenContext = createContext<ArtifactOpeners | null>(null);

/** The entry a viewer takes, for a file that a link names. Its size is not known. */
export function linkedFileEntry(file: WorkspaceFileRef): FileEntry {
  return { path: file.path, name: file.name, size: 0, modified_at: "", mime_type: "" };
}

/**
 * What one plain click on a file link does. It calls one viewer, or it
 * navigates this tab to the rendered view. It never opens the raw file.
 */
export function openFileLink(
  file: WorkspaceFileRef,
  openers: ArtifactOpeners | null,
  isMobile: boolean,
  navigate: (href: string) => void,
): ArtifactSurface | "page" {
  const page = () => {
    navigate(artifactViewUrl(file.sessionId, file.path));
    return "page" as const;
  };
  if (!openers || openers.sessionId !== file.sessionId) return page();
  const entry = linkedFileEntry(file);
  const surface = artifactOpenSurface(file.name, file.path, {
    isMobile,
    panel: Boolean(openers.openPanel),
  });
  if (surface === "panel" && openers.openPanel) {
    openers.openPanel(entry);
    return "panel";
  }
  if (openers.openModal) {
    openers.openModal(entry);
    return "modal";
  }
  return page();
}

interface AnchorProps {
  file: WorkspaceFileRef;
  className?: string;
  children?: ReactNode;
}

/**
 * The anchor, with no hooks: the test draws it with its own openers, as it
 * draws `RemoteImagePlaceholder` for `RemoteImage`.
 */
export function ArtifactFileAnchor({
  file,
  className,
  children,
  openers,
  isMobile,
  navigate,
}: AnchorProps & {
  openers: ArtifactOpeners | null;
  isMobile: boolean;
  navigate: (href: string) => void;
}) {
  return (
    <ControlLink
      href={artifactViewUrl(file.sessionId, file.path)}
      onActivate={() => { openFileLink(file, openers, isMobile, navigate); }}
      className={className}
      data-artifact-link={file.path}
      title={`Open ${file.name}`}
    >
      {children}
    </ControlLink>
  );
}

/** A file link in chat text. `MarkdownLink` draws it for a workspace file. */
export default function ArtifactFileLink(props: AnchorProps) {
  const openers = useContext(ArtifactOpenContext);
  const { isMobile } = useViewMode();
  const router = useRouter();
  const navigate = (href: string) => {
    announceInAppLink(href);
    router.push(href);
  };
  return <ArtifactFileAnchor {...props} openers={openers} isMobile={isMobile} navigate={navigate} />;
}
