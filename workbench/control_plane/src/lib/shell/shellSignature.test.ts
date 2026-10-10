/**
 * Our comet signs the shell bar's right end (owner, 2026-10-11).
 *
 * The owner first put a "Metorite" signature at the foot of the sidebar, and
 * then moved it to the top right of the bar on the same day. These checks
 * fail on the wrong builds that look right:
 *   1. The comet is not the last item of the bar's right group.
 *   2. The comet became a control: a button, a link, a focus stop, a hover.
 *   3. The comet came back to the sidebar or to the phone drawer.
 *   4. A workspace with no logo signs the bar twice. Its left end already
 *      draws our comet, so the right end draws none.
 * The geometry in a browser is `e2e/shell-signature.spec.ts`.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ourMarkLeads, type OrgBranding } from "@/lib/orgBranding";
import { BarSignature, ShellBarRow } from "./ShellBar";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
/** Source without comments, so a comment cannot satisfy or break a check. */
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

const sig = renderToStaticMarkup(createElement(BarSignature));

describe("the comet is the bar's last item", () => {
  it("the row draws it after the activity control, at the end of the right group", () => {
    const html = renderToStaticMarkup(
      createElement(ShellBarRow, {
        here: null,
        onOpen: () => {},
        activity: createElement("i", { "data-zone": "activity" }),
        signature: createElement(BarSignature),
      }),
    );
    // The gradient id is React's `useId`, so it differs per render.
    const noId = (t: string) => t.replace(/metorite-comet-[A-Za-z0-9_-]+/g, "metorite-comet-·");
    expect(noId(html).endsWith(`<i data-zone="activity"></i>${noId(sig)}</div></header>`)).toBe(true);
  });

  it("ShellFrame passes BarSignature to the row", () => {
    expect(code(read("lib/shell/ShellBar.tsx"))).toMatch(/signature=\{signed \? <BarSignature \/> : null\}/);
  });
});

describe("the comet is a signature, not a control", () => {
  it("is the comet alone at 20px, named by its image and a native title", () => {
    expect(sig).toContain("data-metorite-mark");
    expect(sig).toMatch(/<svg[^>]*width="20" height="20"/);
    expect(sig).toContain('role="img"');
    expect(sig).toContain('aria-label="Metorite"');
    expect(sig).toContain('title="Metorite"');
    // No words beside it.
    expect(sig.replace(/<[^>]*>/g, "")).toBe("");
  });

  it("has no button, no link, no focus stop and no hover fill", () => {
    expect(sig).not.toMatch(/<a\b|<button\b|tabindex|href=|hover:/i);
  });

  it("is parted from the controls by a divider in the bar's border token", () => {
    expect(sig).toMatch(/class="[^"]*\bborder-l border-border\b[^"]*"/);
  });
});

describe("one comet in one place", () => {
  it("the sidebar and the phone drawer draw no comet", () => {
    for (const rel of ["components/Sidebar.tsx", "components/AppShell.tsx", "components/AccountSwitcher.tsx"]) {
      expect(code(read(rel)), rel).not.toMatch(/MetoriteMark|MetoriteSignature|BarSignature|data-metorite/);
    }
  });

  it("no left lockup of ours: a customer's logo leaves the right end to us", () => {
    const logo = { dataUri: "data:image/png;base64,AAAA", mime: "image/png", width: 600, height: 160, byteSize: 9 };
    expect(ourMarkLeads({ logo, updatedBy: "", updatedAt: "" } as OrgBranding)).toBe(false);
  });

  it("our comet already leads with no logo, and before the answer comes", () => {
    expect(ourMarkLeads({ logo: null, updatedBy: "", updatedAt: "" })).toBe(true);
    expect(ourMarkLeads(null)).toBe(true);
    expect(ourMarkLeads(undefined)).toBe(true);
  });
});
