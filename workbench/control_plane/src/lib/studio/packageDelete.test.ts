/**
 * BETA-1 fence (task 1809): the delete of a package follows the contract of A5c-1.
 *
 * Mutation 1: stop following a 202 with its new token. "follows each 202" turns red.
 * Mutation 2: send the first token again after a 202. "sends the new token" turns red.
 * Mutation 3: treat 409 as done. "a 409 shows the new counts" turns red.
 * Mutation 4: drop the Retry-After read. "a 503 says to try again" turns red.
 * Mutation 5: remove the bound on calls. "stops at the bound" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  GONE_LINE, PREVIEW_FAILED_LINE, changedText, classifyStep, deleteAndRefresh, forgetPackage, parsePreview, previewCopy, progressLine, readPreview, runDelete,
} from "./packageDelete";

const ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f";
const wire = (confirm = "t0", records = 12) => ({
  package: { id: ID, name: "Visits", slug: "visits" },
  objects: [{ name: "visit", fields: 4, records, capped: false }],
  totals: { objects: 1, fields: 4, records, records_capped: false, versions: 3, installs: 2, other_member_installs: 1 },
  confirm,
});

interface Reply { status: number; body?: unknown; headers?: Record<string, string> }
function fakeFetch(replies: Reply[]) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const r = replies.shift() ?? { status: 500 };
    return new Response(r.body === undefined ? null : JSON.stringify(r.body), { status: r.status, headers: r.headers });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

describe("the preview", () => {
  it("reads the counts and the token", () => {
    const p = parsePreview(wire());
    expect(p).toMatchObject({ name: "Visits", records: 12, versions: 3, installs: 2, otherMemberInstalls: 1, confirm: "t0" });
    expect(p?.objects).toEqual([{ name: "visit", fields: 4, records: 12, capped: false }]);
  });

  it("refuses an answer with no token", () => {
    expect(parsePreview({ ...wire(), confirm: "" })).toBeNull();
    expect(parsePreview(null)).toBeNull();
  });

  it("the dialog names the counts, says it cannot be undone, and warns about other members", () => {
    const c = previewCopy(parsePreview(wire())!);
    expect(c.subject).toBe("Visits");
    expect(c.body).toContain("1 object, 12 records, 3 versions");
    expect(c.body).toContain("cannot undo");
    expect(c.note).toContain("1 of them reaches another member, who loses the app");
  });

  it("reads the preview from the delete-preview route", async () => {
    const f = fakeFetch([{ status: 200, body: wire() }]);
    const got = await readPreview(ID, f.impl);
    expect(got.ok).toBe(true);
    expect(f.calls[0]).toMatchObject({ url: `/api/studio/packages/${ID}/delete-preview`, method: "GET" });
  });

  it("says a gone package is gone, and says a failed read removed nothing", async () => {
    expect(await readPreview(ID, fakeFetch([{ status: 404, body: {} }]).impl)).toEqual({ ok: false, gone: true, message: GONE_LINE });
    const bad = await readPreview(ID, fakeFetch([{ status: 500, body: {} }]).impl);
    expect(bad).toEqual({ ok: false, gone: false, message: PREVIEW_FAILED_LINE });
    expect(PREVIEW_FAILED_LINE).toMatch(/nothing was deleted.*Try again/);
  });
});

describe("one answer of the DELETE", () => {
  it("200 is done with the records removed", () => {
    expect(classifyStep(200, { deleted: true, records_removed: 7 })).toEqual({ kind: "done", removed: 7 });
  });
  it("202 carries what is left and the new token", () => {
    expect(classifyStep(202, { deleted: false, remaining: 300, remaining_capped: true, records_removed: 500, confirm: "t1" }))
      .toEqual({ kind: "more", confirm: "t1", remaining: 300, capped: true, removed: 500 });
  });
  it("a 202 with no token is a failure, not a loop", () => {
    expect(classifyStep(202, { deleted: false, remaining: 3 }).kind).toBe("failed");
  });
  it("404 is gone", () => {
    expect(classifyStep(404, { detail: "Not found" })).toEqual({ kind: "gone" });
  });
  it("a 409 shows the new counts and the records that already went", () => {
    const r = classifyStep(409, { detail: { code: "counts_changed", records_removed: 500, preview: wire("t9", 3) } });
    expect(r.kind).toBe("changed");
    if (r.kind !== "changed") return;
    expect(r.preview?.confirm).toBe("t9");
    expect(r.preview?.records).toBe(3);
    expect(r.message).toContain("500 records already went");
    expect(r.message).toContain("confirm again");
  });
  it("a 503 says to try again, with the wait of Retry-After", () => {
    const r = classifyStep(503, { detail: { code: "store_busy" } }, "2");
    expect(r).toEqual({ kind: "busy", message: "This app is busy now. Try again in 2 seconds." });
    expect(classifyStep(503, {}, null)).toMatchObject({ message: expect.stringContaining("Try again in a moment") });
  });
  it("any other status is a failure that says what to do", () => {
    const r = classifyStep(500, {});
    expect(r.kind).toBe("failed");
    if (r.kind === "failed") expect(r.message).toMatch(/Try again/);
  });
});

describe("the delete loop", () => {
  it("sends DELETE with the token and stops at 200", async () => {
    const f = fakeFetch([{ status: 200, body: { deleted: true, records_removed: 12 } }]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl });
    expect(end).toMatchObject({ kind: "done", totalRemoved: 12 });
    expect(f.calls).toEqual([{ url: `/api/studio/packages/${ID}`, method: "DELETE", body: { confirm: "t0" } }]);
  });

  it("follows each 202 with its new token, shows the progress, and adds up the records", async () => {
    const f = fakeFetch([
      { status: 202, body: { deleted: false, remaining: 900, remaining_capped: false, records_removed: 500, confirm: "t1" } },
      { status: 202, body: { deleted: false, remaining: 400, remaining_capped: false, records_removed: 500, confirm: "t2" } },
      { status: 200, body: { deleted: true, records_removed: 400 } },
    ]);
    const seen: string[] = [];
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl, onProgress: (r, c) => seen.push(progressLine(r, c)) });
    expect(end).toMatchObject({ kind: "done", totalRemoved: 1400 });
    expect(f.calls.map((c) => (c.body as { confirm: string }).confirm)).toEqual(["t0", "t1", "t2"]);
    expect(seen).toEqual(["Deleting. 900 records left.", "Deleting. 400 records left."]);
  });

  it("a 409 after a 202 stops, shows the new counts, and keeps the records count", async () => {
    const f = fakeFetch([
      { status: 202, body: { deleted: false, remaining: 900, remaining_capped: false, records_removed: 500, confirm: "t1" } },
      { status: 409, body: { detail: { code: "counts_changed", records_removed: 0, preview: wire("t7", 5) } } },
    ]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl });
    expect(end.kind).toBe("changed");
    if (end.kind === "changed") expect(end.preview?.confirm).toBe("t7");
    expect(end.totalRemoved).toBe(500);
  });

  it("a 503 stops with the busy words and the last token", async () => {
    const f = fakeFetch([
      { status: 202, body: { deleted: false, remaining: 900, remaining_capped: false, records_removed: 500, confirm: "t1" } },
      { status: 503, body: { detail: { code: "store_busy" } }, headers: { "Retry-After": "3" } },
    ]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl });
    expect(end).toMatchObject({ kind: "busy", confirm: "t1", message: "This app is busy now. Try again in 3 seconds." });
  });

  it("the 409 text counts the records of the whole run", async () => {
    const f = fakeFetch([
      { status: 202, body: { deleted: false, remaining: 900, remaining_capped: false, records_removed: 500, confirm: "t1" } },
      { status: 409, body: { detail: { code: "counts_changed", records_removed: 200, preview: wire("t7", 5) } } },
    ]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl });
    expect(end).toMatchObject({ kind: "changed", totalRemoved: 700, message: changedText(700) });
    expect(changedText(700)).toContain("700 records already went");
  });

  it("a 404 means the package is gone", async () => {
    const end = await runDelete(ID, "t0", { fetchImpl: fakeFetch([{ status: 404, body: {} }]).impl });
    expect(end.kind).toBe("gone");
  });

  it("a refused connection says nothing more was deleted and what to do", async () => {
    const down = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
    const end = await runDelete(ID, "t0", { fetchImpl: down });
    expect(end.kind).toBe("failed");
    if (end.kind === "failed") expect(end.message).toMatch(/Some records may already be deleted.*confirm/);
  });

  it("stops at the bound of calls", async () => {
    const more = { status: 202, body: { deleted: false, remaining: 1, remaining_capped: false, records_removed: 1, confirm: "t" } };
    const f = fakeFetch(Array.from({ length: 10 }, () => ({ ...more })));
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl, maxCalls: 3 });
    expect(end.kind).toBe("failed");
    expect(f.calls).toHaveLength(3);
  });
});

/** A server that checks the token like the gateway: a token that is not the token of now is 409. */
function serverWithToken(state: { token: string; records: number }) {
  const calls: Array<{ method: string; token?: string }> = [];
  let busyOnce = true;
  const impl = (async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const token = init?.body ? (JSON.parse(String(init.body)) as { confirm: string }).confirm : undefined;
    calls.push({ method, token });
    const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
    if (method === "GET") return json(200, wire(state.token, state.records));
    if (token !== state.token) return json(409, { detail: { code: "counts_changed", records_removed: 0, preview: wire(state.token, state.records) } });
    if (busyOnce) { busyOnce = false; state.records = 700; state.token = "t-after-busy"; return json(503, { detail: { code: "store_busy" } }); }
    return json(200, { deleted: true, records_removed: state.records });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

describe("after a busy end, the next Confirm sends the token of the new counts", () => {
  it("reads the preview again, and the retry passes with no 409", async () => {
    const server = serverWithToken({ token: "t0", records: 1200 });
    const end = await deleteAndRefresh(ID, "t0", { fetchImpl: server.impl });
    expect(end.kind).toBe("busy");
    expect(end.fresh?.confirm).toBe("t-after-busy");
    expect(end.fresh?.records).toBe(700);
    const retry = await runDelete(ID, end.fresh!.confirm, { fetchImpl: server.impl });
    expect(retry.kind).toBe("done");
    expect(server.calls.map((c) => c.method)).toEqual(["DELETE", "GET", "DELETE"]);
  });

  it("the old token of the busy end would get a 409, so it is not sent again", async () => {
    const server = serverWithToken({ token: "t0", records: 1200 });
    await deleteAndRefresh(ID, "t0", { fetchImpl: server.impl });
    const stale = await runDelete(ID, "t0", { fetchImpl: server.impl });
    expect(stale.kind).toBe("changed");
  });

  it("a package that is gone at the re-read is reported as gone", async () => {
    const f = fakeFetch([{ status: 503, body: {} }, { status: 404, body: {} }]);
    const end = await deleteAndRefresh(ID, "t0", { fetchImpl: f.impl });
    expect(end.vanished).toBe(true);
  });
});

describe("a page that left stops the loop", () => {
  it("makes no call once the signal is aborted, and says aborted", async () => {
    const c = new AbortController();
    c.abort();
    const f = fakeFetch([{ status: 200, body: { deleted: true, records_removed: 1 } }]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl, signal: c.signal });
    expect(end.kind).toBe("aborted");
    expect(f.calls).toHaveLength(0);
  });

  it("stops between two calls when the page leaves after a 202", async () => {
    const c = new AbortController();
    const f = fakeFetch([
      { status: 202, body: { deleted: false, remaining: 900, remaining_capped: false, records_removed: 500, confirm: "t1" } },
      { status: 200, body: { deleted: true, records_removed: 900 } },
    ]);
    const end = await runDelete(ID, "t0", { fetchImpl: f.impl, signal: c.signal, onProgress: () => c.abort() });
    expect(end.kind).toBe("aborted");
    expect(f.calls).toHaveLength(1);
  });

  it("the button aborts on unmount and never navigates after it", () => {
    const src = readFileSync(new URL("../../components/studio/DeletePackageButton.tsx", import.meta.url), "utf8");
    expect(src).toContain("stop.current?.abort()");
    expect(src).toContain("if (!alive.current");
  });
});

describe("a deleted package leaves no cached page", () => {
  it("forgets the page key, the door reads and the list", () => {
    const src = readFileSync(new URL("./packageDelete.ts", import.meta.url), "utf8");
    expect(src).toContain("studio:package-preview:pkg:${id}");
    expect(src).toContain("studio:${id}:live:");
    expect(typeof forgetPackage).toBe("function");
    const page = readFileSync(new URL("../../app/studio/package-preview/page.tsx", import.meta.url), "utf8");
    expect(page).toContain("detail.error === STUDIO_OFF || (detail.error && detail.data === undefined)");
    const btn = readFileSync(new URL("../../components/studio/DeletePackageButton.tsx", import.meta.url), "utf8");
    expect(btn).toContain("forgetPackage(id)");
  });
});

describe("the button", () => {
  const src = readFileSync(new URL("../../components/studio/DeletePackageButton.tsx", import.meta.url), "utf8");
  it("asks first: the preview opens the dialog, and the dialog confirms", () => {
    expect(src).toContain("readPreview(id)");
    expect(src).toContain("<ConfirmDialog");
    expect(src).toContain("deleteAndRefresh(id, preview.confirm");
  });
  it("keeps one label for the confirm act", () => {
    expect(src).not.toContain("Confirm delete");
  });
  it("returns to the library when the package is gone", () => {
    expect(src).toMatch(/end\.kind === "done" \|\| end\.kind === "gone"/);
    expect(src).toContain("onDeleted()");
  });
});
