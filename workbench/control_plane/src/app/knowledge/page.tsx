"use client";

/**
 * /knowledge — the Spaces view (WS-61 KB-2a, `knowledge_base.md` §11.1).
 *
 * With no `?space=`, the spaces the member can open, as cards. With
 * `?space=<id>`, that space's landing: its name, its purpose and its top
 * pages. The page tree of the space is in the rail, and a page opens at
 * `/knowledge/pages/<id>`. With `&view=archived`, the archived pages of
 * the space, for a manager of it (KB-2d).
 *
 * The layout draws the app bar and the rail, and decides the first-open
 * phase. This page only draws when Knowledge is on for the member.
 */

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import ArchivedPages from "./components/ArchivedPages";
import SpaceCards from "./components/SpaceCards";
import SpaceLanding from "./components/SpaceLanding";
import { ARCHIVED_VIEW } from "./lib/links";

export default function KnowledgePage() {
  return (
    <Suspense fallback={null}>
      <SpacesView />
    </Suspense>
  );
}

function SpacesView() {
  const params = useSearchParams();
  const space = params.get("space");
  if (space && params.get("view") === ARCHIVED_VIEW) return <ArchivedPages key={space} spaceId={space} />;
  return space ? <SpaceLanding key={space} spaceId={space} /> : <SpaceCards />;
}
