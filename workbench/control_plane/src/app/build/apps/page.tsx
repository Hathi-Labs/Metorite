"use client";

/**
 * /build/apps — the Custom Apps gallery.
 *
 * Small software, built by the team, for the team (docs/app-workshop §5).
 * A describe-to-create bar seeds the Workshop builder chat; app cards open
 * the published app full-page, drafts (and editors) jump into the Workshop.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import FilterPills from "@/components/FilterPills";
import type { AppMeta } from "./lib/types";

// ─── Helpers ──────────────────────────────────────────────────────────────

function formatRelative(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function formatCost(usd: number): string {
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(usd < 10 ? 2 : 0)}`;
}

/** Derive a short app name from a free-form description (first ~5 words). */
function deriveName(text: string): string {
  const words = text.replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
  const name = words.slice(0, 5).join(" ");
  const clipped = name.length > 48 ? name.slice(0, 48).trimEnd() + "…" : name;
  return clipped ? clipped[0].toUpperCase() + clipped.slice(1) : "Untitled app";
}

/** Token-only gradient tiles for app glyphs (mirrors the mockup's g1–g4). */
const GLYPH_GRADIENTS = [
  "bg-gradient-to-br from-primary/20 to-accent/15",
  "bg-gradient-to-br from-success/20 to-primary/10",
  "bg-gradient-to-br from-accent/20 to-destructive/10",
  "bg-gradient-to-br from-primary/15 to-secondary",
];

function glyphGradient(slug: string): string {
  let hash = 0;
  for (let i = 0; i < slug.length; i++)
    hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  return GLYPH_GRADIENTS[Math.abs(hash) % GLYPH_GRADIENTS.length];
}

const HINTS = [
  "Track demo-unit loans and returns",
  "Calculate print quotes from STL weight + material",
  "Dashboard for open service tickets by printer model",
];

type Filter = "all" | "mine" | "team" | "drafts" | "templates";

// ─── App card ─────────────────────────────────────────────────────────────

