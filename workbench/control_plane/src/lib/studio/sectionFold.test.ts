/** STU-AF7 fence: the folded state of a section is kept for the viewer, and never breaks the page. */
import { describe, expect, it } from "vitest";

import { foldKey, readFolded, viewerStorage, writeFolded } from "./sectionFold";

function fake(): Storage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  } as unknown as Storage & { data: Map<string, string> };
}

const broken = {
  getItem: () => { throw new Error("blocked"); },
  setItem: () => { throw new Error("full"); },
  removeItem: () => { throw new Error("blocked"); },
};

describe("foldKey", () => {
  it("names the app, the page and the section, in one namespace", () => {
    expect(foldKey("field-visits", "main", "open")).toBe("studio.fold.v1:field-visits:main:open");
  });
  it("gives two sections two keys, even when a part holds the separator", () => {
    expect(foldKey("a:b", "c", "d")).not.toBe(foldKey("a", "b:c", "d"));
  });
});

describe("readFolded and writeFolded", () => {
  it("is open by default", () => {
    expect(readFolded(fake(), foldKey("a", "p", "s"))).toBe(false);
  });
  it("keeps a fold across a remount, and an unfold drops the key", () => {
    const s = fake();
    const k = foldKey("a", "p", "s");
    writeFolded(s, k, true);
    expect(readFolded(s, k)).toBe(true); // a second mount reads the same storage
    expect(readFolded(s, foldKey("a", "p", "other"))).toBe(false);
    writeFolded(s, k, false);
    expect(s.data.size).toBe(0);
    expect(readFolded(s, k)).toBe(false);
  });
  it("treats any other stored value as open", () => {
    const s = fake();
    s.setItem("k", "yes");
    expect(readFolded(s, "k")).toBe(false);
  });
  it("does not throw when the storage throws, and a section stays open", () => {
    expect(readFolded(broken, "k")).toBe(false);
    expect(() => writeFolded(broken, "k", true)).not.toThrow();
    expect(() => writeFolded(broken, "k", false)).not.toThrow();
  });
  it("does not throw with no storage", () => {
    expect(readFolded(null, "k")).toBe(false);
    expect(() => writeFolded(null, "k", true)).not.toThrow();
    expect(viewerStorage()).toBeNull(); // the node env has no window
  });
});
