"use client";

/**
 * /memory — Memory Manager
 *
 * Visualises every fact Metorite has learned about the signed-in user.
 * Supports semantic search and per-item deletion.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import type { Mem0Memory } from "@/lib/memory";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

interface MemoryStatus {
  mem0_enabled: boolean;
  graphiti_enabled: boolean;
  count?: number;
}

function StatusBar({ status, loading }: { status: MemoryStatus | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex gap-2 items-center text-xs text-muted-foreground animate-pulse">
        <span className="h-2 w-2 rounded-full bg-secondary" />
        Checking memory status…
      </div>
    );
  }
  if (!status) return null;
  return (
    <div className="flex flex-wrap gap-3 items-center text-xs">
      <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border ${
        status.mem0_enabled
          ? "bg-success/20 border-success/30 text-success"
          : "bg-secondary border-border text-muted-foreground"
      }`}>
        <span className={`h-1.5 w-1.5 rounded-full ${status.mem0_enabled ? "bg-success" : "bg-muted"}`} />
        Episodic memory {status.mem0_enabled ? "active" : "inactive"}
      </span>
      <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border ${
        status.graphiti_enabled
          ? "bg-primary/20 border-primary/30 text-primary"
          : "bg-secondary border-border text-muted-foreground"
      }`}>
        <span className={`h-1.5 w-1.5 rounded-full ${status.graphiti_enabled ? "bg-primary" : "bg-muted"}`} />
        Knowledge graph {status.graphiti_enabled ? "active" : "inactive"}
      </span>
      {status.count !== undefined && (
        <span className="text-muted-foreground">
          {status.count} {status.count === 1 ? "memory" : "memories"} stored
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual memory card
// ---------------------------------------------------------------------------

function MemoryCard({
  memory,
  onDelete,
  deleting,
}: {
  memory: Mem0Memory;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  return (
    <div
      className={`group relative flex flex-col gap-1.5 rounded-lg border p-4 transition-all ${
        deleting
          ? "border-destructive/20 bg-destructive/10 opacity-50"
          : "border-border bg-card/60 hover:border-primary/30"
      }`}
    >
      {/* memory text */}
      <p className="text-sm text-foreground leading-relaxed pr-8">{memory.memory}</p>

      {/* footer row */}
      <div className="flex items-center justify-between mt-1">
        <div className="flex gap-3 text-xs text-muted-foreground">
          {memory.created_at && (
            <span title={new Date(memory.created_at).toLocaleString()}>
              {relativeTime(memory.created_at)}
            </span>
          )}
          {memory.id && (
            <span className="font-mono opacity-50">
              {memory.id.slice(0, 8)}
            </span>
          )}
        </div>
      </div>

      {/* delete button — always visible */}
      <button
        onClick={() => onDelete(memory.id)}
        disabled={deleting}
        title="Delete this memory"
        className="absolute top-3 right-3 rounded p-1 text-muted-foreground/60 hover:bg-destructive/20 hover:text-destructive transition-all disabled:cursor-not-allowed"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M2 4h12M5 4V2h6v2M6 7v5M10 7v5M3 4l1 9a1 1 0 001 1h6a1 1 0 001-1l1-9" />
        </svg>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function MemoryPage() {
  const { data: session } = useSession();
  const userId: string = session?.user?.email ?? "dev@fracktal.in";

  const [memories, setMemories] = useState<Mem0Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());
  const [confirmClear, setConfirmClear] = useState(false);
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Fetch all memories ─────────────────────────────────────────────────
  const loadMemories = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/memory/${encodeURIComponent(userId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json() as Mem0Memory[] | { results?: Mem0Memory[] };
      setMemories(Array.isArray(data) ? data : (data.results ?? []));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load memories");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  // ── Fetch status ───────────────────────────────────────────────────────
  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const res = await fetch(`/api/memory/${encodeURIComponent(userId)}/status`);
      if (res.ok) setStatus(await res.json());
    } catch {
      /* graceful */
    } finally {
      setStatusLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadMemories();
    loadStatus();
  }, [loadMemories, loadStatus]);

  // ── Semantic search (debounced 400ms) ──────────────────────────────────
  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);

    if (!query.trim()) {
      // restore full list on clear
      loadMemories();
      return;
    }

    searchTimeout.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(`/api/memory/${encodeURIComponent(userId)}/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: query.trim(), limit: 20 }),
        });
        if (res.ok) {
          const data = await res.json() as Mem0Memory[] | { results?: Mem0Memory[] };
          setMemories(Array.isArray(data) ? data : (data.results ?? []));
        }
      } catch {
        /* graceful */
      } finally {
        setSearching(false);
      }
    }, 400);

    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, userId]);

  // ── Delete single memory ───────────────────────────────────────────────
  const handleDelete = useCallback(async (id: string) => {
    setDeletingIds((prev) => new Set(prev).add(id));
    try {
      const res = await fetch(
        `/api/memory/${encodeURIComponent(userId)}/${encodeURIComponent(id)}`,
        { method: "DELETE" }
      );
      if (res.ok || res.status === 204) {
        setMemories((prev) => prev.filter((m) => m.id !== id));
        setStatus((s) => s ? { ...s, count: Math.max(0, (s.count ?? 1) - 1) } : s);
      }
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, [userId]);

  // ── Clear all ──────────────────────────────────────────────────────────
  const handleClearAll = useCallback(async () => {
    const ids = memories.map((m) => m.id);
    setConfirmClear(false);
    for (const id of ids) {
      await handleDelete(id);
    }
  }, [memories, handleDelete]);

  return (
    <div className="flex flex-col min-h-screen p-6 max-w-4xl mx-auto">
      {/* ── Page header ─────────────────────────────────────────────── */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Memory</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Facts Metorite has learned about you from past conversations.
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">{userId}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { loadMemories(); loadStatus(); }}
              disabled={loading}
              title="Refresh"
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-muted-foreground border border-border hover:border-border hover:text-foreground transition-colors disabled:opacity-50"
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
                className={loading ? "animate-spin" : ""}>
                <path d="M14 8A6 6 0 1 1 8 2" />
                <path d="M14 2v4h-4" />
              </svg>
              Refresh
            </button>
            {memories.length > 0 && !query && (
              confirmClear ? (
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-muted-foreground">Delete all {memories.length} memories?</span>
                  <button
                    onClick={handleClearAll}
                    className="rounded-md px-2.5 py-1.5 text-xs bg-destructive/30 border border-destructive/40 text-destructive-foreground hover:bg-destructive/50 transition-colors"
                  >
                    Confirm
                  </button>
                  <button
                    onClick={() => setConfirmClear(false)}
                    className="rounded-md px-2.5 py-1.5 text-xs border border-border text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmClear(true)}
                  className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-destructive border border-destructive/40 hover:border-destructive/60 hover:bg-destructive/15 transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M2 4h12M5 4V2h6v2M6 7v5M10 7v5M3 4l1 9a1 1 0 001 1h6a1 1 0 001-1l1-9" />
                  </svg>
                  Clear all
                </button>
              )
            )}
          </div>
        </div>
      </div>

      {/* ── Status badges ───────────────────────────────────────────── */}
      <div className="mb-5">
        <StatusBar status={status} loading={statusLoading} />
      </div>

      {/* ── Search bar ──────────────────────────────────────────────── */}
      <div className="relative mb-6">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
          width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
        >
          <circle cx="7" cy="7" r="5" />
          <path d="M11 11l3 3" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Semantic search across memories…"
          className="w-full rounded-lg border border-border bg-card pl-9 pr-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none transition-colors"
        />
        {(searching || (query && loading)) && (
          <svg
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground animate-spin"
            width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
          >
            <path d="M14 8A6 6 0 1 1 8 2" />
            <path d="M14 2v4h-4" />
          </svg>
        )}
        {query && !searching && (
          <button
            onClick={() => setQuery("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-muted-foreground transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        )}
      </div>

      {/* ── Body ────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/15 px-4 py-3 text-sm text-destructive-foreground">
          {error}
          <button
            onClick={() => { loadMemories(); setError(null); }}
            className="ml-3 underline text-destructive hover:opacity-80"
          >
            Retry
          </button>
        </div>
      )}

      {loading && memories.length === 0 && (
        <div className="flex flex-col gap-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 rounded-lg border border-border bg-card/40 animate-pulse" />
          ))}
        </div>
      )}

      {!loading && !error && memories.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full border border-border bg-card/40 p-4 mb-4">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-muted-foreground">
              <path d="M12 2a7 7 0 017 7c0 4-3 6-3 9H8c0-3-3-5-3-9a7 7 0 017-7z" />
              <path d="M9 17h6M10 21h4" />
            </svg>
          </div>
          <p className="text-muted-foreground text-sm">
            {query ? "No memories matched your search." : "No memories yet."}
          </p>
          <p className="text-muted-foreground text-xs mt-1">
            {query
              ? "Try a different query or clear the search."
              : "Metorite will learn from your conversations as you chat."}
          </p>
        </div>
      )}

      {memories.length > 0 && (
        <>
          {query && (
            <div className="mb-3 text-xs text-muted-foreground">
              {memories.length} result{memories.length !== 1 ? "s" : ""} for &ldquo;{query}&rdquo;
            </div>
          )}
          <div className="flex flex-col gap-2">
            {memories.map((m) => (
              <MemoryCard
                key={m.id}
                memory={m}
                onDelete={handleDelete}
                deleting={deletingIds.has(m.id)}
              />
            ))}
          </div>
          {!query && (
            <p className="mt-4 text-xs text-muted-foreground/70 text-center">
              {memories.length} {memories.length === 1 ? "memory" : "memories"} · hover a card to delete it
            </p>
          )}
        </>
      )}
    </div>
  );
}
