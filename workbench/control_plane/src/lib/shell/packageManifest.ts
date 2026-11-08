/**
 * The resolved package manifest, as the workbench reads it (`package_host.md`
 * §4 and §5.2, PKG-6).
 *
 * `GET /api/shell/manifest` proxies the gateway's `GET /shell/manifest`. That
 * route answers 404 for an organization outside `PACKAGE_HOST_ORGS`. So the
 * flag is the SERVER's, and this file adds none. A 404, or any error, reads as
 * an empty manifest, and then every helper here returns nothing and the shell
 * draws exactly what it drew before.
 *
 * The shell already holds the `system` entries in code, so they are ignored.
 * Only an entry with an `install_id` counts. Every helper is pure and
 * defensive: a shape it does not know is dropped and never thrown.
 *
 * Rules for an install entry:
 *
 * - **System wins.** An install pane whose href a system pane owns is dropped.
 *   An install job whose id a system job owns is dropped.
 * - **Same-origin paths only.** An href must start with one "/" and hold no
 *   scheme. `javascript:` and `https://…` are dropped.
 * - **A feature gate holds.** An entry that names a feature shows only to a
 *   member who holds it.
 *
 * Fence (R7): `packageManifest.test.ts`.
 */
import { useLiveRefresh } from "@/lib/liveRefresh";
import type { NavPane, NavSection } from "@/lib/nav";
import { NAV_SECTIONS } from "@/lib/nav";
import { useCachedResource } from "@/lib/useCachedResource";
import { MANIFEST_CACHE, manifestKey, refreshManifest } from "./manifestCache";

export { MANIFEST_CACHE, manifestKey, refreshManifest };

export type ManifestSource = "system" | "org" | "group" | "private";

export interface ManifestEntry {
  key: string;
  source: ManifestSource;
  installId: string | null;
  needsSetup: boolean;
  /** Every other field of the entry. */
  payload: Record<string, unknown>;
}

export interface PackageManifest {
  version: string;
  contributions: Record<string, ManifestEntry[]>;
}

export const EMPTY_MANIFEST: PackageManifest = { version: "", contributions: {} };

