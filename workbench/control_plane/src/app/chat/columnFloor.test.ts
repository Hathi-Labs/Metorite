/**
 * H-332 (task 757): the /chat message column keeps a floor. With the
 * conversations, files and documents panes open, the column fell to 128 px and
 * crushed every card. A source fence, because jsdom has no layout: the column
 * has a minimum width, and the row scrolls sideways before it squeezes the
 * column. Mutation: drop the `min-w-[min(100%,26rem)]` class -> red.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const src = readFileSync(fileURLToPath(new URL("./page.tsx", import.meta.url)), "utf8");

describe("the /chat message column", () => {
  it("has a minimum width of 26rem, or the row when that is narrower", () => {
    expect(src).toMatch(/data-chat-column=""[^>]*min-w-\[min\(100%,26rem\)\]/);
  });
  it("sits in a row that scrolls sideways instead of crushing it", () => {
    expect(src).toMatch(/data-chat-row=""/);
    expect(src).toMatch(/overflow-x-auto[^"]*"\s+data-chat-row/);
  });
});
