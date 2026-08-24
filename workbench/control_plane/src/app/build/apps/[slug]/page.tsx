"use client";

/**
 * /build/apps/[slug] — a published Custom App running full-page.
 *
 * Platform chrome (glyph · name · Live·vN · owner · viewer identity · info)
 * around the hardened SandboxedHtml frame; the cc bridge brokers the app's
 * user/storage/ai calls with the VIEWER's session (docs/app-workshop §4.4).
 */

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import SandboxedHtml from "@/components/SandboxedHtml";
import {
  buildAppSrcDoc,
  extractCcIconNames,
  useCcBridge,
  type CcToolConfirmDecision,
  type CcToolConfirmRequest,
} from "../lib/ccBridge";
import type { AppMeta, AppUsage, AppVersion } from "../lib/types";

/** A pending `cc.tools.call()` confirm, waiting on the viewer's decision. */
type PendingToolConfirm = CcToolConfirmRequest & {
  resolve: (decision: CcToolConfirmDecision) => void;
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
    });
  } catch {
    return iso;
  }
}

// ─── First-open consent interstitial (§4.8) ────────────────────────────────
// Plain-language scope disclosure, platform-rendered (never app-rendered).

/** A few well-known integrations don't title-case cleanly (`serpapi` →
 * `SerpAPI`, not `Serpapi`) — everything else falls back to a generic
 * title-case of the `tool:` scope's service segment.
 * ⚠️ The `clickup` entry was removed 2026-08-24 (D52): the tool registry it
 * labelled is empty and no manifest can declare that scope any more. */
const KNOWN_SERVICE_LABELS: Record<string, string> = {
  serpapi: "SerpAPI",
};

function serviceLabel(service: string): string {
  const known = KNOWN_SERVICE_LABELS[service.toLowerCase()];
  if (known) return known;
  return service.length > 0
    ? service[0].toUpperCase() + service.slice(1)
    : service;
}

/** `tool:acme.create_task?list=Procurement` → `acme.create_task` +
 * `{list: "Procurement"}`. Mirrors `gateway/routes/apps/tools.py`'s
 * `parse_tool_scope` (split on the first `?`, `?`-side is `key=value&...`). */
function parseToolScope(
  scope: string
): { service: string; action: string; params: [string, string][] } | null {
  if (!scope.startsWith("tool:")) return null;
  const body = scope.slice("tool:".length);
  const qIndex = body.indexOf("?");
  const toolName = (qIndex === -1 ? body : body.slice(0, qIndex)).trim();
  const query = qIndex === -1 ? "" : body.slice(qIndex + 1);
  if (!toolName) return null;
  const dotIndex = toolName.indexOf(".");
  if (dotIndex === -1) return null;
  const service = toolName.slice(0, dotIndex);
  const action = toolName.slice(dotIndex + 1);
  if (!service || !action) return null;
  const params: [string, string][] = [];
  if (query) {
    for (const pair of query.split("&")) {
      if (!pair) continue;
      const [rawKey, rawValue = ""] = pair.split("=");
      params.push([
        decodeURIComponent(rawKey.replace(/\+/g, " ")),
        decodeURIComponent(rawValue.replace(/\+/g, " ")),
      ]);
    }
  }
  return { service, action, params };
}

/** A manifest scope, in plain language for the consent interstitial. Never
 * silently drops a scope it doesn't recognize — falls back to the raw
 * string, which at least doesn't misrepresent what's being granted. */
function describeScope(scope: string): string {
  if (scope === "identity:read") return "See your name and email";
  if (scope === "storage:app")
    return "Store and read data in this app's shared database";
  if (scope.startsWith("ai:")) return "Use AI on your behalf";
  if (scope.startsWith("tool:")) {
    try {
      const parsed = parseToolScope(scope);
      if (!parsed) return scope;
      const action = parsed.action.replace(/_/g, " ");
      const parenthetical =
        parsed.params.length > 0
          ? ` (${parsed.params.map(([k, v]) => `${k}: ${v}`).join(", ")})`
          : "";
      return `Use ${serviceLabel(parsed.service)} to ${action}${parenthetical}`;
    } catch {
      return scope;
    }
  }
  return scope;
}

/** Icon per scope category — `HelpCircle` for anything unrecognized. */
function scopeIcon(scope: string): ThemedIcon {
  if (scope === "identity:read") return themedIcon("User");
  if (scope === "storage:app") return themedIcon("Database");
  if (scope.startsWith("ai:")) return themedIcon("Sparkles");
  if (scope.startsWith("tool:")) return themedIcon("Plug");
  return themedIcon("HelpCircle");
}