const SOURCES: ReadonlySet<string> = new Set(["system", "org", "group", "private"]);
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** A same-origin path: one leading "/", no scheme, no "//" host, no control character. */
export function safeHref(v: unknown): string | null {
  if (typeof v !== "string") return null;
  if (!v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) return null;
  if (/[\u0000-\u001f\\]/.test(v)) return null;
  return v;
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** Read a body into a manifest. A shape it does not know is dropped. Never throws. */
export function parseManifest(body: unknown): PackageManifest {
  if (!isRecord(body) || !isRecord(body.contributions)) return EMPTY_MANIFEST;
  const contributions: Record<string, ManifestEntry[]> = {};
  for (const [point, list] of Object.entries(body.contributions)) {
    if (!Array.isArray(list)) continue;
    const entries: ManifestEntry[] = [];
    for (const raw of list) {
      if (!isRecord(raw)) continue;
      const { key, source, install_id, needs_setup, ...payload } = raw;
      if (typeof key !== "string" || !key) continue;
      if (typeof source !== "string" || !SOURCES.has(source)) continue;
      if (install_id !== null && install_id !== undefined && typeof install_id !== "string") continue;
      entries.push({
        key,
        source: source as ManifestSource,
        installId: typeof install_id === "string" && install_id ? install_id : null,
        needsSetup: needs_setup === true,
        payload,
      });
    }
    contributions[point] = entries;
  }
  return { version: typeof body.version === "string" ? body.version : "", contributions };
}

/** The entries of one point that an install gave. */
function installed(m: PackageManifest, point: string): ManifestEntry[] {
  return (m.contributions[point] ?? []).filter((e) => e.source !== "system" && e.installId !== null);
}

const holds = (feature: unknown, features: readonly string[]): boolean =>
  feature === undefined || feature === null || (typeof feature === "string" && features.includes(feature));

/** Whether a system pane owns `href`, exactly or as its parent path. */
function systemOwns(href: string): boolean {
  const path = href.split(/[?#]/)[0];
  return NAV_SECTIONS.some((s) =>
    s.items.some((p) => path === p.href || path.startsWith(`${p.href}/`)),
  );
}

/** The team of an install pane. `TEAM_GROUPS` names it "Apps". */
export const INSTALL_TEAM = "apps";

/** The nav panes the installs give, ready for the "Apps" section. */
export function installNavPanes(m: PackageManifest, features: readonly string[] = []): NavPane[] {
  const seen = new Set<string>();
  const out: NavPane[] = [];
  for (const e of installed(m, "nav")) {
    const href = safeHref(e.payload.href);
    const label = text(e.payload.label);
    if (!href || !label || !holds(e.payload.feature, features)) continue;
    if (systemOwns(href) || seen.has(href)) continue;
    seen.add(href);
    out.push({
      href,
      label,
      icon: text(e.payload.icon) ?? "Box",
      // An empty note, not the label again: the launcher card would print the
      // name twice. A payload `note` or `description` gives the line.
      note: text(e.payload.note) ?? text(e.payload.description) ?? "",
      launch: "live",
      // The sidebar lists an install pane under "Apps" (`mergeInstallNav`),
      // and the launcher group "Apps" holds the same panes (`TEAM_GROUPS`).
      team: INSTALL_TEAM,
      ...(typeof e.payload.feature === "string" ? { feature: e.payload.feature } : {}),
    });
  }
  return out;
}

/**
 * Add the install panes to the "Apps" section, AFTER `visibleSections` ran.
 * With nothing to add, the SAME array comes back, so the flag-off shell is
 * the shell it was.
 */
export function mergeInstallNav(sections: readonly NavSection[], panes: readonly NavPane[]): NavSection[] {
  if (panes.length === 0) return sections as NavSection[];
  const taken = new Set(sections.flatMap((s) => s.items.map((p) => p.href)));
  const fresh = panes.filter((p) => !taken.has(p.href));
  if (fresh.length === 0) return sections as NavSection[];
  if (sections.some((s) => s.id === "apps")) {
    return sections.map((s) => (s.id === "apps" ? { ...s, items: [...s.items, ...fresh] } : s));
  }
  const at = sections.findIndex((s) => s.id === "ai-studio" || s.id === "admin");
  const apps: NavSection = { id: "apps", label: "Apps", items: fresh };
  return at < 0 ? [...sections, apps] : [...sections.slice(0, at), apps, ...sections.slice(at)];
}

export interface InstallJob {
  id: string;
  label: string;
  words: readonly string[];
  href: string;
  icon: string;
}

/** The ⌘K jobs the installs give. */
export function installJobs(m: PackageManifest, features: readonly string[] = []): InstallJob[] {
  const seen = new Set<string>();
  const out: InstallJob[] = [];
  for (const e of installed(m, "jobs")) {
    const href = safeHref(e.payload.href);
    const label = text(e.payload.label);
    if (!href || !label || !holds(e.payload.feature, features) || seen.has(e.key)) continue;
    seen.add(e.key);
    const words = Array.isArray(e.payload.words)
      ? e.payload.words.filter((w): w is string => typeof w === "string")
      : [];
    out.push({ id: e.key, label, words, href, icon: text(e.payload.icon) ?? "Zap" });
  }
  return out;
}

export interface InstallNeed {
  installId: string;
  title: string;
  /** Where to finish the setup. A row with no safe `setup_href` is not made. */
  href: string;
}

/**
 * One "Finish setting up <name>" row for each install that needs setup AND
 * names a safe same-origin `setup_href`. A row with no next step never clears,
 * so it is dropped, and the bell's badge counts this one list (PKG-Q11).
 */
export function installNeeds(m: PackageManifest): InstallNeed[] {
  const seen = new Set<string>();
  const out: InstallNeed[] = [];
  for (const list of Object.values(m.contributions)) {
    for (const e of list) {
      if (e.source === "system" || e.installId === null || !e.needsSetup || seen.has(e.installId)) continue;
      const href = safeHref(e.payload.setup_href);
      if (!href) continue;
      seen.add(e.installId);
      const name = text(e.payload.name) ?? text(e.payload.label) ?? e.key;
      out.push({
        installId: e.installId,
        title: `Finish setting up ${name}`,
        href,
      });
    }
  }
  return out;
}

/**
 * The feature slugs that installs declare, over every point. /auth/me
 * `features` holds them while the package host is on, so the access report
 * reads this list to know they have a home (PKG-6b).
 */
export function installFeatureSlugs(m: PackageManifest): string[] {
  const out = new Set<string>();
  for (const list of Object.values(m.contributions)) {
    for (const e of list) {
      if (e.source === "system" || e.installId === null) continue;
      const f = text(e.payload.feature);
      if (f) out.add(f);
    }
  }
  return [...out].sort();
}

/** A paused install leaves the shell within this time (`package_host.md` §5.2). */
export const MANIFEST_TTL_MS = 10_000;

/** Read the manifest. A 404 (flag off) or any failure is an empty manifest. */
export async function fetchManifest(): Promise<PackageManifest> {
  try {
    const res = await fetch("/api/shell/manifest", { cache: "no-store" });
    if (!res.ok) return EMPTY_MANIFEST;
    return parseManifest(await res.json());
  } catch {
    return EMPTY_MANIFEST;
  }
}

/**
 * The manifest resource, from the cache. `refresh` forces a new read.
 *
 * PKG-8a: a Studio publish or save of the assistant reads it again at once, so the
 * owner's new sidebar entry shows with no reload. A delete on the app page drops it
 * through `forgetPackage`.
 */
export function usePackageManifestResource(enabled: boolean = true) {
  const r = useCachedResource<PackageManifest>(enabled ? manifestKey() : null, fetchManifest, {
    ttl: MANIFEST_TTL_MS,
    revalidateOnFocus: true,
  });
  useLiveRefresh(refreshManifest, "studio");
  return r;
}

/** The manifest, from the cache. It is empty while it loads and when the flag is off. */
export function usePackageManifest(enabled: boolean = true): PackageManifest {
  return usePackageManifestResource(enabled).data ?? EMPTY_MANIFEST;
}

/**
 * The nav sections with the panes of the installs in "Apps". The desktop sidebar and
 * the phone drawer both call it, so the two navs show the same installs. With the
 * package host off, the SAME array comes back.
 */
export function useInstallNav(sections: readonly NavSection[], features: readonly string[]): NavSection[] {
  const manifest = usePackageManifest();
  return mergeInstallNav(sections, installNavPanes(manifest, features));
}
