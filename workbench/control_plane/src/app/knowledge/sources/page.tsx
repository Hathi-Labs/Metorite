"use client";

/**
 * /knowledge/sources — the Sources view (WS-61 KC-1b,
 * `knowledge_curation.md` §7.1).
 *
 * The layout draws the app bar and the rail, and decides the first-open
 * phase. It also takes the Google return and brings the member here.
 */

import { Suspense } from "react";

import SourcesView from "../components/SourcesView";

export default function KnowledgeSourcesPage() {
  return (
    <Suspense fallback={null}>
      <SourcesView />
    </Suspense>
  );
}
