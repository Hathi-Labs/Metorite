/**
 * The branding rules that decide what a customer sees at the top of the app.
 *
 * The cases worth pinning are the ones a plausible implementation gets wrong
 * quietly: an org with no logo rendering an empty box instead of our mark, a
 * file the editor could have opened refused at the door, and a square logo
 * allotted a wordmark's width.
 */
import { describe, expect, it } from "vitest";

import {
  LOGO_MAX_BYTES,
  LOGO_PICK_MAX_BYTES,
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
  // Since 2026-10-09 the editor draws a small PNG from whatever the admin
  // picks (`logoCanvas.ts`), so the pre-check refuses only what cannot be
  // opened at all. The stored file is never an SVG, and never large.
  it("opens every common image, SVG included", () => {
    for (const type of ["image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"]) {
      expect(precheckLogoFile({ type, size: 2_000_000 })).toBeNull();
    }
  });

  it("refuses a file that is not an image, and says which ones are", () => {
    const msg = precheckLogoFile({ type: "application/pdf", size: 4_000 });
    expect(msg).toMatch(/PNG/);
    expect(msg).toMatch(/SVG/);
  });

  it("refuses only a file too big to open, not one too big to store", () => {
    expect(precheckLogoFile({ type: "image/png", size: LOGO_MAX_BYTES * 10 })).toBeNull();
    expect(precheckLogoFile({ type: "image/png", size: LOGO_PICK_MAX_BYTES + 1 })).toMatch(/20\.0 MB/);
  });

  it("rejects an empty file", () => {
    expect(precheckLogoFile({ type: "image/png", size: 0 })).toMatch(/empty/i);
  });
});

describe("the lockup in dark mode", () => {
  const base = (over: Partial<OrgBranding>): OrgBranding => ({ logo: logo(), updatedBy: "", updatedAt: "", ...over });

  it("shows the white version in dark mode with the white style", () => {
    const white = logo({ dataUri: "data:image/png;base64,BBBB" });
    const l = lockup(base({ logoDark: white, darkStyle: "white" }), "x");
    expect(l.kind === "org" && l.logoDark.dataUri).toBe("data:image/png;base64,BBBB");
  });

  it("shows the organisation's own dark version, with no card, with the own style", () => {
    const mine = logo({ dataUri: "data:image/png;base64,CCCC" });
    const l = lockup(base({ logoDark: mine, darkStyle: "own" }), "x");
    expect(l.kind === "org" && [l.plate, l.logoDark.dataUri]).toEqual([false, "data:image/png;base64,CCCC"]);
  });

  it("shows the logo itself on a light card with the plate style", () => {
    const l = lockup(base({ darkStyle: "plate" }), "x");
    expect(l.kind === "org" && [l.plate, l.logoDark.dataUri]).toEqual([true, logo().dataUri]);
  });

  it("shows the logo itself in both modes for a row written before dark styles", () => {
    const l = lockup(base({}), "x");
    expect(l.kind === "org" && [l.plate, l.logoDark.dataUri]).toEqual([false, logo().dataUri]);
  });

  it("falls back to the logo when the white style has no image", () => {
    const l = lockup(base({ darkStyle: "white", logoDark: null }), "x");
    expect(l.kind === "org" && l.logoDark.dataUri).toBe(logo().dataUri);
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

  it("drops a bad cached own dark image and keeps the logo", () => {
    const s = fakeStore();
    writeCachedBranding(s, {
      ...good,
      logoDark: { ...good.logo!, dataUri: "javascript:alert(1)" },
      darkStyle: "own",
    });
    const read = readCachedBranding(s);
    expect(read?.logoDark).toBeNull();
    expect(read?.darkStyle).toBe("same");
  });

  it("drops a bad cached dark image and keeps the logo", () => {
    const s = fakeStore();
    writeCachedBranding(s, {
      ...good,
      logoDark: { ...good.logo!, dataUri: "javascript:alert(1)" },
      darkStyle: "white",
    });
    const read = readCachedBranding(s);
    expect(read?.logo?.dataUri).toBe("data:image/png;base64,AAAA");
    expect(read?.logoDark).toBeNull();
    expect(read?.darkStyle).toBe("same");
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
