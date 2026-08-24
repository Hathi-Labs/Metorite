/**
 * The branding rules that decide what a customer sees at the top of the app.
 *
 * The cases worth pinning are the ones a plausible implementation gets wrong
 * quietly: an org with no logo rendering an empty box instead of our mark, an
 * SVG rejected with a message that does not say what to do instead, and a
 * square logo allotted a wordmark's width.
 */
import { describe, expect, it } from "vitest";

import {
  LOGO_MAX_BYTES,
  LOGO_ACCEPT,
  POWERED_BY,
  formatBytes,
  lockup,
  logoBoxWidth,
  precheckLogoFile,
  isRenderableLogoUri,
  readCachedBranding,
  writeCachedBranding,
  type OrgBranding,
  type OrgLogo,
} from "./orgBranding";

const logo = (over: Partial<OrgLogo> = {}): OrgLogo => ({
  dataUri: "data:image/png;base64,AAAA",
  mime: "image/png",
  width: 600,
  height: 160,
  byteSize: 4096,
  ...over,
});

describe("the file pre-check", () => {
  it("accepts the three raster formats", () => {
    for (const type of ["image/png", "image/jpeg", "image/webp"]) {
      expect(precheckLogoFile({ type, size: 20_000 })).toBeNull();
    }
  });

  it("tells an SVG uploader what to do instead of just refusing", () => {
    // SVG is what a designer hands over, so this is the likeliest rejection.
    // "Unsupported file type" sends someone back to guess.
    const msg = precheckLogoFile({ type: "image/svg+xml", size: 4_000 });
    expect(msg).toMatch(/SVG/);
    expect(msg).toMatch(/PNG/);
  });

  it("never lists SVG as acceptable to the picker", () => {
    expect(LOGO_ACCEPT).not.toContain("svg");
  });

  it("rejects an oversized file and says by how much", () => {
    const msg = precheckLogoFile({ type: "image/png", size: LOGO_MAX_BYTES + 1 });
    expect(msg).toMatch(/128 KB/);
  });

  it("rejects an empty file", () => {
    expect(precheckLogoFile({ type: "image/png", size: 0 })).toMatch(/empty/i);
  });

  it("accepts a file exactly at the limit", () => {
    expect(precheckLogoFile({ type: "image/png", size: LOGO_MAX_BYTES })).toBeNull();
  });
});

describe("formatBytes", () => {
  it("reads in the unit a person would use", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(128 * 1024)).toBe("128 KB");
    expect(formatBytes(4 * 1024 * 1024)).toBe("4.0 MB");
  });
});

describe("the lockup", () => {
  it("falls back to our own mark when nothing is uploaded", () => {
    // The failure this guards is an empty box where a logo would be.
    const l = lockup(null, "Control Plane");
    expect(l.kind).toBe("default");
    expect(l).toMatchObject({ title: "Metorite", caption: "Control Plane" });
  });

  it("falls back when the row exists but carries no logo", () => {
    const l = lockup({ logo: null, updatedBy: "a@b.c", updatedAt: "" }, "Home");
    expect(l.kind).toBe("default");
  });

  it("falls back when a stored logo has an empty data URI", () => {
    // A half-written row must not render a broken <img>.
    const l = lockup(
      { logo: logo({ dataUri: "" }), updatedBy: "", updatedAt: "" },
      "Home",
    );
    expect(l.kind).toBe("default");
  });

  it("shows the customer's logo over our attribution", () => {
    const l = lockup({ logo: logo(), updatedBy: "", updatedAt: "" }, "Home");
    expect(l.kind).toBe("org");
    expect(l.caption).toBe(POWERED_BY);
    if (l.kind === "org") expect(l.logo.dataUri).toContain("base64");
  });

  it("D51 — a logo-less org shows its OWN NAME, not the generic caption", () => {
    // With subdomains withdrawn, the chrome is the ONE place a person learns
    // whose workspace they are in. The org name wins over the fallback…
    const named = lockup(null, "Control Plane", "Fracktal Works");
    expect(named).toMatchObject({ kind: "default", caption: "Fracktal Works" });
    // …whitespace does not count as a name…
    const blank = lockup(null, "Control Plane", "   ");
    expect(blank).toMatchObject({ caption: "Control Plane" });
    // …and an uploaded logo still IS the org: the name never displaces it.
    const branded = lockup(
      { logo: logo(), updatedBy: "", updatedAt: "" },
      "Home",
      "Fracktal Works",
    );
    expect(branded.kind).toBe("org");
    expect(branded.caption).toBe(POWERED_BY);
  });

  it("keeps the attribution wording in exactly one place", () => {
    expect(POWERED_BY).toBe("powered by Metorite");
  });
});