function AppCard({
  app,
  onOpen,
  onWorkshop,
  onTogglePin,
  onToggleTemplate,
  onUseTemplate,
}: {
  app: AppMeta;
  onOpen: () => void;
  onWorkshop: () => void;
  onTogglePin: () => void;
  onToggleTemplate: () => void;
  onUseTemplate: () => void;
}) {
  const isLive = !!app.live_version;
  const canEdit = app.role === "own" || app.role === "edit";
  const showWorkshop = !isLive || canEdit;
  const hasUsage = (app.month_calls ?? 0) > 0;
  return (
    <button
      onClick={isLive ? onOpen : showWorkshop ? onWorkshop : undefined}
      className="group relative text-left w-full p-3 sm:p-4 rounded-xl border tech-transition flex flex-col gap-2.5 border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
    >
      <div className="absolute top-2 right-2 flex items-center gap-0.5">
        {canEdit && (
          <span
            role="button"
            tabIndex={0}
            title={app.is_template ? "Remove from templates gallery" : "Mark as template"}
            onClick={(e) => {
              e.stopPropagation();
              onToggleTemplate();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.stopPropagation();
                onToggleTemplate();
              }
            }}
            className={`p-1.5 rounded-lg tech-transition ${
              app.is_template
                ? "text-accent opacity-100"
                : "text-muted-foreground reveal-on-hover hover:text-foreground"
            }`}
          >
            <Icon name="LayoutTemplate" className="w-3.5 h-3.5" />
          </span>
        )}
        <span
          role="button"
          tabIndex={0}
          title={app.pinned ? "Unpin" : "Pin to sidebar"}
          onClick={(e) => {
            e.stopPropagation();
            onTogglePin();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.stopPropagation();
              onTogglePin();
            }
          }}
          className={`p-1.5 rounded-lg tech-transition ${
            app.pinned
              ? "text-primary opacity-100"
              : "text-muted-foreground reveal-on-hover hover:text-foreground"
          }`}
        >
          {app.pinned ? (
            <Icon name="Pin" className="w-3.5 h-3.5 fill-current" />
          ) : (
            <Icon name="PinOff" className="w-3.5 h-3.5" />
          )}
        </span>
      </div>
      <div className="flex items-start gap-3">
        <div
          className={`w-9 h-9 rounded-lg border border-border flex items-center justify-center text-lg shrink-0 ${glyphGradient(app.slug)}`}
        >
          {app.icon || "▦"}
        </div>
        <div className="min-w-0 flex-1 pr-5">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground truncate">
              {app.name}
            </h3>
            {app.role !== "use" && (
              <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-secondary text-muted-foreground shrink-0">
                {app.role === "own" ? "owner" : "editor"}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mt-0.5">
            {isLive && <span>v{app.live_version}</span>}
            {isLive && <span>·</span>}
            <span className="truncate">by {app.owner_email.split("@")[0]}</span>
            <span>·</span>
            <Icon name="Clock" className="w-3 h-3 shrink-0" />
            <span>{formatRelative(app.updated_at)}</span>
          </div>
        </div>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2 min-h-[2rem]">
        {app.description || "No description yet."}
      </p>
      <div className="flex items-center gap-2 mt-auto pt-1">
        {isLive ? (
          <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-success bg-success/10">
            Live
          </span>
        ) : (
          <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-warning bg-warning/10">
            Draft
          </span>
        )}
        {app.is_template && (
          <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-accent bg-accent/10">
            Template
          </span>
        )}
        {hasUsage && (
          <span
            title={`${app.month_calls} AI call${app.month_calls === 1 ? "" : "s"} this month`}
            className="flex items-center gap-1 text-[10.5px] text-muted-foreground"
          >
            <Icon name="Zap" className="w-3 h-3 shrink-0" />
            {formatCost(app.month_cost_usd ?? 0)}
          </span>
        )}
        <div className="flex-1" />
        {app.is_template && (
          <span
            role="link"
            tabIndex={0}
            title="Start a new app from this template"
            onClick={(e) => {
              e.stopPropagation();
              onUseTemplate();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.stopPropagation();
                onUseTemplate();
              }
            }}
            className="flex items-center gap-1 text-[11px] font-semibold text-accent hover:opacity-80 tech-transition"
          >
            <Icon name="GitFork" className="w-3 h-3" />
            Use
          </span>
        )}
        {showWorkshop && (
          <span
            role="link"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onWorkshop();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.stopPropagation();
                onWorkshop();
              }
            }}
            className="text-[11px] font-semibold text-primary hover:opacity-80 tech-transition"
          >
            Workshop →
          </span>
        )}
      </div>
    </button>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────

