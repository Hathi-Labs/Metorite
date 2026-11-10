"use client";

/**
 * /knowledge/directories/<type> — one directory (WS-61 KC-8b,
 * `knowledge_curation.md` §6).
 *
 * The layout draws the app bar and the rail. An unknown `<type>` is a state
 * the page explains, with a way back to the spaces.
 */

import { Suspense } from "react";
import { useParams, useRouter } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";

import DirectoryView from "../../components/DirectoryView";
import { isEntityType } from "../../lib/entities";
import { KNOWLEDGE_HOME } from "../../lib/links";

export default function KnowledgeDirectoryPage() {
  // `useSearchParams` (in the view) needs a boundary of its own, or the route
  // renders nothing on the server.
  return (
    <Suspense fallback={null}>
      <Directory />
    </Suspense>
  );
}

function Directory() {
  const params = useParams<{ type: string }>();
  const router = useRouter();
  const type = params?.type ?? "";
  if (!isEntityType(type)) {
    return (
      <EmptyState
        icon="SearchX"
        message="That directory does not exist"
        action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
      />
    );
  }
  return <DirectoryView key={type} type={type} />;
}
