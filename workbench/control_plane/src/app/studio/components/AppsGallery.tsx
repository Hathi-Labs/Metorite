"use client";

/**
 * The Custom Apps gallery, moved out of `/build/apps` into the Studio library
 * (`ai_studio.md` §14 A1). The Studio page owns the one page heading, so this
 * section heads itself with an `<h2>`. The run page and the Workshop stay at
 * `/build/apps/[slug]` and `/build/apps/[slug]/edit`.
 *
 * Small software, built by the team, for the team (docs/app-workshop §5).
 * A describe-to-create bar seeds the Workshop builder chat; app cards open
 * the published app full-page, drafts (and editors) jump into the Workshop.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import FilterPills from "@/components/FilterPills";
import type { AppMeta } from "@/app/build/apps/lib/types";
import { shellChatOn } from "@/lib/shell/chatFlag";
import {
  PACKAGES_FAILED_LINE, type LibraryFilter, appBrief, changedWords, libraryCounts, mergeLibrary,
  packageShows, type PackageCard as PackageCardData,
} from "@/lib/studio/packageLibrary";
import { buildHref } from "@/lib/studio/packageDraft";
import { STUDIO_OFF, type PackageSummary, listPackages } from "@/lib/studio/realDoor";
import { startBuild } from "@/lib/studio/startBuild";

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

type Filter = LibraryFilter;

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

// ─── Package card ─────────────────────────────────────────────────────────

/** A Studio package. The whole card is a link to the app page. */
function PackageCard({ card, onOpen }: { card: PackageCardData; onOpen: () => void }) {
  const live = card.state === "live";
  return (
    <div className="group flex w-full flex-col rounded-xl border tech-transition border-border bg-card hover:border-primary/40 hover:bg-secondary/30">
      <button type="button" data-package-card={card.id} onClick={onOpen} className="w-full p-3 sm:p-4 pb-2 text-left">
        <div className="flex items-start gap-3">
          <div
            className={`w-9 h-9 rounded-lg border border-border flex items-center justify-center shrink-0 ${glyphGradient(card.id)}`}
          >
            <Icon name="Boxes" className="w-4 h-4 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-foreground truncate">{card.name}</h3>
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mt-0.5">
              <Icon name="Clock" className="w-3 h-3 shrink-0" />
              <span>{changedWords(card.changedAt)}</span>
            </div>
          </div>
        </div>
      </button>
      <div className="mt-auto flex items-center justify-between gap-2 px-3 sm:px-4 pb-3">
        <Badge tone={live ? "success" : "warning"} size="xs">{live ? "Live" : "Draft"}</Badge>
        <Button variant="ghost" size="sm" icon="Code" href={buildHref(card.id)}>Open in Studio IDE</Button>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────

export default function AppsGallery() {
  const router = useRouter();
  const { data: session } = useSession();
  const myEmail = (session?.user?.email ?? "").toLowerCase();

  const [apps, setApps] = useState<AppMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const packagesSeq = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [describe, setDescribe] = useState("");
  const [packages, setPackages] = useState<PackageSummary[]>([]);
  const [packagesError, setPackagesError] = useState(false);

  // Fetch-on-mount wiring: same pattern (and lint carve-out) as
  // tasks/components/AssistantRail.tsx.
  /* eslint-disable react-hooks/set-state-in-effect */
  const fetchApps = useCallback(async () => {
    setLoading(true);
    setError(null);
    // The packages of the member sit beside the Workshop apps. A switch that is off
    // answers 404 and shows no package and no error. Any other failure says so.
    // An answer that arrives after a newer request is dropped, so a slow old list never wins.
    const seq = ++packagesSeq.current;
    const packagesRead = listPackages()
      .then((found) => {
        if (seq !== packagesSeq.current) return;
        setPackages(found);
        setPackagesError(false);
      })
      .catch((e: unknown) => {
        if (seq !== packagesSeq.current) return;
        setPackages([]);
        setPackagesError(!(e instanceof Error && e.message === STUDIO_OFF));
      });
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
      // Hold the skeleton until both reads settle, so "No apps yet" never flashes
      // while the packages are still on the way. A failure of one still shows the other.
      await packagesRead;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchApps();
  }, [fetchApps]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Chat first, in the Studio IDE (G3a): "Build it" makes a draft package, opens its IDE, and opens
  // the dock with the brief waiting in the box. The page of the IDE binds the dock to that package.
  // With the shell chat off, `/chat?q=` does what it did before.
  const buildingRef = useRef(false);
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);
  const createApp = useCallback(
    (seed: string) => {
      if (!shellChatOn()) {
        router.push(`/chat?q=${encodeURIComponent(appBrief(seed))}`);
        return;
      }
      if (buildingRef.current) return;
      buildingRef.current = true;
      setBuilding(true);
      setBuildError(null);
      void startBuild(seed, { go: (href) => router.push(href) })
        .then((r) => { if (!r.ok) setBuildError(r.message); })
        .finally(() => { buildingRef.current = false; setBuilding(false); });
    },
    [router]
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

  const counts = useMemo(() => libraryCounts(apps, packages, myEmail), [apps, packages, myEmail]);

  const visible = useMemo(() => {
    const keep = (a: AppMeta): boolean => {
      switch (filter) {
        case "mine":
          return a.role === "own" || a.owner_email.toLowerCase() === myEmail;
        case "team":
          return a.visibility === "org";
        case "drafts":
          return !a.live_version;
        case "templates":
          return !!a.is_template;
        default:
          return true;
      }
    };
    const shownPackages = packages.filter((p) => packageShows(filter, p));
    return mergeLibrary(apps.filter(keep), shownPackages);
  }, [apps, packages, filter, myEmail]);
  const total = apps.length + packages.length;

  return (
    <section className="flex flex-col" aria-label="Apps">
      {/* ── Section header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 border-b border-border shrink-0">
        <div>
          <h2 className="text-sm font-bold text-foreground">Apps</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Small software, built by the team, for the team
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchApps}
            title="Refresh apps"
            className="p-2 rounded-lg border border-border text-muted-foreground hover:bg-secondary tech-transition"
          >
            <Icon name="RefreshCw" className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <Button size="lg" layout="flex items-center whitespace-nowrap shrink-0" onClick={() => createApp("")} loading={building} disabled={building}>
            <Icon name="Plus" className="w-4 h-4" />
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

      {buildError && (
        <div role="alert" className="flex items-center gap-2 mx-4 sm:mx-6 mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive shrink-0">
          <Icon name="X" className="w-3.5 h-3.5 shrink-0" />
          {buildError}
        </div>
      )}

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
      <div>
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
              <Button size="none" layout="" type="submit" loading={building} disabled={!describe.trim() || building} className="px-3 py-1.5 text-xs font-semibold shrink-0">
                Build it
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
            {packagesError && (
              <div role="alert" className="flex items-center gap-1.5 text-xs text-destructive">
                <Icon name="X" className="w-3.5 h-3.5" /> {PACKAGES_FAILED_LINE}
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

          {error && !loading && total === 0 && (
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

          {!loading && !(error && total === 0) && visible.length === 0 && (
            <div className="flex flex-col items-center justify-center h-56 gap-3 text-center">
              <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center">
                <Icon name="Hammer" className="w-5 h-5 text-primary" />
              </div>
              <p className="text-sm font-medium text-foreground">
                {total === 0 ? "No apps yet" : "Nothing matches this filter"}
              </p>
              <p className="text-xs text-muted-foreground max-w-xs">
                {total === 0
                  ? "Describe the tool you need in the bar above and Mira will build it with you in the chat."
                  : "Switch filters, or describe a new tool above."}
              </p>
              {total === 0 && (
                <Button size="none" layout="" onClick={() => createApp("")} loading={building} disabled={building} className="px-4 py-2 text-sm">
                  Create an app
                </Button>
              )}
            </div>
          )}

          {/* App grid */}
          {!loading && !(error && total === 0) && visible.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {visible.map((item) => item.kind === "package" ? (
                <PackageCard key={item.key} card={item} onOpen={() => router.push(item.href)} />
              ) : (
                <AppCard
                  key={item.key}
                  app={item.app}
                  onOpen={() => router.push(`/build/apps/${item.app.slug}`)}
                  onWorkshop={() =>
                    router.push(`/build/apps/${item.app.slug}/edit`)
                  }
                  onTogglePin={() => togglePin(item.app.slug, !!item.app.pinned)}
                  onToggleTemplate={() =>
                    toggleTemplate(item.app.slug, !!item.app.is_template)
                  }
                  onUseTemplate={() => startFromTemplate(item.app.slug)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