export default function CustomAppsPage() {
  const router = useRouter();
  const { data: session } = useSession();
  const myEmail = (session?.user?.email ?? "").toLowerCase();

  const [apps, setApps] = useState<AppMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [describe, setDescribe] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Fetch-on-mount wiring: same pattern (and lint carve-out) as
  // tasks/components/AssistantRail.tsx.
  /* eslint-disable react-hooks/set-state-in-effect */
  const fetchApps = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/apps");
      if (!res.ok) {
        setError(res.status >= 502 ? "Gateway offline." : `HTTP ${res.status}`);
        setApps([]);
        return;
      }
      // GET /apps returns a bare array (FastAPI response_model=list[AppSummary],
      // no {apps: [...]} envelope — see gateway/routes/apps/lifecycle.py).
      const data = (await res.json()) as AppMeta[];
      setApps(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(String(e));
      setApps([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchApps();
  }, [fetchApps]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const createApp = useCallback(
    async (seed: string) => {
      if (creating) return;
      setCreating(true);
      setCreateError(null);
      try {
        const text = seed.trim();
        const res = await fetch("/api/apps", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: text ? deriveName(text) : "Untitled app",
            description: text || undefined,
          }),
        });
        // POST /apps returns a bare AppDetail on success (no {app: ...}
        // envelope) or a FastAPI error body ({"detail": ...}) on failure.
        const data = (await res.json().catch(() => ({}))) as {
          slug?: string;
          detail?: string;
        };
        if (!res.ok || !data.slug) {
          setCreateError(
            (typeof data.detail === "string" && data.detail) ||
              `Create failed (HTTP ${res.status})`
          );
          return;
        }
        const qs = text ? `?seed=${encodeURIComponent(text)}` : "";
        router.push(`/build/apps/${data.slug}/edit${qs}`);
      } catch (e) {
        setCreateError(String(e));
      } finally {
        setCreating(false);
      }
    },
    [creating, router]
  );

  /** Optimistic — the button's own state must not lag a round-trip; a
   * failure just refetches to correct it rather than surfacing an error for
   * something this low-stakes. */
  const togglePin = useCallback(
    async (slug: string, currentlyPinned: boolean) => {
      setApps((prev) =>
        prev.map((a) => (a.slug === slug ? { ...a, pinned: !currentlyPinned } : a))
      );
      try {
        const res = await fetch(`/api/apps/${encodeURIComponent(slug)}/pin`, {
          method: currentlyPinned ? "DELETE" : "POST",
        });
        if (!res.ok) fetchApps();
      } catch {
        fetchApps();
      }
    },
    [fetchApps]
  );

  /** Same optimistic-toggle shape as togglePin, via the generic PATCH
   * endpoint (AppPatch.is_template) rather than a dedicated route. */
  const toggleTemplate = useCallback(
    async (slug: string, currentlyTemplate: boolean) => {
      setApps((prev) =>
        prev.map((a) =>
          a.slug === slug ? { ...a, is_template: !currentlyTemplate } : a
        )
      );
      try {
        const res = await fetch(`/api/apps/${encodeURIComponent(slug)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_template: !currentlyTemplate }),
        });
        if (!res.ok) fetchApps();
      } catch {
        fetchApps();
      }
    },
    [fetchApps]
  );

  // "Start from this template" — duplicates the template via the same
  // fork/remix endpoint the run page's Fork button uses, then jumps
  // straight into the new copy's Workshop editor.
  const [forkingSlug, setForkingSlug] = useState<string | null>(null);
  const [forkError, setForkError] = useState<string | null>(null);
  const startFromTemplate = useCallback(
    async (slug: string) => {
      if (forkingSlug) return;
      setForkingSlug(slug);
      setForkError(null);
      try {
        const res = await fetch(`/api/apps/${encodeURIComponent(slug)}/fork`, {
          method: "POST",
        });
        const data = (await res.json().catch(() => ({}))) as {
          slug?: string;
          detail?: string;
        };
        if (!res.ok || !data.slug) {
          setForkError(
            (typeof data.detail === "string" && data.detail) ||
              `Couldn't start from template (HTTP ${res.status})`
          );
          return;
        }
        router.push(`/build/apps/${data.slug}/edit`);
      } catch (e) {
        setForkError(String(e));
      } finally {
        setForkingSlug(null);
      }
    },
    [forkingSlug, router]
  );

  const counts = useMemo(() => {
    const mine = apps.filter(
      (a) => a.role === "own" || a.owner_email.toLowerCase() === myEmail
    ).length;
    const team = apps.filter((a) => a.visibility === "org").length;
    const drafts = apps.filter((a) => !a.live_version).length;
    const templates = apps.filter((a) => a.is_template).length;
    return { all: apps.length, mine, team, drafts, templates };
  }, [apps, myEmail]);

  const visible = useMemo(() => {
    switch (filter) {
      case "mine":
        return apps.filter(
          (a) => a.role === "own" || a.owner_email.toLowerCase() === myEmail
        );
      case "team":
        return apps.filter((a) => a.visibility === "org");
      case "drafts":
        return apps.filter((a) => !a.live_version);
      case "templates":
        return apps.filter((a) => a.is_template);
      default:
        return apps;
    }
  }, [apps, filter, myEmail]);

  return (
    <div className="flex flex-col h-full">
      {/* ── Page header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
        <div>
          <h1 className="text-base sm:text-lg font-bold text-foreground">
            Custom Apps
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Small software, built by the team, for the team
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchApps}
            title="Refresh"
            className="p-2 rounded-lg border border-border text-muted-foreground hover:bg-secondary tech-transition"
          >
            <Icon name="RefreshCw" className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <Button size="lg" layout="flex items-center" onClick={() => createApp("")} disabled={creating}>
            {creating ? (
              <Icon name="Loader2" className="w-4 h-4 animate-spin" />
            ) : (
              <Icon name="Plus" className="w-4 h-4" />
            )}
            New app
          </Button>
        </div>
      </div>

      {/* ── Filters ─────────────────────────────────────────────────── */}
      <FilterPills
        items={[
          { id: "all", label: "All", count: counts.all },
          { id: "mine", label: "Mine", count: counts.mine },
          { id: "team", label: "Team", count: counts.team },
          { id: "drafts", label: "Drafts", count: counts.drafts },
          { id: "templates", label: "Templates", count: counts.templates },
        ]}
        activeId={filter}
        onChange={(id) => setFilter(id as Filter)}
      />

      {forkError && (
        <div className="flex items-center gap-2 mx-4 sm:mx-6 mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive shrink-0">
          <Icon name="X" className="w-3.5 h-3.5 shrink-0" />
          {forkError}
          <button
            onClick={() => setForkError(null)}
            className="ml-auto text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Content ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 sm:p-6 flex flex-col gap-5">
          {/* Describe-to-create (genesis bar) */}
          <div className="rounded-2xl border border-border bg-card p-4 sm:p-5 flex flex-col gap-3 bg-gradient-to-br from-primary/5 to-transparent">
            <form
              className="flex items-center gap-2.5 rounded-xl border border-border bg-background px-3.5 py-2.5 focus-within:border-primary/50 tech-transition"
              onSubmit={(e) => {
                e.preventDefault();
                if (describe.trim()) createApp(describe);
              }}
            >
              <Icon name="Sparkles" className="w-4 h-4 text-accent shrink-0" />
              <input
                value={describe}
                onChange={(e) => setDescribe(e.target.value)}
                placeholder="Describe the tool you need… e.g. “A board that shows every printer's service history and next due date”"
                className="flex-1 bg-transparent outline-none text-[13px] text-foreground placeholder:text-muted-foreground"
              />
              <Button size="none" layout="" type="submit" disabled={creating || !describe.trim()} className="px-3 py-1.5 text-xs font-semibold shrink-0">
                {creating ? "Creating…" : "Build it"}
              </Button>
            </form>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-muted-foreground">Try:</span>
              {HINTS.map((h) => (
                <button
                  key={h}
                  onClick={() => setDescribe(h)}
                  className="text-[11px] text-muted-foreground border border-border rounded-full px-2.5 py-1 hover:border-primary/40 hover:text-foreground tech-transition"
                >
                  {h}
                </button>
              ))}
            </div>
            {createError && (
              <div className="flex items-center gap-1.5 text-xs text-destructive">
                <Icon name="X" className="w-3.5 h-3.5" /> {createError}
              </div>
            )}
          </div>

          {/* Loading / error / empty */}
          {loading && (
            <div className="flex flex-col items-center justify-center h-40 gap-3">
              <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading apps…</p>
            </div>
          )}

          {error && !loading && (
            <div className="flex flex-col items-center justify-center h-40 gap-3">
              <p className="text-sm text-destructive">{error}</p>
              <button
                onClick={fetchApps}
                className="text-xs text-muted-foreground hover:text-foreground underline"
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && visible.length === 0 && (
            <div className="flex flex-col items-center justify-center h-56 gap-3 text-center">
              <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center">
                <Icon name="Hammer" className="w-5 h-5 text-primary" />
              </div>
              <p className="text-sm font-medium text-foreground">
                {apps.length === 0 ? "No apps yet" : "Nothing matches this filter"}
              </p>
              <p className="text-xs text-muted-foreground max-w-xs">
                {apps.length === 0
                  ? "Describe the tool you need in the bar above and the Workshop will build it with you."
                  : "Switch filters, or describe a new tool above."}
              </p>
            </div>
          )}

          {/* App grid */}
          {!loading && !error && visible.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {visible.map((app) => (
                <AppCard
                  key={app.slug}
                  app={app}
                  onOpen={() => router.push(`/build/apps/${app.slug}`)}
                  onWorkshop={() =>
                    router.push(`/build/apps/${app.slug}/edit`)
                  }
                  onTogglePin={() => togglePin(app.slug, !!app.pinned)}
                  onToggleTemplate={() =>
                    toggleTemplate(app.slug, !!app.is_template)
                  }
                  onUseTemplate={() => startFromTemplate(app.slug)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
