"use client";

/**
 * The pending proposals of the assistant, read once and shared (KB-5b).
 *
 * The rail's count, the Review view and the notice on a page all read the
 * one cache entry, so they agree. A failed read is an empty list here: the
 * rail shows no entry, and the Review view says it could not load.
 */

import { useCachedResource, type CachedResource } from "@/lib/useCachedResource";

import { listProposals } from "../lib/api";
import { PROPOSALS_KEY, type Proposal } from "../lib/review";

export function useProposals(): CachedResource<Proposal[]> {
  return useCachedResource<Proposal[]>(PROPOSALS_KEY, listProposals);
}

/** The count of the proposals that wait. Zero while loading or on a failed read. */
export function usePendingCount(): number {
  return useProposals().data?.length ?? 0;
}