describe("logoBoxWidth", () => {
  it("gives a wordmark the width its aspect ratio earns", () => {
    // 600×160 at 28px tall wants 105px.
    expect(logoBoxWidth(logo(), 28, 160)).toBe(105);
  });

  it("does not hand a square mark a wordmark's width", () => {
    // The visible defect: a 1:1 logo floating in the left third of a wide box.
    expect(logoBoxWidth(logo({ width: 200, height: 200 }), 28, 160)).toBe(28);
  });

  it("clamps a very wide mark so it cannot push the nav off the edge", () => {
    expect(logoBoxWidth(logo({ width: 1600, height: 200 }), 28, 160)).toBe(160);
  });

  it("degrades to the full box on nonsense dimensions rather than dividing by zero", () => {
    expect(logoBoxWidth(logo({ width: 0, height: 0 }), 28, 160)).toBe(160);
  });
});

describe("the first-paint cache (OI-3a)", () => {
  const fakeStore = (initial: Record<string, string> = {}) => {
    const map = new Map(Object.entries(initial));
    return {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
      dump: () => Object.fromEntries(map),
    };
  };
  const KEY = "cc-org-branding-v1";
  const good: OrgBranding = {
    logo: logo({ dataUri: "data:image/png;base64,AAAA" }),
    updatedBy: "a@b.c",
    updatedAt: "2026-08-14",
  };

  it("round-trips a logo so the next load paints it without a fetch", () => {
    const s = fakeStore();
    writeCachedBranding(s, good);
    expect(readCachedBranding(s)?.logo?.dataUri).toBe("data:image/png;base64,AAAA");
  });

  it("treats a cached 'no logo' as a real answer, not a miss", () => {
    // Otherwise every org WITHOUT a logo pays the round-trip forever, which is
    // the majority of orgs.
    const s = fakeStore();
    writeCachedBranding(s, { logo: null, updatedBy: "", updatedAt: "" });
    expect(readCachedBranding(s)).toEqual({ logo: null, updatedBy: "", updatedAt: "" });
  });

  it("refuses a stored URI that is not a data:image — localStorage is not a trust boundary", () => {
    // Anything that ever ran on this origin can write this key, and the value
    // becomes an <img src>. A cache hit must be validated like a network body.
    for (const hostile of [
      "javascript:alert(1)",
      "data:text/html;base64,PHNjcmlwdD4=",
      "data:image/svg+xml;base64,PHN2Zz4=",
      "https://attacker.example/logo.png",
      "",
    ]) {
      const s = fakeStore({ [KEY]: JSON.stringify({ logo: { ...logo(), dataUri: hostile } }) });
      expect(readCachedBranding(s), hostile).toBeNull();
    }
  });

  it("accepts only the three raster types the server can produce", () => {
    for (const mime of ["png", "jpeg", "webp"]) {
      expect(isRenderableLogoUri(`data:image/${mime};base64,AAAA`)).toBe(true);
    }
    expect(isRenderableLogoUri("data:image/gif;base64,AAAA")).toBe(false);
  });

  it("survives corrupt JSON rather than throwing during render", () => {
    expect(readCachedBranding(fakeStore({ [KEY]: "{not json" }))).toBeNull();
  });

  it("survives storage being unavailable in both directions", () => {
    // Private mode and blocked storage throw on access. A cache is a nicety;
    // it must never be able to break the shell.
    const throwing = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    };
    expect(readCachedBranding(throwing)).toBeNull();
    expect(() => writeCachedBranding(throwing, good)).not.toThrow();
  });

  it("is a no-op on the server, where there is no storage at all", () => {
    expect(readCachedBranding(undefined)).toBeNull();
    expect(() => writeCachedBranding(undefined, good)).not.toThrow();
  });

  it("clears the key when branding is removed", () => {
    const s = fakeStore();
    writeCachedBranding(s, good);
    writeCachedBranding(s, null);
    expect(readCachedBranding(s)).toBeNull();
  });
});
