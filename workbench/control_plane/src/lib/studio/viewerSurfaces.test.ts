/**
 * J2a fix round 2: no owner surface draws for a viewer.
 *
 * Since J2a, `GET /api/studio/packages/{id}` answers a viewer too: a member who reads an app
 * that another member owns, through an install. The answer says `access: viewer` and holds the
 * INSTALLED version, not the draft. Each other package route stays the owner's, so a viewer gets
 * 404 there. This file holds the readers of that one route.
 *
 * 1. The IDE (`getPackageDraft`, and `fetchDraft` of the draft watch) refuses a viewer with the
 *    "not on" state, as before J2a. So `/studio/build?pkg=` shows "This app is not available to
 *    you", and draws no editor, no Save, no Publish and no Draft badge.
 * 2. The app page (`getPackage`) keeps `access`, and draws only the reads (`page.test.ts`).
 * 3. A scan: each workbench reader of the route itself reads `packageAccess`, and each other
 *    caller of a package path names an owner route. A new reader fails here until it decides.
 *
 * Mutation 1: drop the `packageAccess` check of `getPackageDraft`. "the IDE refuses a viewer"
 * turns red. Mutation 2: read the route in a new file with no `packageAccess`. "each reader of
 * the route reads packageAccess" turns red.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { fetchDraft } from "./draftWatch";
import { getPackageDraft } from "./packageDraft";
import { STUDIO_OFF, getPackage } from "./realDoor";

const ID = "123e4567-e89b-12d3-a456-426614174000";
const reply = (body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;
const VIEWER = { package: { id: ID, name: "Visits", access: "viewer", version: 1, manifest: { id: "v" } }, objects: [] };
const OWNER = { package: { id: ID, name: "Visits", live_version: 2, manifest: { id: "v" } }, objects: [] };

describe("the IDE refuses a viewer, as before J2a", () => {
  it("getPackageDraft says off for a viewer and for an access that it does not know", async () => {
    await expect(getPackageDraft(ID, reply(VIEWER))).rejects.toThrow(STUDIO_OFF);
    for (const access of ["editor", "", null]) {
      await expect(getPackageDraft(ID, reply({ ...OWNER, package: { ...OWNER.package, access } })))
        .rejects.toThrow(STUDIO_OFF);
    }
    await expect(getPackageDraft(ID, reply(OWNER))).resolves.toMatchObject({ id: ID, liveVersion: 2 });
  });
  it("the draft watch of the IDE stops on a viewer, as on a 404", async () => {
    await expect(fetchDraft(ID, reply(VIEWER))).rejects.toThrow(STUDIO_OFF);
  });
  it("the app page keeps the access, so it draws the reads only", async () => {
    expect((await getPackage(ID, reply(VIEWER))).access).toBe("viewer");
    expect((await getPackage(ID, reply(OWNER))).access).toBe("owner");
  });
});

/** `src/` of the workbench. */
const SRC = fileURLToPath(new URL("../..", import.meta.url));

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

/** Each call of a path under `/api/studio/packages/<id>`, by file. */
function packagePaths(): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const file of sources(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/`\/api\/studio\/packages\/\$\{[^}]+\}([^`]*)`/g)) {
      const rel = relative(SRC, file).replace(/\\/g, "/");
      found.set(rel, [...(found.get(rel) ?? []), m[1]]);
    }
  }
  return found;
}

/**
 * The owner routes: a viewer gets 404 on each, so they draw nothing for a viewer. `lifetime`, `keep`
 * and `restore` (PKG-10e) belong to the owner of the private install. A viewer gets `install: null`
 * or 404, so the end-date bar draws nothing for a viewer.
 */
const OWNER_ROUTES = /^\/(draft\?|versions|versions\/\$\{[^}]+\}|delete-preview|lifetime|keep|restore)/;

describe("each workbench caller of a package path", () => {
  it("each reader of the route reads packageAccess", () => {
    const readers = [...packagePaths()].filter(([, tails]) => tails.includes("")).map(([f]) => f).sort();
    // packageDelete.ts sends the DELETE of the route, an owner route (404 for a viewer).
    expect(readers).toEqual(["lib/studio/packageDelete.ts", "lib/studio/packageDraft.ts", "lib/studio/realDoor.ts"]);
    for (const file of ["lib/studio/packageDraft.ts", "lib/studio/realDoor.ts"]) {
      expect(readFileSync(join(SRC, file), "utf8"), file).toContain("packageAccess(");
    }
    const del = readFileSync(join(SRC, "lib/studio/packageDelete.ts"), "utf8");
    const at = del.indexOf("`/api/studio/packages/${encodeURIComponent(id)}`");
    expect(del.slice(at, at + 120)).toContain('method: "DELETE"');
  });
  it("each other package path is an owner route", () => {
    for (const [file, tails] of packagePaths()) {
      for (const tail of tails.filter((t) => t !== "")) expect(tail, file).toMatch(OWNER_ROUTES);
    }
  });
  it("only the app page mounts the app delete, behind the owner guard", () => {
    const users = sources(SRC).filter((f) => readFileSync(f, "utf8").includes("<DeletePackageButton"))
      .map((f) => relative(SRC, f).replace(/\\/g, "/"));
    expect(users).toEqual(["app/studio/package-preview/page.tsx"]);
  });
});
