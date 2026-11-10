/**
 * The CRM writes short money in the member's number system (owner,
 * 2026-10-19). Spec: `whatsapp_assistant_channel.md` §13.10.
 *
 * vitest runs in the node environment, so the hook draws through
 * `renderToStaticMarkup`. `publishNumberSystem` stands in for the one read
 * of `GET /api/settings/number-system`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { publishNumberSystem, resetNumberSystemForTests } from "@/lib/numberSystem";

import { useCompactMoney } from "./useCompactMoney";

const CRM = join(__dirname, "..");

function Amount({ value }: { value: number }) {
  return createElement("span", null, useCompactMoney()(value));
}

const draw = (value: number) => renderToStaticMarkup(createElement(Amount, { value }));

afterEach(() => resetNumberSystemForTests());

describe("useCompactMoney", () => {
  it("an international member sees M", () => {
    publishNumberSystem("international");
    expect(draw(13_500_000)).toBe("<span>₹13.5M</span>");
    expect(draw(1_240_000)).toBe("<span>₹1.24M</span>");
  });

  it("an Indian member sees L and Cr", () => {
    publishNumberSystem("indian");
    expect(draw(13_500_000)).toBe("<span>₹1.35\u00a0Cr</span>");
  });

  it("before the one read answers, the default is Indian", () => {
    expect(draw(1_240_000)).toBe("<span>₹12.4\u00a0L</span>");
  });
});

describe("every CRM screen writes short money through the hook", () => {
  const files = ["components/KanbanBoard.tsx", "components/Reports.tsx", "page.tsx"];
  for (const rel of files) {
    it(rel, () => {
      const src = readFileSync(join(CRM, rel), "utf-8");
      expect(src).toMatch(/useCompactMoney\(\)/);
      expect(src).not.toMatch(/\bcompactMoney\(/);
    });
  }
});
