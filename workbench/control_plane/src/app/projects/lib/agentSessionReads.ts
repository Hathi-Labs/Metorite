"use client";

/**
 * The Agents view · the rows of one session (WS-MCP AMR-4, §3.12.9).
 *
 * The map read of a large fleet holds counts only. This read gets the runs and
 * the sub-agents of ONE session, and only when that session opens: in the
 * graph, in the dock or in the phone sheet. It reads again with each new map,
 * so the rows stay as fresh as the cards.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentMap, MapChild, MapRun } from "./agentMap";

export type SessionRead =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; runs: MapRun[]; children: MapChild[] };

/** Reads one session. `null` means the caller may not read it. */
export type ReadSession = (sessionId: string) => Promise<AgentMap | null>;

/** The sessions that are open now, each once. Nothing open is no read. */
export function wantedSessions(...ids: ReadonlyArray<string | null | undefined>): string[] {
  return [...new Set(ids.filter((id): id is string => Boolean(id)))].sort();
}

/** The rows of one session out of its answer. Another session's rows never pass. */
export function sessionRead(map: AgentMap | null, sessionId: string): SessionRead {
  if (!map) return { kind: "error" };
  return {
    kind: "ready",
    runs: map.runs.filter((r) => r.session_id === sessionId),
    children: map.children.filter((c) => c.session_id === sessionId),
  };
}

/** A failed refresh keeps the rows that are on screen. */
export function keepReady(old: SessionRead | undefined, next: SessionRead): SessionRead {
  return next.kind === "error" && old?.kind === "ready" ? old : next;
}

/**
 * The reads of ONE project scope. The runs of a session differ by scope, so a
 * read of one project never draws in another.
 */
export interface ReadCache {
  /** The project in scope, or "" for the whole organization. */
  scope: string;
  reads: ReadonlyMap<string, SessionRead>;
}

const NO_READS: ReadonlyMap<string, SessionRead> = new Map();

/** The reads that a scope may draw. A cache of another scope gives none. */
export function readsOf(cache: ReadCache, scope: string): ReadonlyMap<string, SessionRead> {
  return cache.scope === scope ? cache.reads : NO_READS;
}

/** One answer into the cache. The first answer of a new scope drops the old one. */
export function putRead(cache: ReadCache, scope: string, id: string, next: SessionRead): ReadCache {
  const held = readsOf(cache, scope);
  return { scope, reads: new Map(held).set(id, keepReady(held.get(id), next)) };
}

export function useSessionReads(
  /** The project in scope, or "". A change of it drops every read. */
  scope: string,
  ids: readonly string[],
  /** A new value reads again: the map of the last re-read. */
  stamp: unknown,
  read: ReadSession,
): { reads: ReadonlyMap<string, SessionRead>; retry: () => void } {
  const [cache, setCache] = useState<ReadCache>(() => ({ scope, reads: NO_READS }));
  const [again, setAgain] = useState(0);
  const readRef = useRef(read);
  useEffect(() => {
    readRef.current = read;
  }, [read]);
  const key = ids.join(",");
  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    for (const id of key.split(",")) {
      const put = (next: SessionRead) => {
        if (live) setCache((held) => putRead(held, scope, id, next));
      };
      readRef
        .current(id)
        .then((map) => put(sessionRead(map, id)))
        .catch(() => put({ kind: "error" }));
    }
    return () => {
      live = false;
    };
  }, [scope, key, stamp, again]);
  const retry = useCallback(() => {
    setCache((held) => ({ ...held, reads: new Map([...held.reads].filter(([, r]) => r.kind === "ready")) }));
    setAgain((n) => n + 1);
  }, []);
  return { reads: readsOf(cache, scope), retry };
}
