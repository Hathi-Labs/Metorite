"use client";

/**
 * /knowledge/review?p= — the Review view (WS-61 KB-5b, `knowledge_base.md`
 * §11.1). The layout draws the bar and the rail. `?p=` selects a proposal,
 * so one proposal is a link, and the chat card links here.
 */

import { Suspense } from "react";

import ReviewView from "../components/ReviewView";

export default function KnowledgeReviewPage() {
  // `useSearchParams` needs a boundary of its own.
  return (
    <Suspense fallback={null}>
      <ReviewView />
    </Suspense>
  );
}
