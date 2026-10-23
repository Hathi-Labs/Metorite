"use client";

/**
 * /knowledge/gaps — the Gaps view (WS-61 KB-9a, `knowledge_base.md` §8.4
 * and §11.1).
 *
 * The layout draws the app bar and the rail, and decides the first-open
 * phase. `?write=<id>` opens the New page form for one question, so the My
 * Day card is a link.
 */

import { Suspense } from "react";

import GapsView from "../components/GapsView";

export default function KnowledgeGapsPage() {
  // `useSearchParams` needs a boundary of its own, or the route renders
  // nothing on the server.
  return (
    <Suspense fallback={null}>
      <GapsView />
    </Suspense>
  );
}
