/**
 * WS-47 WAC-1 — Chat on WhatsApp on My Profile.
 *
 * vitest runs in node here, with no DOM renderer. So the section's rules live
 * in `whatsappLink.ts` and run here, and a source scan pins that the
 * component and the page consume them rather than a copy.
 *
 * Fences: `wac-section-hidden-when-dark` and `wac-section-shows-link-and-qr`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { create } from "qrcode";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type IssuedCode,
  fetchLinkState,
  isIssuedCode,
  issueCode,
  qrDataUrl,
  sectionView,
} from "./whatsappLink";

const ORG = "11111111-1111-4111-8111-111111111111";
const ISSUED: IssuedCode = {
  code: "7KQ2M9XRVT",
  expires_at: "2026-10-09T12:15:00+00:00",
  link: "https://wa.me/919800000000?text=Link%20me%3A%207KQ2M9XRVT",
  display_number: "919800000000",
  code_ttl_minutes: 15,
};

function state(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    display_number: "919800000000",
    code_ttl_minutes: 15,
    links: [],
    ...overrides,
  };
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("wac-section-hidden-when-dark", () => {
  it("hides on a failed request", () => {
    expect(sectionView(null)).toEqual({ kind: "hidden" });
  });

  it.each([401, 403, 404, 500, 502, 503])("hides on a %i", (status) => {
    expect(sectionView({ status, body: state() })).toEqual({ kind: "hidden" });
  });

  it("hides when the gateway says the channel is not enabled", () => {
    expect(
      sectionView({ status: 200, body: state({ enabled: false }) })
    ).toEqual({ kind: "hidden" });
  });

  it("hides on a body with no enabled flag", () => {
    expect(sectionView({ status: 200, body: {} })).toEqual({ kind: "hidden" });
    expect(sectionView({ status: 200, body: null })).toEqual({ kind: "hidden" });
  });

  it("hides on the real 404 of a dark channel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        reply(404, {
          detail: "Chat on WhatsApp is not available for your organization.",
        })
      )
    );
    expect(sectionView(await fetchLinkState())).toEqual({ kind: "hidden" });
  });
});

describe("the ready section", () => {
  it("shows the member's live links and drops revoked ones", () => {
    const view = sectionView({
      status: 200,
      body: state({
        links: [
          {
            organization_id: ORG,
            organization_name: "Fracktal",
            status: "active",
            linked_at: "2026-10-09T10:00:00+00:00",
            is_current: true,
            expires_at: null,
            phone_hint: "0001",
          },
          {
            organization_id: ORG,
            organization_name: "Fracktal",
            status: "revoked",
            linked_at: null,
            is_current: false,
            expires_at: null,
            phone_hint: null,
          },
        ],
      }),
    });
    expect(view.kind).toBe("ready");
    if (view.kind !== "ready") return;
    expect(view.links).toHaveLength(1);
    expect(view.links[0]).toMatchObject({
      label: "Linked · current",
      tone: "success",
      detail: "phone ending 0001, for Fracktal",
    });
  });

  it("names a pending code as waiting, in the warning tone", () => {
    const view = sectionView({
      status: 200,
      body: state({
        links: [
          {
            organization_id: ORG,
            organization_name: "Fracktal",
            status: "pending",
            linked_at: null,
            is_current: false,
            expires_at: "2026-10-09T12:15:00+00:00",
            phone_hint: null,
          },
        ],
      }),
    });
    if (view.kind !== "ready") throw new Error("expected ready");
    expect(view.links[0].tone).toBe("warning");
    expect(view.links[0].label).toBe("Waiting for your message");
  });
});

describe("wac-section-shows-link-and-qr", () => {
  it("issues a code and returns the link the page shows", async () => {
    const fetchMock = vi.fn(async () => reply(201, ISSUED));
    vi.stubGlobal("fetch", fetchMock);

    const issued = await issueCode();

    expect(issued.link).toBe(ISSUED.link);
    expect(fetchMock).toHaveBeenCalledWith("/api/me/whatsapp-link/code", {
      method: "POST",
    });
  });

  it("draws a QR code of that same link, in the browser", async () => {
    const url = await qrDataUrl(ISSUED.link);
    expect(url.startsWith("data:image/png;base64,")).toBe(true);
    const qr = create(ISSUED.link, { errorCorrectionLevel: "M" });
    const encoded = qr.segments
      .map((s) =>
        typeof s.data === "string"
          ? s.data
          : new TextDecoder().decode(s.data as Uint8Array)
      )
      .join("");
    expect(encoded).toBe(ISSUED.link);
  });

  it("throws the gateway's sentence on a refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        reply(503, {
          detail: "Chat on WhatsApp is not set up on this deployment yet.",
        })
      )
    );
    await expect(issueCode()).rejects.toThrow(
      "Chat on WhatsApp is not set up on this deployment yet."
    );
  });

  it("refuses an answer whose link is not a wa.me link", () => {
    expect(isIssuedCode(ISSUED)).toBe(true);
    expect(isIssuedCode({ ...ISSUED, link: "https://evil.test/x" })).toBe(false);
    expect(isIssuedCode({ code: "X" })).toBe(false);
    expect(isIssuedCode(null)).toBe(false);
  });
});

describe("the surfaces consume these rules", () => {
  const root = join(__dirname, "..", "..", "..");
  const read = (rel: string) => readFileSync(join(root, rel), "utf-8");

  it("the section draws nothing while hidden, and uses the primitives", () => {
    const src = read("app/people/components/WhatsAppLinkSection.tsx");
    expect(src).toMatch(/if \(view\.kind === "hidden"\) return null;/);
    expect(src).toContain("sectionView(await fetchLinkState())");
    expect(src).toContain("qrDataUrl(issued.link)");
    expect(src).toMatch(/<img\s+src=\{qr\}/);
    expect(src).toContain('from "@/components/ui/Button"');
    expect(src).toContain('from "@/components/ui/Badge"');
    expect(src).not.toContain("dangerouslySetInnerHTML");
  });

  it("My Profile renders the section", () => {
    expect(read("app/people/me/page.tsx")).toContain("<WhatsAppLinkSection />");
  });

  it("the two hops forward no browser body and no query", () => {
    for (const rel of [
      "app/api/me/whatsapp-link/route.ts",
      "app/api/me/whatsapp-link/code/route.ts",
    ]) {
      const src = read(rel);
      expect(src).toContain("proxyToGateway(");
      expect(src).not.toMatch(/req\.(json|text|nextUrl|arrayBuffer)/);
      expect(src).toContain('export const dynamic = "force-dynamic"');
    }
  });
});
