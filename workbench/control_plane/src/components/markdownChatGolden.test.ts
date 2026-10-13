/**
 * The chat's Markdown did not change with WS-57 TR-5.
 *
 * TR-5 lets `taskRefs` switch the remark plugin on by itself, for the task
 * card. `markdownChatGolden.json` holds what `MarkdownBody` drew for each
 * input in each chat mode (and for a document) on the commit BEFORE that
 * change. This renders them again and compares the HTML, byte for byte. A
 * change to the chat's output fails here, and only a deliberate one should
 * write the file again.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import { buildEntityIndex } from "@/lib/entityIndex";
import { OWNER_ANSWER, OWNER_TOOL_EVENTS } from "@/lib/entityPills.fixture";
import { resolveTaskRefs } from "@/lib/taskRefs";

// The inputs and the modes the goldens were written from. They live in this
// test file: `#120` reads as a colour to the conformance scan, which skips
// test files.
const CHAT_GOLDEN_INPUTS: readonly string[] = [
  "See #12 and #9999, also «Board drag and drop» and `#12` in code.\n\n" +
    "Email a@b.io and [link #12](https://github.com/acme/x/pull/1). PR #120, #1 priority.",
  "# Heading\n\n- item #5\n- «x» **«Zed»** today.**Bold** `«c»`\n\n```\n#12 «y»\n```",
  "Link /projects?project=5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f and " +
    "[Ops](/projects?project=5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f) " +
    "https://github.com/acme/widgets/pull/12 (#12), «#5 «Five»» #5 «Five».",
];

/** The prop sets a chat surface, a document or the card passed before TR-5. */
const CHAT_GOLDEN_MODES: Record<
  string,
  { entityPills?: boolean; fences?: boolean; taskRefs?: boolean; inline?: boolean; caret?: boolean; plainEmails?: boolean }
> = {
  projectsChat: { entityPills: true, fences: true },
  projectsChatRefs: { entityPills: true, fences: true, taskRefs: true },
  projectsChatNoRefs: { entityPills: true, fences: true, taskRefs: false },
  otherChat: { fences: true },
  otherChatRefs: { fences: true, taskRefs: true },
  otherChatNoRefs: { fences: true, taskRefs: false },
  document: {},
  pillsOnly: { entityPills: true },
  inlineField: { inline: true, fences: true, taskRefs: true },
  streaming: { caret: true, fences: true, taskRefs: true },
  cardBeforeTr5: { plainEmails: true },
};

const GOLDEN: Record<string, string> = JSON.parse(
  readFileSync(fileURLToPath(new URL("./markdownChatGolden.json", import.meta.url)), "utf-8"),
);

describe("the chat's Markdown is the one it was before TR-5", () => {
  beforeAll(async () => {
    // The app's store, primed as it was when the goldens were written: #12
    // and #5 are tasks, and #9999, #120 and #1 are not.
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      json: async () => ({
        rows: [
          { id: "aaaaaaaa-0000-4000-8000-000000000012", task_number: 12, title: "Twelve", status_name: "To do", category: "todo" },
          { id: "aaaaaaaa-0000-4000-8000-000000000005", task_number: 5, title: "Five", status_name: "Done", category: "done" },
        ],
      }),
    }));
    await resolveTaskRefs([12, 5, 9999, 120, 1]);
  });

  const index = buildEntityIndex(OWNER_TOOL_EVENTS);
  const inputs = [OWNER_ANSWER, ...CHAT_GOLDEN_INPUTS];

  it("holds every input in every mode", () => {
    expect(Object.keys(GOLDEN)).toHaveLength(inputs.length * Object.keys(CHAT_GOLDEN_MODES).length);
  });

  inputs.forEach((content, i) => {
    for (const [name, mode] of Object.entries(CHAT_GOLDEN_MODES)) {
      it(`input ${i} in mode ${name}`, () => {
        const props = { content, ...mode, entityIndex: mode.entityPills ? index : undefined };
        expect(renderToStaticMarkup(createElement(MarkdownBody, props))).toBe(GOLDEN[`${i}:${name}`]);
      });
    }
  });
});
