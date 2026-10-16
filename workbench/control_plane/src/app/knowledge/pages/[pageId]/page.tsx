"use client";

/**
 * /knowledge/pages/<id> — one Knowledge page (WS-61 KB-2a).
 *
 * KR-3 deep-links a search hit here. The layout draws the app bar and the
 * rail, and `components/PageView.tsx` draws the page.
 */

import { Suspense, use } from "react";

import PageView from "../../components/PageView";

export default function KnowledgePageRoute({ params }: { params: Promise<{ pageId: string }> }) {
  const { pageId } = use(params);
  return (
    <Suspense fallback={null}>
      <PageView key={pageId} pageId={pageId} />
    </Suspense>
  );
}
