/**
 * People · the directory list must reach the gateway (R7 fence).
 *
 * One claim, and it is the whole of the bug this replaces:
 *
 * > The request that loads the directory must hit the proxy route, not Next's
 * > own 404 page.
 *
 * **What went wrong.** The list is `GET /people/` upstream, so the client asked
 * for `/api/people/`. The proxy was a REQUIRED catch-all (`[...path]`), which
 * matches one segment or more and never the bare path. Next stripped the
 * trailing slash, found no route, and served an HTML 404. `call()` then ran
 * `JSON.parse` on `<!DOCTYPE html>`, which is the literal error a member saw:
 *
 *     Unexpected token '<', "<!DOCTYPE "... is not valid JSON
 *
 * **Why nobody noticed for so long.** `/people` is a `preview` pane with no nav
 * entry, so the surface was only ever opened on purpose. The list had never
 * loaded once, in any environment.
 *
 * **The second symptom was the expensive one.** `can_manage` rides on that same
 * response. With the read failing, the flag stayed false, the "Add person"
 * control never drew, and an admin had no way to add anybody to the directory
 * at all — including themselves.
 *
 * Two halves fail independently, so both are pinned here. Reverting the folder
 * name breaks the first. Gluing the slash back on in `call()` breaks the
 * second, because Next answers `/api/people/` with a 308 before any route runs.
 */

import { existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { peopleApi } from "./api";

const PROXY_DIR = fileURLToPath(
  new URL("../../api/people", import.meta.url)
);

/** Install a `fetch` that records its URL and answers with an empty list. */
function captureFetch(): { urls: string[] } {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      urls.push(String(input));
      return Promise.resolve(
        new Response(
          JSON.stringify({
            rows: [],
            total: 0,
            hr_visible: true,
            can_manage: true,
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
      );
    })
  );
  return { urls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the /api/people proxy can match the bare path", () => {
  it("is an OPTIONAL catch-all", () => {
    // `[[...path]]` matches zero segments as well as many. `[...path]` does
    // not, and that single pair of brackets was the outage.
    expect(existsSync(`${PROXY_DIR}/[[...path]]/route.ts`)).toBe(true);
  });

  it("has no required catch-all left beside it", () => {
    // Two route folders that both claim `/api/people/*` is ambiguous, and Next
    // resolves it in a way nobody here should have to remember.
    const dirs = readdirSync(PROXY_DIR);
    expect(dirs).not.toContain("[...path]");
  });
});

describe("the directory list asks for a routable URL", () => {
  it("omits the trailing slash when it carries no filters", async () => {
    const { urls } = captureFetch();

    await peopleApi.directory();

    // `/api/people/` would 308 to `/api/people` before the route is consulted.
    // The redirect happens to survive a GET, so this is about never depending
    // on it — a POST or PATCH built the same way would not be so lucky.
    expect(urls).toEqual(["/api/people"]);
  });

  it("puts the query straight onto the bare path", async () => {
    const { urls } = captureFetch();

    await peopleApi.directory({ q: "ada", status: "active" });

    expect(urls[0]).toBe("/api/people?q=ada&status=active");
    expect(urls[0]).not.toContain("/?");
  });

  it("still puts a named endpoint on a segment", async () => {
    // The fix must not flatten the paths that were always correct.
    const { urls } = captureFetch();

    await peopleApi.facets();

    expect(urls[0]).toBe("/api/people/facets");
  });
});
