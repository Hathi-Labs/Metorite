"use client";

/**
 * One section of a Studio page (STU-AF7): a title over a group of cells, folded by the shared
 * `CollapsibleSection`. It draws no control of its own. A fold is kept for the viewer, in
 * `localStorage`, under the app, the page and the section (`lib/studio/sectionFold.ts`).
 *
 * The title is manifest text, and it draws as text. The panel stays mounted while folded, so a
 * table keeps its data and its scroll, and a reload opens the state that the viewer left.
 */
import { useCallback, useState, type ReactNode } from "react";

import CollapsibleSection from "@/components/ui/Collapsible";
import { foldKey, readFolded, viewerStorage, writeFolded } from "@/lib/studio/sectionFold";

export interface StudioSectionProps {
  /** The app id. With none, the fold is not kept. */
  scope?: string | null;
  pageId: string;
  id: string;
  title: string;
  children: ReactNode;
}

export default function StudioSection({ scope, pageId, id, title, children }: StudioSectionProps) {
  const key = scope ? foldKey(scope, pageId, id) : null;
  // The page draws after its data loads, in the browser, so the first render may read the storage.
  const [open, setOpen] = useState(() => !(key && readFolded(viewerStorage(), key)));
  const onOpenChange = useCallback((next: boolean) => {
    setOpen(next);
    if (key) writeFolded(viewerStorage(), key, !next);
  }, [key]);
  return (
    <CollapsibleSection label={title} open={open} onOpenChange={onOpenChange} keepMounted className="min-w-0" ariaLabel={title}>
      {children}
    </CollapsibleSection>
  );
}
