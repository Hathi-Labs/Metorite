/**
 * PKG-6b fences, by render. Each case draws the real component with the
 * package manifest mocked, so a change that drops an install row, an install
 * pane or an install slug fails here (R7). `package_host.md` PKG-6b.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";
import { NAV_SECTIONS, visibleSections } from "@/lib/nav";

const h = vi.hoisted(() => ({
  features: ["projects"] as string[],
  manifestBody: null as unknown,
  refresh: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: unknown }) =>
    createElement("a", { href, ...rest }, children as never),
}));
vi.mock("@/components/ui/Modal", () => ({
  // Always draws its body. A server render reads the bell as closed
  // (`useBellOpen`), and the test reads what the host hands the panel.
  default: (props: { children?: unknown }) => createElement("div", { "data-modal": "" }, props.children as never),
}));
vi.mock("next-auth/react", () => ({ useSession: () => ({ data: null, status: "unauthenticated" }) }));
vi.mock("@/app/tasks/components/UndoToast", () => ({ UndoToastFallback: () => null }));
vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: { ...NO_ACCESS, authenticated: true, features: h.features, is_admin: false },
    loading: false,
    stale: false,
    refresh: async () => {},
  }),
}));
vi.mock("./packageManifest", async (orig) => {
  const real = await orig<typeof import("./packageManifest")>();
  const m = () => (h.manifestBody ? real.parseManifest(h.manifestBody) : real.EMPTY_MANIFEST);
  return {
    ...real,
    usePackageManifest: () => m(),
    usePackageManifestResource: () => ({ data: m(), refresh: h.refresh }),
  };
});
vi.mock("./NeedsYouCard", async (orig) => {
  const real = await orig<typeof import("./NeedsYouCard")>();
  return {
    ...real,
    useNeedsYou: () => ({
      items: [],
      total: 0,
      sources: {},
      byApp: {},
      floors: new Set(),
      loading: false,
      error: null,
      refresh: () => {},
      errors: {},
      act: () => {},
    }),
  };
});

import AccessPage from "@/app/people/access/page";
import AppLauncher from "./AppLauncher";
import { BellPanel, ShellBellHost, bellCount, closeBell } from "./ShellBell";
import type { NeedsYou } from "./NeedsYouCard";

const entry = (key: string, payload: Record<string, unknown>) => ({
  key,
  source: "org",
  install_id: "i-1",
  needs_setup: false,
  ...payload,
});
const body = (contributions: Record<string, unknown[]>) => ({ version: "v1", contributions });

const emptyNeeds = {
  items: [],
  total: 0,
  sources: {},
  byApp: {},
  floors: new Set(),
  loading: false,
  error: null,
  refresh: () => {},
  errors: {},
  act: () => {},
} as unknown as NeedsYou;
const extra = [{ installId: "i1", title: "Finish setting up Acme", href: "/zz-acme/setup" }];

const setupBody = body({
  nav: [entry("acme", { href: "/zz-acme", label: "Acme", name: "Acme", setup_href: "/zz-acme/setup", needs_setup: true })],
});

beforeEach(() => {
  h.features = ["projects"];
  h.manifestBody = null;
  closeBell();
});

describe("bellCount", () => {
  it("adds the setup rows to the feed total", () => {
    expect(bellCount(3, extra)).toBe(4);
    expect(bellCount(0, extra)).toBe(1);
    expect(bellCount(5, [])).toBe(5);
  });
  it("stays undefined until the feed answers", () => {
    expect(bellCount(undefined, extra)).toBeUndefined();
  });
});

describe("BellPanel", () => {
  it("draws the setup rows it is given", () => {
    const html = renderToStaticMarkup(
      createElement(BellPanel, { open: true, onClose: () => {}, needs: emptyNeeds, extra }),
    );
    expect(html).toContain('data-install-need="i1"');
    expect(html).toContain("Finish setting up Acme");
    expect(html).toContain('href="/zz-acme/setup"');
  });
  it("draws none without extra", () => {
    const html = renderToStaticMarkup(
      createElement(BellPanel, { open: true, onClose: () => {}, needs: emptyNeeds }),
    );
    expect(html).not.toContain("data-install-need");
  });
});

describe("ShellBellHost", () => {
  it("hands the manifest's setup rows to the panel", () => {
    h.manifestBody = setupBody;
    const html = renderToStaticMarkup(createElement(ShellBellHost, { placement: "end" }));
    expect(html).toContain("Finish setting up Acme");
  });
});

describe("AppLauncher", () => {
  const held = () => visibleSections(NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? "")), true);
  const draw = () =>
    renderToStaticMarkup(
      createElement(AppLauncher, { open: true, onClose: () => {}, sections: held(), pathname: "/" }),
    );

  it("lists an install pane under Apps, with no repeated name", () => {
    h.manifestBody = body({ nav: [entry("fv", { href: "/zz-fv", label: "Field visits" })] });
    const html = draw();
    expect(html).toContain('href="/zz-fv"');
    expect(html).toContain(">Apps<");
    expect(html).not.toMatch(/Field visits<\/span><span[^>]*>Field visits/);
  });
  it("lists no install pane with the host off", () => {
    expect(draw()).not.toContain("/zz-fv");
  });
});

describe("the My access page", () => {
  const draw = () => renderToStaticMarkup(createElement(AccessPage));
  it("does not call an install-only slug unmapped", () => {
    h.features = ["zz_fv_feature"];
    h.manifestBody = body({ nav: [entry("fv", { href: "/zz-fv", label: "Field visits", feature: "zz_fv_feature" })] });
    expect(draw()).not.toContain("Granted, but with no menu item");
  });
  it("still calls a slug with no home unmapped", () => {
    h.features = ["zz_fv_feature"];
    expect(draw()).toContain("zz_fv_feature");
  });
});
