import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { pushView, replaceView } from "./history";

/**
 * The fence for `history.ts`: a CRM view change makes no server request.
 * The runtime half is `e2e/crm-rail-held-request.spec.ts`, which holds the
 * server's answer and clicks the rail.
 */

describe("pushView and replaceView", () => {
  it("push a history entry with the view's address and nothing else", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    pushView("/crm?tab=leads", history);
    expect(history.pushState).toHaveBeenCalledWith(null, "", "/crm?tab=leads");
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  it("replace the entry when the page corrects its own address", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    replaceView("/crm?tab=deals&view=v1", history);
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/crm?tab=deals&view=v1");
    expect(history.pushState).not.toHaveBeenCalled();
  });
});

describe("the CRM page changes its view without the router", () => {
  const src = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");

  // A `router.push` or `router.replace` waits on a React Server Components
  // request before the view changes. With no timeout, a held request left
  // the rail dead (crm_platform.md §18.6, owner report 2026-10-12).
  it("calls neither router.push nor router.replace", () => {
    expect(src).not.toMatch(/\brouter\.(push|replace)\s*\(/);
    expect(src).not.toMatch(/\buseRouter\s*\(/);
  });

  it("sends every view change through pushView", () => {
    expect(src).toMatch(/const go = useCallback\(\(next: CrmView\) => pushView\(viewHref\(next\)\)/);
  });
});