export default function AppRunPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  const { data: session } = useSession();
  const viewerEmail = session?.user?.email ?? "dev@fracktal.in";

  const [app, setApp] = useState<AppMeta | null>(null);
  const [bundle, setBundle] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // First-open consent interstitial (§4.8) — a blocking gate distinct from
  // the tool-confirm toast: it must clear before the live bundle is even
  // fetched, not just before a specific cc.tools.call().
  const [showConsent, setShowConsent] = useState(false);
  const [consentBusy, setConsentBusy] = useState(false);
  const [consentNotice, setConsentNotice] = useState<string | null>(null);

  // Info popover (versions + usage, fetched lazily on first open).
  const [showInfo, setShowInfo] = useState(false);
  const [versions, setVersions] = useState<AppVersion[] | null>(null);
  const [usage, setUsage] = useState<AppUsage | null>(null);
  const infoRef = useRef<HTMLDivElement | null>(null);

  // "Make live" (rollback) two-step confirm, editors only.
  const [confirmVersion, setConfirmVersion] = useState<number | null>(null);
  const [rollbackBusy, setRollbackBusy] = useState(false);

  // Fork/remix — duplicate this app's current source as a new app owned by
  // the viewer (works for anyone who can see the app, not just editors).
  const [forking, setForking] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);
  const forkApp = useCallback(async () => {
    if (forking) return;
    setForking(true);
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
            `Fork failed (HTTP ${res.status})`
        );
        return;
      }
      router.push(`/build/apps/${data.slug}/edit`);
    } catch (e) {
      setForkError(String(e));
    } finally {
      setForking(false);
    }
  }, [slug, forking, router]);

  // Tool-confirm toast (bottom-right) — a cc.tools.call() hit a destructive
  // tool with no remembered grant; resolve() unblocks the app frame's call.
  const [pendingConfirm, setPendingConfirm] = useState<PendingToolConfirm | null>(
    null
  );
  const [rememberTool, setRememberTool] = useState(false);
  const onToolConfirm = useCallback(
    (req: CcToolConfirmRequest) =>
      new Promise<CcToolConfirmDecision>((resolve) => {
        setRememberTool(false);
        setPendingConfirm({ ...req, resolve });
      }),
    []
  );

  // Broker the app frame's cc.* calls against /api/apps/{slug}/…
  useCcBridge(slug, { mode: "live", onToolConfirm });

  /** Fetch + set the live bundle — factored out so both the "no consent
   * needed" mount path and the "just consented" Allow handler can trigger
   * it without duplicating the fetch. `track=1` counts this open in the
   * app's usage stats. */
  const fetchLiveBundle = useCallback(async () => {
    const bres = await fetch(
      `/api/apps/${encodeURIComponent(slug)}/bundle?version=live&track=1`
    );
    if (bres.ok) setBundle(await bres.text());
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/apps/${encodeURIComponent(slug)}`);
        if (!res.ok) {
          if (!cancelled)
            setError(
              res.status === 404 ? "App not found." : `HTTP ${res.status}`
            );
          return;
        }
        // GET /apps/{slug} returns a bare AppDetail (no {app: ...} envelope
        // — see gateway/routes/apps/lifecycle.py).
        const data = (await res.json()) as AppMeta;
        if (cancelled || !data) return;
        setApp(data);
        if (data.needs_consent === true) {
          // Blocking gate — don't fetch the bundle until the viewer clears
          // the interstitial (or bails via "Not now").
          setShowConsent(true);
          return;
        }
        if (data.live_version) await fetchLiveBundle();
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug, fetchLiveBundle]);

  /** "Allow" on the consent interstitial — records consent for the live
   * scope set, then proceeds exactly like the no-consent-needed path. A 409
   * `scope_set_changed` means the live version moved between page load and
   * this click; refresh app meta so the modal shows the current scopes
   * instead of silently proceeding on stale consent. */
  const allowConsent = useCallback(async () => {
    if (!app) return;
    setConsentBusy(true);
    setConsentNotice(null);
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/consent`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scope_set_hash: app.live_scope_set_hash,
          }),
        }
      );
      if (res.ok) {
        setShowConsent(false);
        if (app.live_version) await fetchLiveBundle();
        return;
      }
      if (res.status === 409) {
        // FastAPI's HTTPException wraps the app-facing payload under
        // `detail` (see gateway/routes/apps/grants.py's consent_app) —
        // same envelope the Publish modal already unwraps for its own
        // conformance-scan error.
        const body = (await res.json().catch(() => ({}))) as {
          detail?: { error?: string } | string;
        };
        const detail =
          typeof body.detail === "object" && body.detail ? body.detail : null;
        if (detail?.error === "scope_set_changed") {
          setConsentNotice("The scopes changed — refreshing…");
          const ares = await fetch(`/api/apps/${encodeURIComponent(slug)}`);
          if (ares.ok) {
            const data = (await ares.json()) as AppMeta;
            if (data) setApp(data);
          }
          return;
        }
      }
      setConsentNotice(`Couldn't record consent (HTTP ${res.status}).`);
    } catch (e) {
      setConsentNotice(String(e));
    } finally {
      setConsentBusy(false);
    }
  }, [app, slug, fetchLiveBundle]);

  const toggleInfo = useCallback(() => {
    setShowInfo((open) => {
      const next = !open;
      if (next) {
        if (versions === null) {
          // GET /apps/{slug}/versions returns a bare array (no {versions:
          // [...]} envelope — see gateway/routes/apps/publish.py).
          fetch(`/api/apps/${encodeURIComponent(slug)}/versions`)
            .then((r) => (r.ok ? r.json() : []))
            .then((d: AppVersion[]) => setVersions(Array.isArray(d) ? d : []))
            .catch(() => setVersions([]));
        }
        if (usage === null) {
          fetch(`/api/apps/${encodeURIComponent(slug)}/usage`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d: AppUsage | null) => setUsage(d))
            .catch(() => {});
        }
      }
      return next;
    });
  }, [slug, versions, usage]);

  // Close the popover on outside click.
  useEffect(() => {
    if (!showInfo) return;
    const onDown = (e: MouseEvent) => {
      if (infoRef.current && !infoRef.current.contains(e.target as Node)) {
        setShowInfo(false);
        setConfirmVersion(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [showInfo]);

  /** Roll the live pointer back to an older published version. */
  const makeLive = useCallback(
    async (version: number) => {
      setRollbackBusy(true);
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(slug)}/rollback`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version }),
          }
        );
        if (res.ok) {
          setConfirmVersion(null);
          // Refetch app meta + live bundle so the badge and frame match.
          const ares = await fetch(`/api/apps/${encodeURIComponent(slug)}`);
          if (ares.ok) {
            const data = (await ares.json()) as AppMeta;
            if (data) setApp(data);
          }
          const bres = await fetch(
            `/api/apps/${encodeURIComponent(slug)}/bundle?version=live`
          );
          if (bres.ok) setBundle(await bres.text());
        }
      } catch {
        // Best-effort — leave the confirm strip open so the user can retry.
      } finally {
        setRollbackBusy(false);
      }
    },
    [slug]
  );

  const srcDoc = useMemo(
    () => (bundle ? buildAppSrcDoc(bundle, { slug, mode: "live" }) : null),
    [bundle, slug]
  );
  // Same icon pre-resolution as the Workshop's preview — the published run
  // page goes through the exact same sandboxed frame.
  const runIcons = useMemo(
    () => (srcDoc ? extractCcIconNames(srcDoc) : []),
    [srcDoc]
  );

  const canEdit = app?.role === "own" || app?.role === "edit";

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <AppIcon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Loading app…</p>
      </div>
    );
  }

  if (error || !app) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <p className="text-sm text-destructive">{error ?? "App not found."}</p>
        <Button variant="secondary" size="none" layout="" onClick={() => router.push("/build/apps")} className="px-3 sm:px-4 py-2 text-sm">
          Back to Custom Apps
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* ── App chrome header ───────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-card shrink-0">
        <div className="w-8 h-8 rounded-lg border border-border bg-gradient-to-br from-primary/20 to-accent/15 flex items-center justify-center text-base shrink-0">
          {app.icon || "▦"}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-foreground truncate">
              {app.name}
            </span>
            {app.live_version ? (
              <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-success bg-success/10 shrink-0">
                Live · v{app.live_version}
              </span>
            ) : (
              <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-warning bg-warning/10 shrink-0">
                Draft
              </span>
            )}
          </div>
          <div className="text-[11px] text-muted-foreground truncate">
            by {app.owner_email}
          </div>
        </div>
        <div className="flex-1" />
        <span className="hidden sm:flex items-center gap-1.5 text-[11px] text-muted-foreground border border-border rounded-full px-2.5 py-1 shrink-0">
          runs as {viewerEmail}
        </span>
        <Button variant="secondary" size="none" layout="flex items-center" onClick={forkApp} disabled={forking} title="Duplicate this app as your own editable copy" className="px-2 sm:px-3 py-1.5 text-xs gap-1.5 shrink-0">
          {forking ? (
            <AppIcon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <AppIcon name="GitFork" className="w-3.5 h-3.5" />
          )}
          <span className="hidden sm:inline">Fork</span>
        </Button>
        {canEdit && (
          <Button variant="secondary" size="none" layout="flex items-center" onClick={() => router.push(`/build/apps/${slug}/edit`)} title="Open in Workshop" className="px-2 sm:px-3 py-1.5 text-xs gap-1.5 shrink-0">
            <AppIcon name="Wrench" className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Open in Workshop</span>
          </Button>
        )}
        <div className="relative shrink-0" ref={infoRef}>
          <button
            onClick={toggleInfo}
            title="App info"
            className={`p-2 rounded-lg border border-border tech-transition ${
              showInfo
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:bg-secondary"
            }`}
          >
            <AppIcon name="Info" className="w-4 h-4" />
          </button>

          {/* Info popover */}
          {showInfo && (
            <div className="absolute right-0 top-full mt-2 w-80 max-w-[calc(100vw-1.5rem)] rounded-xl border border-border bg-card shadow-lg z-40 p-4 flex flex-col gap-3 text-xs">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">Owner</span>
                <span className="text-foreground truncate">
                  {app.owner_email}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">AI usage</span>
                <span className="text-foreground">
                  {usage
                    ? `${formatTokens(usage.month_tokens)} tokens this month`
                    : "—"}
                </span>
              </div>
              <div className="border-t border-border pt-2.5 flex flex-col gap-1.5">
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  Versions
                </span>
                {versions === null ? (
                  <AppIcon name="Loader2" className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
                ) : versions.length === 0 ? (
                  <span className="text-muted-foreground">
                    No published versions.
                  </span>
                ) : (
                  versions.slice(0, 6).map((v) => {
                    const isCurrent = v.version === app.live_version;
                    return (
                      <div key={v.version} className="flex flex-col gap-1">
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <span className="font-mono text-foreground shrink-0">
                            v{v.version}
                          </span>
                          <span className="truncate flex-1">
                            {v.release_notes || "—"}
                          </span>
                          <span className="shrink-0">
                            {formatDate(v.published_at)}
                          </span>
                          {isCurrent ? (
                            <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full text-success bg-success/10 shrink-0">
                              live
                            </span>
                          ) : canEdit && confirmVersion !== v.version ? (
                            <Button variant="secondary" size="none" radius="keep" layout="" onClick={() => setConfirmVersion(v.version)} className="text-[10px] rounded-md px-2 py-0.5 shrink-0">
                              Make live
                            </Button>
                          ) : null}
                        </div>
                        {canEdit && !isCurrent && confirmVersion === v.version && (
                          <div className="flex items-center gap-1.5">
                            <span className="text-[10px] text-muted-foreground flex-1">
                              Make v{v.version} live?
                            </span>
                            <Button size="none" radius="keep" layout="flex items-center" onClick={() => makeLive(v.version)} disabled={rollbackBusy} className="text-[10px] rounded-md px-2 py-1 gap-1">
                              {rollbackBusy && (
                                <AppIcon name="Loader2" className="w-3 h-3 animate-spin" />
                              )}
                              Confirm
                            </Button>
                            <button
                              onClick={() => setConfirmVersion(null)}
                              className="text-[10px] rounded-md border border-border px-2 py-1 text-muted-foreground hover:text-foreground tech-transition"
                            >
                              Cancel
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {forkError && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-destructive/5 text-xs text-destructive shrink-0">
          <AppIcon name="AlertTriangle" className="w-3.5 h-3.5 shrink-0" />
          {forkError}
          <button
            onClick={() => setForkError(null)}
            className="ml-auto text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── The app, in the sandboxed frame ─────────────────────────── */}
      <div className="flex-1 min-h-0 flex flex-col">
        {srcDoc ? (
          <SandboxedHtml chromeless html={srcDoc} iconNames={runIcons} />
        ) : (
          <div className="flex flex-col items-center justify-center flex-1 gap-3 text-center px-6">
            <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center">
              <AppIcon name="Hammer" className="w-5 h-5 text-primary" />
            </div>
            <p className="text-sm font-medium text-foreground">
              Not published yet
            </p>
            <p className="text-xs text-muted-foreground max-w-xs">
              This app has no live version. Build and publish it from the
              Workshop first.
            </p>
            {canEdit && (
              <Button size="none" layout="" onClick={() => router.push(`/build/apps/${slug}/edit`)} className="px-3 sm:px-4 py-2 text-sm">
                Open Workshop
              </Button>
            )}
          </div>
        )}
      </div>

      {/* ── Status strip ────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-1 border-t border-border bg-card shrink-0 font-mono text-[10.5px] text-muted-foreground">
        <span className="w-1.5 h-1.5 rounded-full bg-success shrink-0" />
        <span className="truncate">
          sandboxed frame · runs as {viewerEmail} · audit logged
        </span>
      </div>

      {/* ── First-open consent interstitial — a blocking gate (§4.8), NOT the
          bottom-right toast pattern: centered, backdrop-blocked, matching
          the Publish modal's own container styling for consistency. ────── */}
      {showConsent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card shadow-lg p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-lg border border-border bg-gradient-to-br from-primary/20 to-accent/15 flex items-center justify-center text-base shrink-0">
                {app.icon || "▦"}
              </div>
              <h2 className="text-base font-bold text-foreground">
                {app.name}{" "}
                <span className="font-normal text-muted-foreground">
                  wants to:
                </span>
              </h2>
            </div>

            <div className="flex flex-col gap-1.5">
              {(app.live_scopes ?? []).map((scope) => {
                const Icon = scopeIcon(scope);
                return (
                  <div
                    key={scope}
                    className="flex items-center gap-2.5 rounded-lg border border-border px-3 py-2.5"
                  >
                    <Icon className="w-4 h-4 text-muted-foreground shrink-0" />
                    <span className="text-sm text-foreground">
                      {describeScope(scope)}
                    </span>
                  </div>
                );
              })}
            </div>

            {consentNotice && (
              <p className="text-xs text-warning">{consentNotice}</p>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="none" layout="" onClick={() => router.push("/build/apps")} className="px-3 sm:px-4 py-2 text-sm">
                Not now
              </Button>
              <Button size="lg" layout="flex items-center" onClick={allowConsent} disabled={consentBusy}>
                {consentBusy && <AppIcon name="Loader2" className="w-4 h-4 animate-spin" />}
                Allow
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Tool-confirm toast — a destructive cc.tools.call() awaiting the
          viewer's approve/deny (RFC §4.4, mockup-app-run.html .toast). ──── */}
      {pendingConfirm && (
        <div className="fixed bottom-5 right-5 z-40 w-[360px] rounded-2xl border border-border bg-popover shadow-lg p-3.5 flex flex-col gap-2.5">
          <div className="flex items-start gap-2.5">
            <AppIcon name="AlertTriangle" className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-[12.5px] font-semibold text-foreground leading-snug">
                <span className="font-bold">{app.name}</span> wants to use{" "}
                <span className="font-mono font-normal">
                  {pendingConfirm.tool}
                </span>
              </p>
              <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                This runs through the Action Broker as you.
              </p>
            </div>
          </div>
          <pre className="rounded-lg border border-border bg-secondary px-2.5 py-2 font-mono text-[11px] text-muted-foreground overflow-auto max-h-40">
            {JSON.stringify(pendingConfirm.args, null, 2)}
          </pre>
          <div className="flex items-center gap-2">
            <label className="mr-auto flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={rememberTool}
                onChange={(e) => setRememberTool(e.target.checked)}
                className="accent-current"
              />
              Always allow for this app
            </label>
            <Button variant="secondary" size="none" layout="" onClick={() => {
                pendingConfirm.resolve({
                  approved: false,
                  remember: rememberTool,
                });
                setPendingConfirm(null);
              }} className="px-3 py-1.5 text-xs">
              Deny
            </Button>
            <Button size="none" layout="" onClick={() => {
                pendingConfirm.resolve({
                  approved: true,
                  remember: rememberTool,
                });
                setPendingConfirm(null);
              }} className="px-3 py-1.5 text-xs">
              Approve
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
