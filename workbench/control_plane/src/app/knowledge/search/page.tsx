"use client";

/**
 * /knowledge/search?q= — the Search view (WS-61 KB-3b, `knowledge_base.md`
 * §11.1).
 *
 * The layout draws the app bar and the rail, and decides the first-open
 * phase. This page only draws when Knowledge is on for the member. The words
 * live in `?q=`, so a search is a link, and a reload shows the same hits.
 */

import { Suspense } from "react";

import KnowledgeSearch from "../components/KnowledgeSearch";

export default function KnowledgeSearchPage() {
  // `useSearchParams` needs a boundary of its own, or the route renders
  // nothing on the server.
  return (
    <Suspense fallback={null}>
      <KnowledgeSearch />
    </Suspense>
  );
}
