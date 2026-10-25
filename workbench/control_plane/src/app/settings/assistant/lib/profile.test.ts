// The Assistant pane's pure rules (WS-63 PZ-7).
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EMPTY_PROFILE,
  ProfileError,
  errorText,
  exportPath,
  fetchExport,
  fetchProfile,
  forgetAll,
  mergeAddress,
  mergeStyle,
  nearCap,
  saveAddress,
  saveLearning,
  shortDate,
  sourceLabel,
} from "./profile";

describe("mergeAddress", () => {
  it("keeps the keys the edit does not touch", () => {
    expect(mergeAddress({ name: "Vijay", formality: "casual" }, { greeting: "warm" })).toEqual({
      name: "Vijay",
      formality: "casual",
      greeting: "warm",
    });
  });
  it("drops a cleared key, never sending an empty string or null", () => {
    const out = mergeAddress({ name: "Vijay", greeting: "warm" }, { name: "  ", greeting: null });
    expect(out).toEqual({});
    expect(JSON.stringify(out)).not.toContain('""');
    expect(JSON.stringify(out)).not.toContain("null");
  });
  it("trims a name", () => {
    expect(mergeAddress({}, { name: " Vi " })).toEqual({ name: "Vi" });
  });
});

describe("mergeStyle", () => {
  it("keeps notes when the length changes", () => {
    expect(mergeStyle({ notes: ["Answer first"], language: "en" }, { length: "short" })).toEqual({
      notes: ["Answer first"],
      language: "en",
      length: "short",
    });
  });
  it("drops an emptied notes list and a cleared language", () => {
    expect(mergeStyle({ notes: ["a"], language: "en", length: "short" }, { notes: [], language: "" })).toEqual({
      length: "short",
    });
  });
});

describe("errorText", () => {
  it("reads a pydantic array", () => {
    expect(errorText([{ msg: "Value error, fact too long" }, { msg: "bad kind" }], 422)).toBe(
      "fact too long bad kind",
    );
  });
  it("reads a string detail", () => {
    expect(errorText("at most 40 facts", 422)).toBe("at most 40 facts");
  });
  it("words 404, 503 and the unknown case", () => {
    expect(errorText(undefined, 404)).toMatch(/gone/);
    expect(errorText("unavailable", 503)).toMatch(/not available/);
    expect(errorText(undefined, 500)).toMatch(/did not save/);
  });
});

describe("small helpers", () => {
  it("nearCap warns at 80%", () => {
    expect(nearCap(31, 40)).toBe(false);
    expect(nearCap(32, 40)).toBe(true);
  });
  it("shortDate and sourceLabel", () => {
    expect(shortDate("2026-10-23T11:00:00Z")).toBe("2026-10-23");
    expect(shortDate(undefined)).toBe("");
    expect(sourceLabel("chat")).toBe("Learned in chat");
    expect(sourceLabel(undefined)).toBe("");
  });
});

describe("fetch helpers", () => {
  afterEach(() => vi.unstubAllGlobals());
  const stub = (res: () => Response) => {
    const f = vi.fn(async (_u: string, _i?: RequestInit) => res());
    vi.stubGlobal("fetch", f);
    return f;
  };

  it("the learning switch sends {key:'learning'}", async () => {
    const f = stub(() => new Response(JSON.stringify({ v: 1, learning: false }), { status: 200 }));
    const p = await saveLearning(false);
    expect(p.learning).toBe(false);
    expect(f.mock.calls[0][0]).toBe("/api/auth/me/assistant-profile");
    expect(f.mock.calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ key: "learning", value: false });
  });
  it("an address save sends the whole sub-object under its key", async () => {
    const f = stub(() => new Response("{}", { status: 200 }));
    await saveAddress({ name: "V" });
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ key: "address", value: { name: "V" } });
  });
  it("a 503 on load throws, never the empty profile", async () => {
    stub(() => new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }));
    await expect(fetchProfile()).rejects.toBeInstanceOf(ProfileError);
  });
  it("a network failure throws", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("x"); }));
    await expect(fetchProfile()).rejects.toBeInstanceOf(ProfileError);
  });
  it("a 422 carries the gateway message", async () => {
    stub(() => new Response(JSON.stringify({ detail: [{ msg: "Value error, name too long" }] }), { status: 422 }));
    await expect(saveAddress({ name: "x" })).rejects.toThrow("name too long");
  });
  it("forget everything sends DELETE and returns the empty profile", async () => {
    const f = stub(() => new Response(null, { status: 204 }));
    expect(await forgetAll()).toEqual(EMPTY_PROFILE);
    expect(f.mock.calls[0][1]?.method).toBe("DELETE");
  });
});

// PZ-9: the download. Mutation: drop the `?format=text`, and the text case
// fails. Make a failed export resolve, and the 503 case fails.
describe("fetchExport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks the proxy for each format and names no member", () => {
    expect(exportPath("json")).toBe("/api/auth/me/assistant-profile/export");
    expect(exportPath("text")).toBe("/api/auth/me/assistant-profile/export?format=text");
  });

  it("keeps the bytes and takes the server's file name", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      seen.push(url);
      return new Response("Your assistant profile\n", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="assistant-profile.txt"' },
      });
    }));
    const file = await fetchExport("text");
    expect(seen).toEqual(["/api/auth/me/assistant-profile/export?format=text"]);
    expect(file.filename).toBe("assistant-profile.txt");
    expect(await file.body.text()).toBe("Your assistant profile\n");
  });

  it("falls back to its own name when the header is missing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect((await fetchExport("json")).filename).toBe("assistant-profile.json");
  });

  it("throws a ProfileError on a failed export, never an empty file", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 })));
    await expect(fetchExport("json")).rejects.toBeInstanceOf(ProfileError);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    await expect(fetchExport("text")).rejects.toMatchObject({ status: 0 });
  });
});
