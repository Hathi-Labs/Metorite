/**
 * The draft of a package, for the Studio IDE (`ai_studio.md` §6.3, slice G3a).
 *
 * `getPackage` in `realDoor.ts` drops the manifest, because the live door has no use for it.
 * The IDE draws the DRAFT, so this reader keeps it. It calls the same route
 * (`GET /api/studio/packages/{id}`) and adds no backend. It also holds the create call of
 * "Build it" (`POST /api/studio/packages`), the href of the IDE, and the one hash that tells
 * the page when the draft changed.
 */
import { stableJson } from "./dataDoor";
import { appBrief } from "./packageLibrary";
import { StudioOff, type ObjectRow } from "./realDoor";

const PACKAGE_ID = /^[0-9a-fA-F-]{36}$/;

/** The words of a read or a create that failed. They say what happened and the next step. */
export const DRAFT_READ_FAILED = "The draft did not load. Check your connection. The page tries again in a few seconds.";
export const CREATE_FAILED =
  "Studio could not make the app. Nothing was opened. Check your connection, then press Build it again.";
export const CREATE_LIMIT =
  "You hold the most apps that one member can hold. Delete an app you do not need, then press Build it again.";

export interface PackageDraft {
  id: string;
  name: string;
  /** The raw draft manifest. The page reads it through the pure helpers, and trusts none of it. */
  manifest: unknown;
  liveVersion: number | null;
  /** The server hash of the draft: the `based_on` of a save. Null from a server that gives none. */
  manifestHash: string | null;
  objects: ObjectRow[];
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The href of the IDE. `object` picks an object in the tabs. */
export function buildHref(pkg: string, object?: string): string {
  const q = new URLSearchParams({ pkg });
  if (object) q.set("object", object);
  return `/studio/build?${q.toString()}`;
}

/** Read the draft manifest and the object rows of one package. A 404 is the "not on" state. */
export async function getPackageDraft(id: string, fetchImpl: typeof fetch = fetch): Promise<PackageDraft> {
  if (!PACKAGE_ID.test(id)) throw new StudioOff();
  const res = await fetchImpl(`/api/studio/packages/${id}`);
  if (res.status === 404) throw new StudioOff();
  if (!res.ok) throw new Error(DRAFT_READ_FAILED);
  const body: unknown = await res.json().catch(() => ({}));
  const pkg = isObject(body) && isObject(body.package) ? body.package : {};
  const objects = isObject(body) && Array.isArray(body.objects) ? (body.objects.filter(isObject) as unknown as ObjectRow[]) : [];
  return {
    id,
    name: String(pkg.name ?? ""),
    manifest: pkg.manifest ?? null,
    liveVersion: typeof pkg.live_version === "number" ? pkg.live_version : null,
    manifestHash: typeof pkg.manifest_hash === "string" ? pkg.manifest_hash : null,
    objects,
  };
}

/** The same text for the same manifest, whatever the order of its keys. */
export function draftHash(manifest: unknown): string {
  // FNV-1a over the stable JSON. It only tells the page that the draft changed: it is not the
  // server's `manifest_hash`, and nothing sends it anywhere.
  const text = stableJson(manifest ?? null);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${text.length.toString(36)}-${h.toString(16)}`;
}

/** The name of a new app, from the first words of the brief. */
export function nameFromBrief(brief: string): string {
  // The stock brief of "New app" holds no name, so the app is "New app" and not the first words of the ask.
  if (brief.trim() === appBrief("")) return "New app";
  const text = brief.replace(/^\s*build me an app:\s*/i, "").replace(/\s+/g, " ").trim();
  const title = text.split(" ").slice(0, 5).join(" ").slice(0, 60).replace(/[\s.,;:]+$/, "");
  return title ? title[0].toUpperCase() + title.slice(1) : "New app";
}

/** A valid manifest with nothing in it. The id is unique, so a second app never meets a 409. */
export function emptyManifest(brief: string, rand: () => number = Math.random): Record<string, unknown> {
  const name = nameFromBrief(brief);
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^[^a-z]+/, "").slice(0, 36).replace(/-+$/, "") || "app";
  const tail = Math.floor(rand() * 36 ** 5).toString(36).padStart(5, "0");
  return {
    studio: 1,
    id: `${slug}-${tail}`,
    name,
    purpose: brief.replace(/\s+/g, " ").trim().slice(0, 300) || "To be written.",
    connections: [],
    objects: [],
    actions: [],
    views: [],
    workflows: [],
  };
}

/** Make a draft package. It throws an `Error` whose message says what happened and the next step. */
export async function createDraftPackage(
  brief: string, fetchImpl: typeof fetch = fetch,
): Promise<{ id: string; name: string }> {
  let res: Response;
  try {
    res = await fetchImpl("/api/studio/packages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(emptyManifest(brief)),
    });
  } catch {
    throw new Error(CREATE_FAILED);
  }
  if (res.status === 404) throw new StudioOff();
  if (res.status === 409) throw new Error(CREATE_LIMIT);
  if (!res.ok) throw new Error(CREATE_FAILED);
  const body: unknown = await res.json().catch(() => ({}));
  const pkg = isObject(body) && isObject(body.package) ? body.package : {};
  if (typeof pkg.id !== "string" || !PACKAGE_ID.test(pkg.id)) throw new Error(CREATE_FAILED);
  return { id: pkg.id, name: String(pkg.name ?? "") };
}
